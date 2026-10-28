"""Verify that installing a reading revision left every previous table intact."""
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
from backend.semantic import semantic_status


def table_hash(db, name):
    digest = hashlib.sha256()
    rows = 0
    # Only identifiers discovered in the fixed local schema, never user input.
    escaped = name.replace('"', '""')
    keys = [row[0] for row in db.execute("SELECT name FROM pragma_table_info(?) WHERE pk>0 ORDER BY pk", (name,))]
    ordering = ",".join('"' + key.replace('"', '""') + '"' for key in keys) if keys else "rowid"
    for row in db.execute('SELECT * FROM "' + escaped + '" ORDER BY ' + ordering):
        digest.update(json.dumps(list(row), ensure_ascii=False, separators=(",", ":"), default=lambda value: {"blob_hex": value.hex()}).encode("utf-8"))
        digest.update(b"\n")
        rows += 1
    return {"rows": rows, "sha256": digest.hexdigest()}


def audit(database, baseline):
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as db, closing(sqlite3.connect(Path(baseline).resolve().as_uri() + "?mode=ro", uri=True)) as old:
        db.row_factory = old.row_factory = sqlite3.Row
        names = [row[0] for row in old.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='pilot_reading_overlays' ORDER BY name")]
        checks = {}
        for name in names:
            before, after = table_hash(old, name), table_hash(db, name)
            checks[name] = {"before": before, "after": after, "unchanged": before == after}
        views = []
        for biography_id in range(1, 6):
            view = presentation(database, biography_id)
            assert view.get("presentation_revision") and view["reading_view"]["coverage"]["complete"]
            views.append({"biography_id": biography_id, "presentation_revision": view["presentation_revision"],
                          "paragraph_groups": len(view["paragraph_groups"]), "criticism": len(view["criticism"]),
                          "coverage": view["reading_view"]["coverage"], "footnotes": len(view["annotations"]["footnotes"])})
        source = (ROOT / "data/source.mARkdown").read_bytes()
        sha = hashlib.sha256(source).hexdigest()
        index = semantic_status(database)
        result = {"baseline": str(Path(baseline).resolve()), "previous_tables": checks, "source_sha256": sha,
                  "source_matches_database": db.execute("SELECT full_text FROM sources WHERE sha256=?", (sha,)).fetchone()[0].encode("utf-8") == source,
                  "biographies": views, "sixth_presentation": presentation(database, 6),
                  "database_integrity": db.execute("PRAGMA integrity_check").fetchone()[0],
                  "foreign_key_errors": [list(row) for row in db.execute("PRAGMA foreign_key_check")],
                  "semantic_fingerprint": index.get("fingerprint"),
                  "semantic_index_unchanged": index.get("fingerprint") == "100350c97cfdeb65d910de55031cabc2a42de58f64476d68ff42d7a8ce837223",
                  "reference_hashes": {name: hashlib.sha256((ROOT / "reference" / name).read_bytes()).hexdigest() for name in ("topic_dictionary.csv", "legacy_indexer.html")}}
        result["passed"] = all(check["unchanged"] for check in checks.values()) and result["source_matches_database"] and result["database_integrity"] == "ok" and not result["foreign_key_errors"] and result["semantic_index_unchanged"] and result["sixth_presentation"]["available"] is False
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=ROOT / "data/library.sqlite3")
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "docs/READING_V2_INTEGRITY.json")
    args = parser.parse_args()
    report = audit(args.db, args.baseline)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"passed": report["passed"], "unchanged_tables": len(report["previous_tables"]), "mapped_chars": sum(view["coverage"]["accounted_chars"] for view in report["biographies"]), "output": str(args.output)}, ensure_ascii=False))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
