"""Measure known-passage retrieval, without changing source or review records.

The 30 questions are a small, curated regression set, not an exhaustive relevance
judgment of every possible result. Hit@10 means that a result overlaps one known
relevant, source-verified passage. It is not precision, historical authentication,
or proof that an unreturned subject is absent from the book.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import math
import os
import platform
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.normalization import fts_query


def percentile(values, fraction=0.95):
    """Linear interpolation, explicitly also defined for one observation."""
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * fraction
    lower = math.floor(index)
    upper = math.ceil(index)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def overlap_chars(result, expected):
    if result.get("source_id") != expected["source_id"]:
        return 0
    if result.get("section_id") is not None and result["section_id"] != expected["section_id"]:
        return 0
    return max(0, min(result["source_char_end"], expected["source_char_end"]) - max(result["source_char_start"], expected["source_char_start"]))


def first_known_hit(items, expected, top_k=10):
    for rank, item in enumerate(items[:top_k], 1):
        for passage in expected:
            required = min(40, math.ceil((passage["source_char_end"] - passage["source_char_start"]) * 0.25))
            overlap = overlap_chars(item, passage)
            if overlap >= required:
                return {"rank": rank, "overlap_chars": overlap, "required_overlap_chars": required,
                        "expected_start": passage["source_char_start"], "result_start": item["source_char_start"],
                        "result_end": item["source_char_end"]}
    return None


def load_verified_gold(path, database):
    rows = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Duplicate evaluation query IDs")
    sources = {row["sha256"]: row for row in database.execute("SELECT id,sha256,full_text FROM sources")}
    for query in rows:
        source = sources.get(query["source_sha256"])
        if source is None:
            raise ValueError("Gold source SHA is not present in the database: " + query["id"])
        for passage in query["expected"]:
            if passage["source_id"] != source["id"]:
                raise ValueError("Gold source identity differs from the imported source: " + query["id"])
            a, b = passage["source_char_start"], passage["source_char_end"]
            if not 0 <= a < b <= len(source["full_text"]) or source["full_text"][a:b] != passage["evidence_text"]:
                raise ValueError("Gold evidence is not an exact source slice: " + query["id"])
            section = database.execute(
                "SELECT source_id,source_char_start,source_char_end FROM source_sections WHERE id=?",
                (passage["section_id"],)).fetchone()
            if (section is None or section["source_id"] != passage["source_id"]
                    or not section["source_char_start"] <= a < b <= section["source_char_end"]):
                raise ValueError("Gold section identity or bounds differ from the imported source: " + query["id"])
            biography_id = passage.get("biography_id")
            if biography_id is not None:
                biography = database.execute(
                    "SELECT source_id,section_id,source_char_start,source_char_end FROM biographies WHERE id=?",
                    (biography_id,)).fetchone()
                if (biography is None or biography["source_id"] != passage["source_id"]
                        or biography["section_id"] != passage["section_id"]
                        or not biography["source_char_start"] <= a < b <= biography["source_char_end"]):
                    raise ValueError("Gold biography identity or bounds differ from the imported source: " + query["id"])
            elif database.execute("SELECT 1 FROM biographies WHERE section_id=? LIMIT 1", (passage["section_id"],)).fetchone():
                raise ValueError("Gold biography identity is missing for a biography section: " + query["id"])
    return rows


def lexical_baseline(database, query, top_k=10):
    expression = fts_query(query)
    if not expression:
        return []
    return [dict(row) for row in database.execute(
        "SELECT s.id,s.source_id,s.section_id,s.biography_id,s.source_char_start,s.source_char_end "
        "FROM segments_fts f JOIN segments s ON s.id=f.segment_id "
        "WHERE segments_fts MATCH ? ORDER BY bm25(segments_fts) LIMIT ?", (expression, top_k))]


def aggregate(rows, key, top_k):
    eligible = [row for row in rows if row.get(key) is not None]
    hits = sum(row[key]["known_hit"] is not None for row in eligible)
    return {"queries": len(eligible), "known_hits": hits,
            f"known_passage_hit_at_{top_k}": hits / len(eligible) if eligible else None,
            "p95_ms": percentile([row[key]["elapsed_ms"] for row in eligible]),
            "mean_ms": sum(row[key]["elapsed_ms"] for row in eligible) / len(eligible) if eligible else None}


def evaluate(db_path, gold_path, *, semantic_dir=None, top_k=10, baseline_only=False, warmups=1):
    connection = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        gold = load_verified_gold(gold_path, connection)
        review_before = connection.execute("SELECT COUNT(*) FROM review_log").fetchone()[0]
        status = None
        search = None
        if not baseline_only:
            from backend.semantic import THREADS, semantic_status
            from backend.smart_search import smart_search
            search = smart_search
            status = semantic_status(db_path, semantic_dir)
        warmup_ms = []
        if search:
            for index in range(warmups):
                started = time.perf_counter()
                # A separate query avoids warming the result cache for a gold case.
                search({"q": f"تهيئة محلية للبحث في النص {index}", "plan": {"meaning": "تهيئة محلية للبحث في النص", "include_terms": [], "exclude_terms": [], "topic_ids": []}, "page": 1, "page_size": top_k}, db_path, semantic_dir)
                warmup_ms.append((time.perf_counter() - started) * 1000)
        results = []
        for case in gold:
            started = time.perf_counter()
            old = lexical_baseline(connection, case["query"], top_k)
            baseline_ms = (time.perf_counter() - started) * 1000
            row = {"id": case["id"], "category": case["category"], "query": case["query"],
                   "baseline": {"elapsed_ms": baseline_ms, "returned": len(old), "known_hit": first_known_hit(old, case["expected"], top_k)}, "smart": None}
            if search:
                started = time.perf_counter()
                # This is an explicit, fixed user meaning: the original natural
                # language question with no benchmark-specific expansion/weights.
                response = search({"q": case["query"], "plan": {"meaning": case["query"], "include_terms": [], "exclude_terms": [], "topic_ids": []}, "page": 1, "page_size": top_k}, db_path, semantic_dir)
                elapsed = (time.perf_counter() - started) * 1000
                row["smart"] = {"elapsed_ms": elapsed, "returned": len(response["items"]),
                                "known_hit": first_known_hit(response["items"], case["expected"], top_k),
                                "mode": response.get("mode"), "cache_hit": response.get("cache_hit", False),
                                "ranking": response.get("ranking"), "warning": response.get("warning")}
            results.append(row)
            print(json.dumps({"query": case["id"], "baseline_hit": bool(row["baseline"]["known_hit"]),
                              "smart_hit": bool(row["smart"]["known_hit"]) if row["smart"] else None,
                              "mode": row["smart"]["mode"] if row["smart"] else None,
                              "elapsed_ms": row["smart"]["elapsed_ms"] if row["smart"] else baseline_ms}, ensure_ascii=False), file=sys.stderr, flush=True)
        review_after = connection.execute("SELECT COUNT(*) FROM review_log").fetchone()[0]
        if review_before != review_after:
            raise RuntimeError("Review log changed while evaluating; investigate concurrent writes")
        by_category = {}
        for category in sorted({row["category"] for row in results}):
            subset = [row for row in results if row["category"] == category]
            by_category[category] = {"baseline": aggregate(subset, "baseline", top_k), "smart": aggregate(subset, "smart", top_k)}
        hybrid_only = [row for row in results if row["smart"] and row["smart"]["mode"] == "hybrid"]
        descriptive = [row for row in results if row["category"] in ("paraphrase", "outside_dictionary")]
        descriptive_metrics = aggregate(descriptive, "smart", top_k)
        descriptive_metric = descriptive_metrics[f"known_passage_hit_at_{top_k}"]
        all_hybrid = len(hybrid_only) == len(results) and bool(results)
        smart_metrics = aggregate(results, "smart", top_k)
        return {"evaluation_version": "known-passages-v1", "gold_sha256": hashlib.sha256(Path(gold_path).read_bytes()).hexdigest(),
                "execution_environment": {"method": "in-process synchronous smart_search worker function; excludes HTTP/browser transport", "platform": platform.platform(), "processor": platform.processor(), "python": platform.python_version(), "logical_cpu_count": os.cpu_count(), "inference_cpu_threads": THREADS},
                "source_sha256": sorted({case["source_sha256"] for case in gold}), "query_count": len(gold),
                "category_counts": dict(Counter(case["category"] for case in gold)), "top_k": top_k,
                "metric_definition": "A result must share source/section and overlap at least min(40,ceil(25% of gold span length)) literal Unicode characters with a known relevant passage. These are curated known-passage hits, not precision/exhaustive recall or historical authentication.",
                "selection_protocol": "Explicit original question as meaning; no gold-specific term expansion; first results page; one uncached run per distinct question after separate model warm-up.",
                "baseline": aggregate(results, "baseline", top_k), "smart": smart_metrics,
                "by_category": by_category, "descriptive_questions": descriptive_metrics, "semantic_status": status, "all_queries_used_hybrid": all_hybrid,
                "warmup_ms": warmup_ms, "cache_hits": sum(bool(row["smart"] and row["smart"]["cache_hit"]) for row in results),
                "goals": {"descriptive_hit_at_10_at_least_80_percent": all_hybrid and top_k == 10 and descriptive_metric is not None and descriptive_metric >= 0.8,
                          "warm_p95_at_most_8000_ms": all_hybrid and smart_metrics["p95_ms"] is not None and smart_metrics["p95_ms"] <= 8000,
                          "not_measured_as_hybrid_if_fallback": not all_hybrid},
                "review_log_before": review_before, "review_log_after": review_after, "queries": results}
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "library.sqlite3")
    parser.add_argument("--gold", type=Path, default=ROOT / "fixtures" / "semantic_evaluation.jsonl")
    parser.add_argument("--semantic-dir", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline-only", action="store_true")
    parser.add_argument("--require-hybrid", action="store_true")
    parser.add_argument("--warmups", type=int, default=1)
    args = parser.parse_args()
    if args.warmups < 0:
        parser.error("--warmups must be nonnegative")
    report = evaluate(args.db, args.gold, semantic_dir=args.semantic_dir, baseline_only=args.baseline_only, warmups=args.warmups)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if args.require_hybrid and not report["all_queries_used_hybrid"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
