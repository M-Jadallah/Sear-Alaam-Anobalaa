"""Measure frozen batch witnesses with local retrieval, without tuning the gold."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import math
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.curation import _load, _digest, _readonly, _source, batch_revision
from backend.smart_search import smart_search, _CACHE


def known_witness_hit(item, gold, source_id, biography_id):
    """Same pre-existing pilot metric: 25% of the anchor or 40 code points."""
    overlap = max(0, min(item.get("source_char_end", 0), gold["end"]) - max(item.get("source_char_start", 0), gold["start"]))
    threshold = min(40, math.ceil((gold["end"] - gold["start"]) * .25))
    return item.get("source_id") == source_id and item.get("biography_id") == biography_id and overlap >= threshold


def percentile95(values):
    if not values:
        return None
    values = sorted(values)
    position = (len(values) - 1) * .95
    lo = math.floor(position)
    return values[lo] + (values[math.ceil(position)] - values[lo]) * (position - lo)


def evaluate(batch, database, source_path, *, scope="both"):
    directory, manifest, bundles = _load(batch)
    revision = batch_revision(manifest, bundles)
    proof = json.loads((directory/"preview.json").read_text(encoding="utf-8"))
    gold_hash = _digest({str(identifier): bundle["gold"] for identifier, bundle in bundles.items()})
    if proof.get("revision") != revision or proof.get("gold_frozen_sha256") != gold_hash:
        raise ValueError("Questions or authoring changed after preview; review and freeze a fresh preview before evaluation")
    with closing(_readonly(database)) as db:
        source = _source(db, source_path)
        if source["sha256"] != manifest["source_sha256"]:
            raise ValueError("The evaluation source differs from the batch")
        source_id, literal = source["id"], source["full_text"]
        if not db.execute("SELECT 1 FROM curation_batches WHERE id=? AND revision=?", (manifest["batch_id"], revision)).fetchone():
            raise ValueError("Evaluate the current isolated preview or the matching published revision")
    questions, unavailable = [], []
    for identifier, bundle in bundles.items():
        gold = bundle["gold"]
        rows = gold.get("items", []) if isinstance(gold, dict) else gold
        if not rows:
            unavailable.append({"biography_id": identifier, "reason": gold.get("unavailable_reason", "")})
        questions.extend({**row, "biography_id": identifier} for row in rows)
    report = {"batch_id": manifest["batch_id"], "revision": revision, "gold_frozen_sha256": gold_hash,
              "database": str(Path(database).resolve()), "source_sha256": manifest["source_sha256"],
              "metric": "Top 10, same source and biography; overlap >= min(40, ceil(25% of anchor length))",
              "development_gold_not_human_adjudicated": True, "unavailable": unavailable, "suites": []}
    if not questions:
        report["status"] = "no_questions_with_documented_reason"
        return report
    # Loading time is disclosed separately. This query is not one of the gold
    # questions, and the result cache is cleared before every measured request.
    _CACHE.clear()
    warm_started = time.perf_counter()
    warmup = smart_search({"q": "مواقف من تراجم الأعلام وأخبارهم", "page_size": 10}, database)
    report["warmup"] = {"wall_ms": round((time.perf_counter()-warm_started)*1000, 3), "mode": warmup.get("mode"), "warning": warmup.get("warning")}
    for mode in (["global", "batch"] if scope == "both" else [scope]):
        results = []
        for gold in questions:
            _CACHE.clear()
            request = {"q": gold["question"], "plan": {"meaning": gold["question"], "topic_ids": [], "include_terms": [], "exclude_terms": [], "expansion_terms": []}, "page_size": 10}
            if mode == "batch":
                request["plan"]["batch_id"] = manifest["batch_id"]
            started = time.perf_counter()
            result = smart_search(request, database)
            elapsed = round((time.perf_counter()-started)*1000, 3)
            items = result["items"][:10]
            for item in items:
                if item["source_id"] == source_id and item["text_original"] != literal[item["source_char_start"]:item["source_char_end"]]:
                    raise ValueError("A returned witness is not a literal slice of the bound source")
            rank = next((index for index,item in enumerate(items,1) if known_witness_hit(item,gold,source_id,gold["biography_id"])), None)
            results.append({"question": gold["question"], "biography_id": gold["biography_id"], "anchor_start": gold["start"], "anchor_end": gold["end"],
                            "first_hit_rank": rank, "hit_at_10": rank is not None, "mode": result.get("mode"), "cache_hit": result.get("cache_hit"),
                            "wall_ms": elapsed, "warning": result.get("warning"),
                            "returned_ranges": [{key:item.get(key) for key in ("source_id","biography_id","source_char_start","source_char_end","match_type")} for item in items]})
        hits = sum(result["hit_at_10"] for result in results)
        report["suites"].append({"scope": mode, "questions": len(results), "hits_at_10": hits,
                                 "hit_rate_percent": round(hits / len(results) * 100, 3),
                                 "p95_wall_ms": percentile95([result["wall_ms"] for result in results]),
                                 "mean_wall_ms": sum(result["wall_ms"] for result in results)/len(results), "results": results})
    report["status"] = "measured"
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=ROOT/"data/source.mARkdown")
    parser.add_argument("--scope", choices=("global", "batch", "both"), default="both")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = evaluate(args.batch, args.db, args.source, scope=args.scope)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"status":report["status"], "batch_id":report["batch_id"], "suites":[{key:value for key,value in suite.items() if key!="results"} for suite in report["suites"]], "output":str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
