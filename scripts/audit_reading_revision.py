"""Read-only conservation and exact history audit for published reading updates."""
from contextlib import closing
import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.audit_curation import readonly, has_table, rows_by_key
from scripts.audit_reading import table_hash
from backend.pilot import presentation

READING_TABLES = ("pilot_reading_overlays", "curation_diacritics")
HISTORY_TABLES = ("reading_revision_batches", "reading_revision_history")


def audit(database, baseline, biography_ids, source_path=None):
    targets = set(biography_ids)
    if not targets or any(type(i) is not int or i <= 0 for i in targets):
        raise ValueError("Specify actual biography IDs")
    with closing(readonly(database)) as db, closing(readonly(baseline)) as before:
        names = [r[0] for r in before.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        protected = {}
        for table in names:
            if table in READING_TABLES or table in HISTORY_TABLES:
                continue
            a, b = table_hash(before, table), table_hash(db, table)
            protected[table] = {"before": a, "after": b, "unchanged": a == b}
        histories_preserved = {}
        for table in HISTORY_TABLES:
            old, current = rows_by_key(before, table), rows_by_key(db, table)
            histories_preserved[table] = all(current.get(k) == row for k, row in old.items())
        old_history = rows_by_key(before, "reading_revision_history")
        new_history = [dict(row) for row in db.execute("SELECT * FROM reading_revision_history ORDER BY id") if (row["id"],) not in old_history] if has_table(db, "reading_revision_history") else []
        outside = [h["biography_id"] for h in new_history if h["biography_id"] not in targets]
        scoped = {}
        for table in READING_TABLES:
            old, current = rows_by_key(before, table), rows_by_key(db, table)
            changed_outside = [list(key) for key in set(old)|set(current) if old.get(key) != current.get(key) and key[0] not in targets]
            scoped[table] = {"previous_rows": len(old), "current_rows": len(current), "outside_target_changes": changed_outside, "in_scope": not changed_outside}
        views, history_checks = [], []
        for identifier in sorted(targets):
            rows = [h for h in new_history if h["biography_id"] == identifier]
            good = len(rows) == 1
            if good:
                h = rows[0]
                for table, prefix in (("pilot_reading_overlays", "reading"), ("curation_diacritics", "diacritics")):
                    old_row = before.execute('SELECT * FROM "'+table+'" WHERE biography_id=?', (identifier,)).fetchone() if has_table(before, table) else None
                    now_row = db.execute('SELECT * FROM "'+table+'" WHERE biography_id=?', (identifier,)).fetchone()
                    prior = json.loads(h["previous_"+prefix+"_json"]) if h["previous_"+prefix+"_json"] is not None else None
                    good = good and prior == (dict(old_row) if old_row else None)
                    good = good and now_row is not None and now_row["revision"] == h["revision"] and json.loads(now_row["payload"]) == json.loads(h["new_"+prefix+"_json"])
                good = good and bool(h["curator"].strip()) and bool(h["reviewer"].strip()) and h["curator"] != h["reviewer"] and bool(h["reason"].strip())
            history_checks.append({"biography_id": identifier, "new_history_rows": len(rows), "exact_previous_rows_and_current_payloads": bool(good)})
            p = presentation(database, identifier)
            views.append({"biography_id": identifier, "available": p.get("available", False), "revision": p.get("presentation_revision"),
                          "source_sha256": p.get("source_sha256"), "source_coverage": p.get("reading_view", {}).get("coverage"),
                          "diacritics_status": p.get("diacritics", {}).get("status"), "word_coverage": p.get("diacritics", {}).get("coverage"),
                          "paragraph_groups": len(p.get("paragraph_groups", [])), "criticism": len(p.get("criticism", []))})
        source_ok, source_sha = True, None
        if source_path:
            raw = Path(source_path).read_bytes(); source_sha = hashlib.sha256(raw).hexdigest()
            row = db.execute("SELECT full_text FROM sources WHERE sha256=?", (source_sha,)).fetchone()
            source_ok = row is not None and row[0].encode("utf-8") == raw
        result = {"database": str(Path(database).resolve()), "baseline": str(Path(baseline).resolve()), "biography_ids": sorted(targets),
                  "protected_tables": protected, "prior_history_preserved": histories_preserved, "new_history_outside_scope": outside,
                  "reading_scope": scoped, "history_checks": history_checks, "presentations": views,
                  "source_sha256": source_sha, "source_matches_database": source_ok,
                  "database_integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
                  "foreign_key_errors": [list(row) for row in db.execute("PRAGMA foreign_key_check")]}
        result["passed"] = all(c["unchanged"] for c in protected.values()) and all(histories_preserved.values()) and not outside and all(c["in_scope"] for c in scoped.values()) and all(c["exact_previous_rows_and_current_payloads"] for c in history_checks) and all(p["available"] and p["source_coverage"]["complete"] and p["diacritics_status"] in ("ready", "partial") for p in views) and source_ok and result["database_integrity"] == "ok" and not result["foreign_key_errors"]
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--biography-ids", type=int, nargs="+", required=True)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.db, args.baseline, args.biography_ids, args.source)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "protected_tables": len(report["protected_tables"]), "biographies": len(report["presentations"]), "output": str(args.output)}))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
