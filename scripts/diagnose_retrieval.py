"""Read-only stage recall diagnostics; gold is never imported by application code."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend import smart_search as search
from backend.semantic import DEFAULT_DB, semantic_status
from backend.pilot import topic_candidates
from scripts.evaluate_semantic import first_known_hit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    status = semantic_status(DEFAULT_DB)
    if not status["ready"]:
        raise RuntimeError(status)
    rows = []
    for case in (json.loads(line) for line in (ROOT / "fixtures/semantic_evaluation.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()):
        query = case["query"]
        plan = {"meaning": query, "include_terms": [], "exclude_terms": [], "topic_ids": []}
        lexical_query = search._lexical_query(query, plan)
        lexical = search._lexical_candidates(DEFAULT_DB, lexical_query, status)
        semantic = search._semantic_candidates(query, status)
        topical = topic_candidates(DEFAULT_DB, lexical_query, plan)
        fused = search.deduplicate_overlaps(search.rrf_fuse(lexical, semantic, topical=topical))
        pool = fused[:search.RERANK_CANDIDATES]
        ranked = search._rerank(query, pool, status["model_fingerprint"])
        stages = {"lexical": lexical, "semantic": semantic, "fused": fused, "pool": pool, "top10": ranked[:10]}
        row = {"id": case["id"], "category": case["category"], "query": query,
               "stages": {name: first_known_hit(items, case["expected"], top_k=len(items)) for name, items in stages.items()}}
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
