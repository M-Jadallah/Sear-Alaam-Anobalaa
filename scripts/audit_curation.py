"""Read-only conservation audit for additive, source-bound curation batches."""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.pilot import presentation
from scripts.audit_reading import table_hash

CANONICAL = ("sources", "page_markers", "people", "source_sections", "biographies", "person_aliases",
             "segments", "person_mentions", "mention_candidates", "topic_hits", "review_log")
PREVIOUS = ("pilot_documents", "pilot_coverage", "pilot_evidence", "pilot_events", "pilot_topic_aliases",
            "pilot_reading_overlays", "topics", "curation_batches", "curation_batch_members", "curation_diacritics")
SCOPED = ("pilot_documents", "pilot_coverage", "pilot_evidence", "pilot_reading_overlays", "curation_diacritics")


def readonly(path):
    db = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def has_table(db, name):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def rows_by_key(db, table):
    if not has_table(db, table):
        return {}
    keys = [row[0] for row in db.execute("SELECT name FROM pragma_table_info(?) WHERE pk>0 ORDER BY pk", (table,))]
    if not keys:
        raise ValueError("Expected a primary key in " + table)
    # Table names are constants above; keys come from the local SQLite schema.
    return {tuple(row[key] for key in keys): tuple(row) for row in db.execute('SELECT * FROM "' + table + '"')}


def audit(database, baseline, biography_ids, source_path=None):
    targets = set(biography_ids)
    if not targets or any(type(value) is not int or value <= 0 for value in targets):
        raise ValueError("Specify the actual internal biography IDs from the exported manifest")
    with closing(readonly(database)) as db, closing(readonly(baseline)) as before:
        canonical = {}
        for table in CANONICAL:
            a, b = table_hash(before, table), table_hash(db, table)
            canonical[table] = {"before": a, "after": b, "unchanged": a == b}
        previous, scoped = {}, {}
        for table in PREVIOUS:
            old, current = rows_by_key(before, table), rows_by_key(db, table)
            changed = [list(key) for key, row in old.items() if current.get(key) != row]
            previous[table] = {"before_rows": len(old), "after_rows": len(current), "changed_previous_keys": changed,
                               "preserved": not changed}
        for table in SCOPED:
            old = rows_by_key(before, table)
            rows = db.execute('SELECT * FROM "' + table + '"').fetchall() if has_table(db, table) else []
            keys = [r[0] for r in db.execute("SELECT name FROM pragma_table_info(?) WHERE pk>0 ORDER BY pk", (table,))]
            new = [r for r in rows if tuple(r[key] for key in keys) not in old]
            outside = [r["biography_id"] for r in new if r["biography_id"] not in targets]
            scoped[table] = {"new_rows": len(new), "outside_target_biographies": outside, "in_scope": not outside}
        new_topics = set(rows_by_key(db, "topics")) - set(rows_by_key(before, "topics"))
        new_topics_proposed = all(row["dictionary_status"] == "proposed_from_text" for row in db.execute("SELECT * FROM topics") if (row["id"],) in new_topics)
        views = []
        for biography_id in sorted(targets):
            view = presentation(database, biography_id)
            has_vocalization = has_table(db, "curation_diacritics") and db.execute("SELECT 1 FROM curation_diacritics WHERE biography_id=?", (biography_id,)).fetchone() is not None
            views.append({"biography_id": biography_id, "available": view.get("available", False),
                          "source_sha256": view.get("source_sha256"), "reading_revision": view.get("presentation_revision"),
                          "coverage": view.get("reading_view", {}).get("coverage", {}),
                          "footnotes": len(view.get("annotations", {}).get("footnotes", [])),
                          "diacritics": view.get("diacritics"),
                          "vocalization_valid": not has_vocalization or view.get("diacritics", {}).get("status") in ("ready", "partial")})
        source_unchanged = True
        sha = None
        if source_path:
            raw = Path(source_path).read_bytes()
            sha = hashlib.sha256(raw).hexdigest()
            row = db.execute("SELECT full_text FROM sources WHERE sha256=?", (sha,)).fetchone()
            source_unchanged = row is not None and row[0].encode("utf-8") == raw
        integrity = db.execute("PRAGMA integrity_check").fetchone()[0]
        foreign = [list(row) for row in db.execute("PRAGMA foreign_key_check")]
        result = {"database": str(Path(database).resolve()), "baseline": str(Path(baseline).resolve()),
                  "biography_ids": sorted(targets), "source_sha256": sha, "source_matches_database": source_unchanged,
                  "canonical_tables": canonical, "previous_rows": previous, "new_rows_scope": scoped,
                  "new_topics_all_proposed": new_topics_proposed, "presentations": views,
                  "database_integrity": integrity, "foreign_key_errors": foreign}
        result["passed"] = all(check["unchanged"] for check in canonical.values()) and all(check["preserved"] for check in previous.values()) and all(check["in_scope"] for check in scoped.values()) and new_topics_proposed and source_unchanged and integrity == "ok" and not foreign and all(view["available"] and view["reading_revision"] and view["coverage"].get("complete") and view["vocalization_valid"] for view in views)
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--biography-ids", type=int, nargs="+", required=True, help="Resolved internal IDs from the batch manifest")
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.db, args.baseline, args.biography_ids, args.source)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": result["passed"], "biography_ids": result["biography_ids"], "output": str(args.output)}, ensure_ascii=False))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
