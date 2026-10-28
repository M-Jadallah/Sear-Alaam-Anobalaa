"""Freeze authored pilot gold and measure 15 pilot + 30 legacy questions.

Both suites use the unchanged evaluator and the default global search plan.
The frozen gold is never rewritten after retrieval results are observed.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys

for variable in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[variable] = "4"

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.evaluate_semantic import evaluate, load_verified_gold

DB_PATH = ROOT / "data/library.sqlite3"
GOLD_PATH = ROOT / "fixtures/pilot_evaluation.jsonl"


def fingerprint(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def state():
    with sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True) as db:
        return {
            "review_log": db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0],
            "pilot_events": db.execute("SELECT COUNT(*) FROM pilot_events").fetchone()[0],
            "human_reviewed_evidence": db.execute("SELECT COUNT(*) FROM pilot_evidence WHERE human_status='reviewed'").fetchone()[0],
            "pilot_revisions": [row[0] for row in db.execute("SELECT DISTINCT revision FROM pilot_documents ORDER BY revision")],
            "pilot_agent_supported": db.execute("SELECT COUNT(*) FROM pilot_evidence WHERE agent_decision='supported'").fetchone()[0],
            "pilot_evidence": db.execute("SELECT COUNT(*) FROM pilot_evidence").fetchone()[0],
            "code_sha256": {name: fingerprint(ROOT / "backend" / name) for name in ("smart_search.py", "smart_interpret.py", "pilot.py")},
            "curation_sha256": {str(bid): fingerprint(ROOT / "reference/pilot" / f"biography-{bid}.json") for bid in range(1, 6)},
        }


def freeze_gold():
    db = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    try:
        cases = []
        for bid in range(1, 6):
            document = json.loads((ROOT / "reference/pilot" / f"biography-{bid}.json").read_text(encoding="utf-8"))
            biography = db.execute("SELECT b.*,s.full_text source_text,s.sha256 FROM biographies b JOIN sources s ON s.id=b.source_id WHERE b.id=?", (bid,)).fetchone()
            if biography["sha256"] != document["source_sha256"]:
                raise ValueError("Pilot gold does not match the canonical source")
            for gold in document["gold"]:
                start, end = gold["start"], gold["end"]
                if not biography["source_char_start"] <= start < end <= biography["source_char_end"]:
                    raise ValueError("Pilot gold crosses biography boundaries")
                marker = db.execute("SELECT raw_marker FROM page_markers WHERE source_id=? AND source_char_offset>=? ORDER BY source_char_offset LIMIT 1", (biography["source_id"], start)).fetchone()
                cases.append({
                    "id": f"P{len(cases)+1:02d}", "category": "paraphrase", "query": gold["question"],
                    "source_sha256": document["source_sha256"],
                    "judgment_note": "سؤال تطوير للعينة وشاهده المحدد بالقراءة قبل اختبار الاسترجاع؛ لا يمثل حكمًا على جميع النتائج.",
                    "judgment_provenance": "Codex authored by literal source inspection before retrieval; no human-reviewed identity/topic or historical authentication is implied",
                    "expected": [{"source_id": biography["source_id"], "section_id": biography["section_id"], "biography_id": bid,
                                  "source_char_start": start, "source_char_end": end,
                                  "evidence_text": biography["source_text"][start:end], "heading": biography["heading"],
                                  "raw_page_marker": marker[0] if marker else None, "page_reference_status": "raw_unverified",
                                  "segment_ids": [row[0] for row in db.execute("SELECT id FROM segments WHERE biography_id=? AND source_char_start<? AND source_char_end>? ORDER BY segment_ordinal", (bid, end, start))]}],
                })
        if len(cases) != 15:
            raise ValueError("Exactly fifteen authored pilot questions are required")
        rendered = "".join(json.dumps(case, ensure_ascii=False) + "\n" for case in cases)
        if GOLD_PATH.exists():
            if GOLD_PATH.read_text(encoding="utf-8") != rendered:
                raise ValueError("Frozen pilot gold changed; do not overwrite after observing retrieval")
        else:
            GOLD_PATH.write_text(rendered, encoding="utf-8")
        load_verified_gold(GOLD_PATH, db)
        return fingerprint(GOLD_PATH)
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", choices=("all", "global", "scoped"), default="all")
    args = parser.parse_args()
    gold_sha = freeze_gold()
    frozen_state = state()
    print(json.dumps({"stage": "gold_frozen_before_queries", "gold_sha256": gold_sha, "state": frozen_state}, ensure_ascii=False), flush=True)
    import backend.smart_search as search_module
    previous = json.loads((ROOT / "docs/EVALUATION_FINAL.json").read_text(encoding="utf-8"))
    suites = [
        (GOLD_PATH, ROOT / "docs/PILOT_EVALUATION.json", "pilot_15_global"),
        (ROOT / "fixtures/semantic_evaluation.jsonl", ROOT / "docs/PILOT_REGRESSION_30.json", "legacy_30_global"),
        (GOLD_PATH, ROOT / "docs/PILOT_SCOPED_EVALUATION.json", "pilot_15_explicit_scope_secondary"),
    ]
    for gold, output, label in suites:
        scoped = label == "pilot_15_explicit_scope_secondary"
        if (args.suite == "global" and scoped) or (args.suite == "scoped" and not scoped):
            continue
        search_module._CACHE.clear()
        before = state()
        original_search = search_module.smart_search
        def explicitly_scoped_search(payload, db_path, semantic_dir=None):
            request = deepcopy(payload)
            request.setdefault("plan", {})["pilot_only"] = True
            return original_search(request, db_path, semantic_dir)
        try:
            # The secondary suite uses the public explicit-scope option only;
            # it changes no gold, terms, weights, labels, model, or core code.
            if scoped:
                search_module.smart_search = explicitly_scoped_search
            report = evaluate(DB_PATH, gold, warmups=1)
        finally:
            search_module.smart_search = original_search
        after = state()
        if before != after or fingerprint(GOLD_PATH) != gold_sha:
            raise RuntimeError("Pilot state, code, curation, or frozen gold changed during evaluation")
        report["pilot_state_before"] = before
        report["pilot_state_after"] = after
        measured = [row["smart"] for row in report["queries"] if row["smart"]]
        report["ranking_observations"] = {
            "reranked_counts": dict(Counter(row.get("ranking", {}).get("reranked", 0) for row in measured)),
            "topic_candidate_counts": dict(Counter(row.get("ranking", {}).get("topic_candidates", 0) for row in measured)),
            "warnings": dict(Counter(row["warning"] for row in measured if row.get("warning"))),
        }
        report["pilot_evaluation_protocol"] = {
            "suite": label,
            "search_scope": "explicit pilot_only=true; no selected topic IDs" if scoped else "global default; no pilot restriction or selected topic IDs",
            "goal_role": "Secondary explicit-scope usability measurement; does not replace the primary global quality goal" if scoped else "Primary global quality measurement",
            "cache": "result cache cleared before each suite; model and vector caches reused in one process",
            "gold_freeze_sha256": gold_sha,
            "limits": "Small development known-passage test, not an independent human-adjudicated benchmark, precision, exhaustive recall, or scientific authentication.",
        }
        if scoped:
            report["selection_protocol"] += " Explicit public pilot_only=true was applied to every request; this is a secondary scoped measurement."
        if label == "legacy_30_global":
            report["comparison_to_previous_final"] = {
                "previous_gold_sha256": previous["gold_sha256"],
                "same_gold": previous["gold_sha256"] == report["gold_sha256"],
                "previous_all": previous["smart"], "previous_descriptive": previous["descriptive_questions"],
                "known_hits_delta": report["smart"]["known_hits"] - previous["smart"]["known_hits"],
                "descriptive_known_hits_delta": report["descriptive_questions"]["known_hits"] - previous["descriptive_questions"]["known_hits"],
            }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"stage": "suite_complete", "suite": label, "output": str(output), "smart": report["smart"],
                          "descriptive": report["descriptive_questions"], "warmup_ms": report["warmup_ms"],
                          "all_hybrid": report["all_queries_used_hybrid"], "cache_hits": report["cache_hits"], "goals": report["goals"]}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
