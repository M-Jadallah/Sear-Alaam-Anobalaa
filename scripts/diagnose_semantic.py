"""Read-only retrieval-stage diagnostics against a frozen evaluation report.

Only the embedding model is loaded. The final top-ten outcome is copied from the
existing evaluation; the reranker is never loaded or invoked. This does not tune
queries, dictionary entries, weights, models, or the gold source passages.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend import smart_search as search
from backend.semantic import DEFAULT_DB, canonical_connection, configure_cpu, load_models, semantic_status
from scripts.evaluate_semantic import first_known_hit, load_verified_gold

STAGES = ("lexical100", "dense_context", "dense_paragraph", "dense100", "union200", "rrf_all", "dedup_all", "rrf40", "final10")


def known_hit(items, expected, limit=None):
    hit = first_known_hit(items, expected, len(items) if limit is None else limit)
    if hit is None:
        return None
    row = items[hit["rank"] - 1]
    return {**hit, "window_id": row["id"], "source_id": row["source_id"], "section_id": row["section_id"],
            "granularity": row.get("granularity", "context"), "token_count": row["token_count"],
            "retrieval_scores": row.get("scores")}


def loss_stage(row):
    if row["final10"]:
        return "known_hit_in_final10"
    if row["union200"] is None:
        return "absent_from_top100_retrieval_channels"
    if row["dedup_all"] is None:
        return "removed_by_overlap_deduplication"
    if row["rrf40"] is None:
        return "outside_rrf_top40_budget"
    return "outside_reranked_top10_budget"


def summary(rows):
    return {"queries": len(rows), "known_hits": {stage: sum(row[stage] is not None for row in rows) for stage in STAGES},
            "loss_stages": dict(Counter(row["loss_stage"] for row in rows))}


def diagnose(database, gold_path, evaluation_path, semantic_dir=None):
    configure_cpu()
    evaluation_bytes = Path(evaluation_path).read_bytes()
    evaluation = json.loads(evaluation_bytes)
    if evaluation.get("top_k") != 10 or not evaluation.get("all_queries_used_hybrid"):
        raise ValueError("This diagnosis requires a frozen hybrid top-ten evaluation")
    gold_sha = hashlib.sha256(Path(gold_path).read_bytes()).hexdigest()
    if evaluation["gold_sha256"] != gold_sha:
        raise ValueError("The frozen evaluation and unchanged gold file do not match")
    status = semantic_status(database, semantic_dir)
    evaluated_status = evaluation["semantic_status"]
    if not status.get("ready") or status.get("fingerprint") != evaluated_status.get("fingerprint"):
        raise ValueError("The active index differs from the frozen evaluation or is not ready")
    with canonical_connection(database) as db:
        gold = load_verified_gold(gold_path, db)
        before = db.execute("SELECT COALESCE(MAX(id),0),COUNT(*) FROM review_log").fetchone()
        before = tuple(before)
    if before[1] != evaluation["review_log_after"]:
        raise ValueError("The review state differs from the frozen evaluation")
    evaluated_rows = {row["id"]: row for row in evaluation["queries"]}
    if set(evaluated_rows) != {row["id"] for row in gold}:
        raise ValueError("Frozen evaluation question identities do not match the gold set")
    # The adapter caches this embedding-only pair for calls to the existing
    # retrieval pipeline. No reranker or new final ranking is computed.
    started = time.perf_counter()
    embedder, _ = load_models(with_reranker=False)
    load_ms = (time.perf_counter() - started) * 1000
    rows = []
    with patch.object(search, "_models", lambda fingerprint=None: (embedder, None)):
        search._vectors(status)
        if status.get("paragraph_status", {}).get("ready"):
            search._paragraph_vectors(status["paragraph_status"])
        for case in gold:
            frozen = evaluated_rows[case["id"]]
            if frozen["query"] != case["query"]:
                raise ValueError("Frozen evaluation query differs from gold: " + case["id"])
            started = time.perf_counter()
            query = case["query"]
            plan = {"meaning": query, "include_terms": [], "exclude_terms": [], "expansion_terms": [], "topic_ids": []}
            lexical = search._lexical_candidates(database, search._lexical_query(query, plan), status)
            dense = search._semantic_candidates(query, status)
            fused = search.rrf_fuse(lexical, dense)
            dedup = search.deduplicate_overlaps(fused)
            pool = dedup[:search.RERANK_CANDIDATES]
            context = [row for row in dense if row.get("granularity") == "context"]
            paragraphs = [row for row in dense if row.get("granularity") == "paragraph"]
            output = {"id": case["id"], "category": case["category"], "query": query,
                      "lexical100": known_hit(lexical, case["expected"]),
                      "dense_context": known_hit(context, case["expected"]),
                      "dense_paragraph": known_hit(paragraphs, case["expected"]),
                      "dense100": known_hit(dense, case["expected"]),
                      "union200": known_hit(lexical + dense, case["expected"]),
                      "rrf_all": known_hit(fused, case["expected"]),
                      "dedup_all": known_hit(dedup, case["expected"]),
                      "rrf40": known_hit(pool, case["expected"]),
                      "final10": frozen["smart"]["known_hit"],
                      "candidate_counts": {"lexical": len(lexical), "dense": len(dense), "dense_context": len(context),
                                           "dense_paragraph": len(paragraphs), "fused_distinct_spans": len(fused), "dedup": len(dedup), "rerank_pool": len(pool)},
                      "semantic_granularity": status.get("semantic_granularity"),
                      "diagnostic_elapsed_ms": (time.perf_counter() - started) * 1000}
            output["loss_stage"] = loss_stage(output)
            rows.append(output)
            print(json.dumps({"id": case["id"], "loss_stage": output["loss_stage"], "elapsed_ms": output["diagnostic_elapsed_ms"]}), file=sys.stderr, flush=True)
    with canonical_connection(database) as db:
        after = tuple(db.execute("SELECT COALESCE(MAX(id),0),COUNT(*) FROM review_log").fetchone())
    latest = semantic_status(database, semantic_dir)
    if latest.get("fingerprint") != status["fingerprint"] or before != after:
        raise RuntimeError("Source index/review state changed during the read-only diagnosis")
    if hashlib.sha256(Path(gold_path).read_bytes()).hexdigest() != gold_sha or Path(evaluation_path).read_bytes() != evaluation_bytes:
        raise RuntimeError("Gold or frozen evaluation changed during diagnosis")
    descriptive = [row for row in rows if row["category"] in ("paraphrase", "outside_dictionary")]
    return {"diagnostic_version": "retrieval-stages-v1", "gold_sha256": gold_sha,
            "frozen_evaluation_sha256": hashlib.sha256(evaluation_bytes).hexdigest(),
            "source_sha256": evaluation["source_sha256"], "index_fingerprint": status["fingerprint"],
            "semantic_granularity": status.get("semantic_granularity"), "dense_allocation": status.get("dense_allocation"),
            "cpu_threads": 4, "embedding_load_ms": load_ms, "review_state_before": before, "review_state_after": after,
            "protocol": "Unchanged original questions, empty explicit include/exclude/expansion/topic conditions. Recompute lexical, embedding and RRF stages only. Final10 copied from the frozen evaluation without loading or invoking its reranker. Known-passage overlap uses the same gold-verified metric; not exhaustive relevance or historical authentication.",
            "all_queries": summary(rows), "descriptive_queries": summary(descriptive),
            "by_category": {category: summary([row for row in rows if row["category"] == category]) for category in sorted({row["category"] for row in rows})},
            "queries": rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--gold", type=Path, default=ROOT / "fixtures" / "semantic_evaluation.jsonl")
    parser.add_argument("--evaluation", type=Path, default=ROOT / "data" / "semantic" / "evaluation-final.json")
    parser.add_argument("--semantic-dir", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "docs" / "SEMANTIC_STAGE_DIAGNOSTIC.json")
    args = parser.parse_args()
    report = diagnose(args.db, args.gold, args.evaluation, args.semantic_dir)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"all_queries": report["all_queries"], "descriptive_queries": report["descriptive_queries"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
