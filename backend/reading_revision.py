"""Audited revisions of published reading layers; canonical/review rows never change."""
from __future__ import annotations

import argparse
from contextlib import closing
from copy import deepcopy
from pathlib import Path
import json
import re
import sqlite3

from .curation import _digest, _read_json, _write_json, _readonly, _source, _screenshot_valid, QA_CHECKS
from .db import DEFAULT_DB, DEFAULT_SOURCE, ROOT, connect
from .diacritics import SCHEMA as DIACRITICS_SCHEMA, export_document, validate_document as validate_diacritics, validate_editorial_headings
from .pilot import backup_database
from .pilot_reading import SCHEMA as READING_SCHEMA, VERSION as READING_VERSION, effective_annotations, validate_reading_document, validate_paragraph_notes
from .reading import build_reading_view

VERSION = "reading-revision-v1"
WORK_DIR = ROOT / "reference" / "reading-revisions"
AUDIT_CHECKS = ("source_coverage", "paragraphs", "criticism_context", "negative_cases", "diacritics", "paragraph_context")
SCHEMA = """CREATE TABLE IF NOT EXISTS reading_revision_batches (
    id TEXT PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id),
    source_sha256 TEXT NOT NULL, start_ordinal INTEGER NOT NULL, end_ordinal INTEGER NOT NULL,
    revision TEXT NOT NULL, manifest_json TEXT NOT NULL,
    published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reading_revision_history (
    id INTEGER PRIMARY KEY, batch_id TEXT NOT NULL REFERENCES reading_revision_batches(id),
    biography_id INTEGER NOT NULL REFERENCES biographies(id), source_id INTEGER NOT NULL REFERENCES sources(id),
    source_sha256 TEXT NOT NULL, previous_reading_json TEXT, previous_diacritics_json TEXT,
    new_reading_json TEXT NOT NULL, new_diacritics_json TEXT NOT NULL,
    previous_reading_revision TEXT, previous_diacritics_revision TEXT, revision TEXT NOT NULL,
    curator TEXT NOT NULL, reviewer TEXT NOT NULL, reason TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(batch_id,biography_id)
)"""


def _table(db, name):
    return bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _row(db, table, identifier):
    if not _table(db, table):
        return None
    # The table names are the fixed internal constants at call sites below.
    row = db.execute('SELECT * FROM "'+table+'" WHERE biography_id=?', (identifier,)).fetchone()
    return dict(row) if row is not None else None


def _snapshot(db, bio, sha):
    pilot = _row(db, "pilot_documents", bio["id"])
    if pilot is None or pilot["source_sha256"] != sha:
        raise ValueError("يلزم وجود ترجمة منشورة بطبقة مصدر مطابقة قبل إصدار تصحيح القراءة")
    return {"biography_id": bio["id"], "source_ordinal": bio["source_ordinal"], "heading": bio["heading"],
            "source_sha256": sha, "source_char_start": bio["source_char_start"], "source_char_end": bio["source_char_end"],
            "full_text": bio["full_text"], "pilot": json.loads(pilot["payload"]), "pilot_row": pilot,
            "reading_row": _row(db, "pilot_reading_overlays", bio["id"]),
            "diacritics_row": _row(db, "curation_diacritics", bio["id"])}


def export_batch(start, end, *, db_path=DEFAULT_DB, source_path=DEFAULT_SOURCE, work_dir=WORK_DIR):
    if type(start) is not int or type(end) is not int or not 1 <= start <= end or end-start+1 > 5:
        raise ValueError("النطاق شامل من source_ordinal وبحد أقصى خمس تراجم منشورة")
    with closing(_readonly(db_path)) as db:
        source = _source(db, source_path)
        bios = list(db.execute("SELECT * FROM biographies WHERE source_id=? AND source_ordinal BETWEEN ? AND ? ORDER BY source_ordinal", (source["id"], start, end)))
        if [b["source_ordinal"] for b in bios] != list(range(start, end+1)):
            raise ValueError("رتب التراجم المطلوبة غير موجودة كاملة")
        snapshots = [_snapshot(db, b, source["sha256"]) for b in bios]
        baseline = _digest(snapshots)
        identifier = f"reading-{source['sha256'][:12]}-{start}-{end}-{baseline[:12]}"
        directory = Path(work_dir).resolve()/source["sha256"]/identifier
        if (directory/"manifest.json").exists():
            manifest = _read_json(directory/"manifest.json")
            if manifest.get("batch_id") != identifier or manifest.get("baseline_sha256") != baseline:
                raise ValueError("مساحة عمل تصحيح القراءة لا تطابق الحالة الحالية")
            return {"status": "resumed", "batch": str(directory), "manifest": manifest}
        manifest = {"version": VERSION, "batch_id": identifier, "source_id": source["id"], "source_sha256": source["sha256"],
                    "start_ordinal": start, "end_ordinal": end, "baseline_sha256": baseline, "curator": "", "reason": "", "biographies": []}
        for bio, snapshot in zip(bios, snapshots):
            folder = directory/f"biography-{bio['source_ordinal']}"
            reading = json.loads(snapshot["reading_row"]["payload"]) if snapshot["reading_row"] else {
                "version": READING_VERSION, "biography_id": bio["id"], "source_sha256": source["sha256"],
                "source_char_start": bio["source_char_start"], "source_char_end": bio["source_char_end"],
                "paragraph_groups": [{"start": b["source_char_start"], "end": b["source_char_end"]} for b in build_reading_view(bio["full_text"], bio["source_char_start"], starts_at_line_start=True)["blocks"]], "criticism": []}
            reading["reviewer"] = ""
            reading.setdefault("paragraph_notes", [{**group, "context_start": group["start"], "context_end": group["end"], "reason": ""} for group in reading["paragraph_groups"]])
            reading["heading_labels"] = [{"at": h["at"], "label": h["label"]} for h in effective_annotations(snapshot["pilot"]["presentation"], reading)["headings"]]
            diacritics = json.loads(snapshot["diacritics_row"]["payload"]) if snapshot["diacritics_row"] else export_document(bio, source["sha256"])
            diacritics["reviewer"] = ""
            for name, document in (("source", snapshot), ("reading", reading), ("diacritics", diacritics), ("audit", {"authoring_sha256": "", "reviewer": "", "all_words_checked": False, "all_paragraphs_checked": False, "findings": [], "checks": {key: False for key in AUDIT_CHECKS}})):
                _write_json(folder/(name+".json"), document)
            manifest["biographies"].append({"id": bio["id"], "source_ordinal": bio["source_ordinal"], "heading": bio["heading"],
                    "source_char_start": bio["source_char_start"], "source_char_end": bio["source_char_end"], "directory": folder.name, "baseline_sha256": _digest(snapshot)})
        _write_json(directory/"manifest.json", manifest)
        _write_json(directory/"qa.json", {"revision": "", "content_sha256": "", "reviewer": "", "checks": {key: False for key in QA_CHECKS}, "screenshots": []})
        return {"status": "exported", "batch": str(directory), "manifest": manifest}


def _load(batch):
    directory = Path(batch).resolve()
    manifest = _read_json(directory/"manifest.json")
    if manifest.get("version") != VERSION or not re.fullmatch(r"[a-f0-9]{64}", str(manifest.get("source_sha256", ""))):
        raise ValueError("بيان تصحيح القراءة غير صالح")
    start, end = manifest.get("start_ordinal"), manifest.get("end_ordinal")
    if type(start) is not int or type(end) is not int or not 1 <= start <= end or end-start+1 > 5:
        raise ValueError("نطاق تصحيح القراءة غير صالح")
    if manifest.get("batch_id") != f"reading-{manifest['source_sha256'][:12]}-{start}-{end}-{str(manifest.get('baseline_sha256',''))[:12]}":
        raise ValueError("معرف تصحيح القراءة لا يطابق بيانه")
    entries = manifest.get("biographies", [])
    if [e.get("source_ordinal") for e in entries] != list(range(start, end+1)) or len({e.get("id") for e in entries}) != len(entries):
        raise ValueError("تغطية تصحيح القراءة ناقصة أو مكررة")
    bundles = {}
    for entry in entries:
        expected = f"biography-{entry['source_ordinal']}"
        if entry.get("directory") != expected:
            raise ValueError("مسار ترجمة التصحيح غير صالح")
        bundles[entry["id"]] = {key: _read_json(directory/expected/(key+".json")) for key in ("source", "reading", "diacritics", "audit")}
    if _digest([bundles[e["id"]]["source"] for e in entries]) != manifest.get("baseline_sha256"):
        raise ValueError("لقطات أساس التصحيح لا تطابق بصمتها")
    return directory, manifest, bundles


def authoring_digest(bundle):
    return _digest({key: bundle[key] for key in ("source", "reading", "diacritics")})


def content_digest(bundles):
    return _digest({str(identifier): authoring_digest(bundle) for identifier, bundle in bundles.items()})


def batch_revision(manifest, bundles):
    return _digest({"manifest": manifest, "content": content_digest(bundles), "audit": {str(identifier): b["audit"] for identifier, b in bundles.items()}})


def _published(db, identifier):
    return db.execute("SELECT revision FROM reading_revision_batches WHERE id=?", (identifier,)).fetchone() if _table(db, "reading_revision_batches") else None


def _reject_human(value):
    if isinstance(value, dict):
        if set(value).intersection(("human_status", "human_checked", "human_reviewer", "review_status", "identity_review_status")) or value.get("method") == "reviewed":
            raise ValueError("تصحيح القراءة لا يمنح اعتمادًا بشريًا")
        for child in value.values():
            _reject_human(child)
    elif isinstance(value, list):
        for child in value:
            _reject_human(child)


def _validate(db, source, manifest, bundles, *, require_audit=True, installed_revision=None):
    if (source["id"], source["sha256"]) != (manifest["source_id"], manifest["source_sha256"]):
        raise ValueError("مصدر تصحيح القراءة تغير")
    if any(not isinstance(manifest.get(key), str) or not manifest[key].strip() for key in ("curator", "reason")):
        raise ValueError("يلزم اسم مؤلف التصحيح وسبب إصداره")
    reports = []
    for entry in manifest["biographies"]:
        bio = db.execute("SELECT * FROM biographies WHERE source_id=? AND source_ordinal=?", (source["id"], entry["source_ordinal"])).fetchone()
        bundle = bundles[entry["id"]]
        snapshot = bundle["source"]
        if bio is None or bio["id"] != entry["id"] or any(bio[key] != entry[key] for key in ("heading", "source_char_start", "source_char_end")) or _digest(snapshot) != entry["baseline_sha256"]:
            raise ValueError("تعريف الترجمة أو لقطة المصدر لا يطابق تصحيح القراءة")
        current = _snapshot(db, bio, source["sha256"])
        if installed_revision is None:
            if current != snapshot:
                raise ValueError("تغيرت نسخة القراءة أو التنسيق أثناء التأليف؛ يلزم تصدير جديد")
        elif current["pilot_row"] != snapshot["pilot_row"] or current["full_text"] != snapshot["full_text"] or current["reading_row"] is None or current["reading_row"]["revision"] != installed_revision or current["diacritics_row"] is None or current["diacritics_row"]["revision"] != installed_revision or json.loads(current["reading_row"]["payload"]) != bundle["reading"] or json.loads(current["diacritics_row"]["payload"]) != bundle["diacritics"]:
            raise ValueError("النسخة المنشورة تغيرت بعد إصدار تصحيح القراءة")
        for key in ("reading", "diacritics", "audit"):
            _reject_human(bundle[key])
        reading, diacritics = bundle["reading"], bundle["diacritics"]
        if reading.get("reviewer") != manifest["curator"]:
            raise ValueError("اسم فاحص القراءة لا يطابق المؤلف")
        validate_reading_document(db, reading, base_document=snapshot["pilot"])
        validate_paragraph_notes(reading, required=True)
        annotations = effective_annotations(snapshot["pilot"]["presentation"], reading)
        validate_editorial_headings(annotations["headings"])
        coverage = validate_diacritics(diacritics, bio, reading, source["sha256"], manifest["curator"])
        if require_audit:
            audit = bundle["audit"]
            if audit.get("authoring_sha256") != authoring_digest(bundle) or not isinstance(audit.get("reviewer"), str) or not audit["reviewer"].strip() or audit["reviewer"].strip() == manifest["curator"].strip():
                raise ValueError("يلزم تدقيق مستقل مرتبط بالنسخة الحالية من التصحيح")
            if audit.get("all_words_checked") is not True or audit.get("all_paragraphs_checked") is not True or any(audit.get("checks", {}).get(key) is not True for key in AUDIT_CHECKS):
                raise ValueError("تدقيق تصحيح القراءة غير مكتمل")
            if not isinstance(audit.get("findings"), list) or any(not isinstance(f, dict) or f.get("resolved") is not True or not isinstance(f.get("note"), str) or not f["note"].strip() for f in audit["findings"]):
                raise ValueError("توجد ملاحظات تصحيح قراءة غير محلولة")
        reports.append({"biography_id": bio["id"], "source_ordinal": bio["source_ordinal"], "paragraph_groups": len(reading["paragraph_groups"]), "criticism": len(reading.get("criticism", [])), "diacritics_coverage": coverage, "authoring_sha256": authoring_digest(bundle)})
    return reports


def validate_batch(batch, *, db_path=DEFAULT_DB, source_path=DEFAULT_SOURCE, require_audit=True):
    _, manifest, bundles = _load(batch)
    revision = batch_revision(manifest, bundles)
    with closing(_readonly(db_path)) as db:
        source = _source(db, source_path)
        old = _published(db, manifest["batch_id"])
        if old and old[0] != revision:
            raise ValueError("تصحيح القراءة منشور بإصدار مختلف؛ لا تستبدله")
        reports = _validate(db, source, manifest, bundles, require_audit=require_audit, installed_revision=revision if old else None)
    return {"valid": True, "status": "published" if old else "validated", "revision": revision, "biographies": reports}


def _require_qa(directory, revision, bundles):
    try:
        proof, qa = _read_json(directory/"preview.json"), _read_json(directory/"qa.json")
    except OSError as error:
        raise ValueError("تلزم معاينة وتقرير QA لتصحيح القراءة") from error
    digest = content_digest(bundles)
    if proof.get("revision") != revision or proof.get("content_sha256") != digest or not Path(proof.get("database", "")).is_file():
        raise ValueError("معاينة تصحيح القراءة قديمة أو غير موجودة")
    if qa.get("revision") != revision or qa.get("content_sha256") != digest or not isinstance(qa.get("reviewer"), str) or not qa["reviewer"].strip() or any(qa.get("checks", {}).get(key) is not True for key in QA_CHECKS):
        raise ValueError("QA تصحيح القراءة ناقص أو قديم أو يتضمن إخفاقًا")
    screenshots = qa.get("screenshots")
    if not isinstance(screenshots, list) or not screenshots or any(not isinstance(p, str) or not _screenshot_valid(Path(p) if Path(p).is_absolute() else directory/p) for p in screenshots):
        raise ValueError("تلزم صور فحص فعلية للنسخة الحالية")


def status_batch(batch, *, db_path=DEFAULT_DB, source_path=DEFAULT_SOURCE):
    directory, manifest, bundles = _load(batch)
    revision = batch_revision(manifest, bundles)
    gates, reasons = {}, {}
    for key, audit in (("authoring_valid", False), ("audit_valid", True)):
        try:
            value = validate_batch(batch, db_path=db_path, source_path=source_path, require_audit=audit)
            gates[key] = True
            if audit:
                reports = value["biographies"]
        except (ValueError, KeyError, TypeError, IndexError) as error:
            gates[key] = False; reasons[key] = str(error)
    proof = _read_json(directory/"preview.json") if (directory/"preview.json").is_file() else {}
    gates["preview_current"] = proof.get("revision") == revision and proof.get("content_sha256") == content_digest(bundles) and Path(proof.get("database", "")).is_file()
    try:
        _require_qa(directory, revision, bundles)
        gates["qa_current"] = gates["preview_current"]
    except (OSError, ValueError, KeyError, TypeError) as error:
        gates["qa_current"] = False; reasons["qa_current"] = str(error)
    with closing(_readonly(db_path)) as db:
        old = _published(db, manifest["batch_id"])
    gates["published"] = bool(old and old[0] == revision)
    return {"batch_id": manifest["batch_id"], "revision": revision, "content_sha256": content_digest(bundles),
            "gates": gates, "remaining": [key for key, value in gates.items() if not value], "reasons": reasons,
            "authoring": [{"biography_id": identifier, "authoring_sha256": authoring_digest(b)} for identifier, b in bundles.items()],
            "biographies": reports if gates.get("audit_valid") else []}


def _schema(db):
    db.execute(READING_SCHEMA)
    db.execute(DIACRITICS_SCHEMA)
    for statement in SCHEMA.split(";"):
        if statement.strip():
            db.execute(statement)


def _install(db, manifest, entry, bundle, revision):
    source, reading, diacritics = bundle["source"], bundle["reading"], bundle["diacritics"]
    prior_reading, prior_diacritics = source["reading_row"], source["diacritics_row"]
    reading_json, diacritics_json = json.dumps(reading, ensure_ascii=False), json.dumps(diacritics, ensure_ascii=False)
    db.execute("INSERT INTO reading_revision_history(batch_id,biography_id,source_id,source_sha256,previous_reading_json,previous_diacritics_json,new_reading_json,new_diacritics_json,previous_reading_revision,previous_diacritics_revision,revision,curator,reviewer,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (manifest["batch_id"], entry["id"], manifest["source_id"], manifest["source_sha256"], json.dumps(prior_reading, ensure_ascii=False) if prior_reading else None, json.dumps(prior_diacritics, ensure_ascii=False) if prior_diacritics else None, reading_json, diacritics_json, prior_reading["revision"] if prior_reading else None, prior_diacritics["revision"] if prior_diacritics else None, revision, manifest["curator"], bundle["audit"]["reviewer"], manifest["reason"]))
    db.execute("INSERT INTO pilot_reading_overlays(biography_id,source_id,source_sha256,revision,payload) VALUES (?,?,?,?,?) ON CONFLICT(biography_id) DO UPDATE SET source_id=excluded.source_id,source_sha256=excluded.source_sha256,revision=excluded.revision,payload=excluded.payload,updated_at=CURRENT_TIMESTAMP",
               (entry["id"], manifest["source_id"], manifest["source_sha256"], revision, reading_json))
    db.execute("INSERT INTO curation_diacritics(biography_id,source_id,source_sha256,revision,payload) VALUES (?,?,?,?,?) ON CONFLICT(biography_id) DO UPDATE SET source_id=excluded.source_id,source_sha256=excluded.source_sha256,revision=excluded.revision,payload=excluded.payload",
               (entry["id"], manifest["source_id"], manifest["source_sha256"], revision, diacritics_json))


def _publish(batch, db_path, source_path, *, preview=False):
    directory, manifest, bundles = _load(batch)
    revision = batch_revision(manifest, bundles)
    validation = validate_batch(batch, db_path=db_path, source_path=source_path)
    if validation["status"] == "published":
        return {"status": "unchanged", "batch_id": manifest["batch_id"], "revision": revision}
    if not preview:
        _require_qa(directory, revision, bundles)
    backup = None if preview else backup_database(db_path)
    with closing(connect(db_path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            source = _source(db, source_path)
            _validate(db, source, manifest, bundles)
            _schema(db)
            db.execute("INSERT INTO reading_revision_batches(id,source_id,source_sha256,start_ordinal,end_ordinal,revision,manifest_json) VALUES (?,?,?,?,?,?,?)",
                       (manifest["batch_id"], manifest["source_id"], manifest["source_sha256"], manifest["start_ordinal"], manifest["end_ordinal"], revision, json.dumps(manifest, ensure_ascii=False)))
            for entry in manifest["biographies"]:
                _install(db, manifest, entry, bundles[entry["id"]], revision)
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return {"status": "preview" if preview else "published", "batch_id": manifest["batch_id"], "revision": revision, "backup": str(backup) if backup else None, "biographies": len(bundles)}


def publish_batch(batch, *, db_path=DEFAULT_DB, source_path=DEFAULT_SOURCE):
    return _publish(batch, db_path, source_path)


def preview_batch(batch, *, db_path=DEFAULT_DB, source_path=DEFAULT_SOURCE, output=None):
    directory, manifest, bundles = _load(batch)
    validation = validate_batch(batch, db_path=db_path, source_path=source_path)
    if validation["status"] == "published":
        raise ValueError("التصحيح منشور بالفعل؛ استخدم status ولا تنشئ معاينة زائفة")
    target = Path(output).resolve() if output else directory/"preview.sqlite3"
    if target == Path(db_path).resolve() or target.exists():
        raise ValueError("يلزم مسار معاينة جديد مستقل")
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(_readonly(db_path)) as source, closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    try:
        result = _publish(batch, target, source_path, preview=True)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    proof = {"revision": result["revision"], "content_sha256": content_digest(bundles), "database": str(target), "source_sha256": manifest["source_sha256"], "preview_only": True}
    _write_json(directory/"preview.json", proof)
    return {**result, **proof, "commands": [f"$env:SIYAR_DB = '{target}'", "python -m uvicorn backend.app:app --host 127.0.0.1 --port 8001"], "frontend_commands": ["$env:VITE_API_TARGET = 'http://127.0.0.1:8001'", "npm --prefix frontend run dev -- --port 5174"]}


def main():
    parser = argparse.ArgumentParser(description="تصحيح طبقات قراءة منشورة مع حفظ الأصل والموضوعات وتاريخ النسخ")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("export", "status", "validate", "preview", "publish"):
        command = sub.add_parser(name)
        command.add_argument("--db", type=Path, default=DEFAULT_DB)
        command.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
        if name == "export":
            command.add_argument("--start", type=int, required=True); command.add_argument("--end", type=int, required=True)
            command.add_argument("--work-dir", type=Path, default=WORK_DIR)
        else:
            command.add_argument("--batch", type=Path, required=True)
        if name == "validate": command.add_argument("--authoring-only", action="store_true")
        if name == "preview": command.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "export": result = export_batch(args.start, args.end, db_path=args.db, source_path=args.source, work_dir=args.work_dir)
        elif args.command == "status": result = status_batch(args.batch, db_path=args.db, source_path=args.source)
        elif args.command == "validate": result = validate_batch(args.batch, db_path=args.db, source_path=args.source, require_audit=not args.authoring_only)
        elif args.command == "preview": result = preview_batch(args.batch, db_path=args.db, source_path=args.source, output=args.output)
        else: result = publish_batch(args.batch, db_path=args.db, source_path=args.source)
    except (ValueError, KeyError, TypeError, IndexError, OSError, json.JSONDecodeError) as error:
        parser.exit(2, str(error)+"\n")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
