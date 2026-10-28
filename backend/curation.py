"""Source-bound, append-only editorial batches; draft export never edits the library.

The CLI deliberately does not classify or approve text. Agents author the exported
documents, another agent records a hash-bound audit, and browser QA runs against
an isolated preview copy before a batch can be published locally.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile

from fastapi import APIRouter

from .db import DEFAULT_DB, DEFAULT_SOURCE, ROOT, connect
from .pilot import _digest, _insert_evidence, _literal, backup_database, has_pilot, validate_document
from .pilot_reading import VERSION as READING_VERSION, SCHEMA as READING_SCHEMA, validate_reading_document, validate_paragraph_notes, grouped_reading_view
from .reading import build_reading_view

LEGACY_VERSION = "curation-batch-v1"
VERSION = "curation-batch-v2"
WORK_DIR = ROOT / "reference" / "curation"
BASE_AUDIT_CHECKS = ("source_coverage", "paragraphs", "criticism_context", "topic_evidence", "negative_cases")
AUDIT_CHECKS = (*BASE_AUDIT_CHECKS, "diacritics", "paragraph_context")
BASE_QA_CHECKS = ("desktop", "mobile", "keyboard", "copy_raw", "copy_formatted", "highlight", "footnotes", "no_horizontal_overflow", "no_console_errors", "unchanged_outside_batch")
QA_CHECKS = (*BASE_QA_CHECKS, "diacritics_toggle")
PRESENTATION_KEYS = ("headings", "quotes", "poetry", "lists", "bold", "keywords", "names", "footnotes")


def _read_json(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _screenshot_valid(path):
    try:
        with Path(path).open("rb") as handle:
            prefix=handle.read(24)
        return (len(prefix)>=24 and prefix[:8]==b"\x89PNG\r\n\x1a\n" and prefix[12:16]==b"IHDR" and int.from_bytes(prefix[16:20],"big")>0 and int.from_bytes(prefix[20:24],"big")>0) or prefix[:3]==b"\xff\xd8\xff"
    except OSError:
        return False


def _readonly(db_path):
    if not Path(db_path).is_file():
        raise ValueError("قاعدة المصدر غير موجودة؛ لا ينشئ التصدير قاعدة بديلة")
    db = sqlite3.connect(Path(db_path).resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _has_batches(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='curation_batches'").fetchone() is not None


def is_published_biography(db, biography_id):
    return bool(_has_batches(db) and db.execute("SELECT 1 FROM curation_batch_members WHERE biography_id=?", (biography_id,)).fetchone())


def has_new_batches(db):
    return bool(_has_batches(db) and db.execute("SELECT 1 FROM curation_batches WHERE legacy=0 LIMIT 1").fetchone())


def _legacy_rows(db):
    if not has_pilot(db):
        return []
    return list(db.execute("SELECT b.id,b.source_id,b.source_ordinal,b.heading,s.sha256 FROM biographies b JOIN pilot_documents p ON p.biography_id=b.id JOIN sources s ON s.id=b.source_id WHERE p.source_sha256=s.sha256 AND b.source_ordinal<=5 ORDER BY b.source_id,b.source_ordinal"))


def _legacy_id(sha):
    return "legacy-first-five:" + sha[:12]


def list_batches(db):
    items = []
    registered = set()
    if _has_batches(db):
        for row in db.execute("SELECT c.* FROM curation_batches c JOIN sources s ON s.id=c.source_id WHERE c.source_sha256=s.sha256 ORDER BY c.source_id,c.start_ordinal,c.id"):
            bios = [dict(b) for b in db.execute("SELECT b.id,b.source_ordinal,b.heading FROM curation_batch_members m JOIN biographies b ON b.id=m.biography_id WHERE m.batch_id=? ORDER BY b.source_ordinal", (row["id"],))]
            items.append({"id": row["id"], "batch_id": row["id"], "label": row["label"], "source_id": row["source_id"], "start_ordinal": row["start_ordinal"], "end_ordinal": row["end_ordinal"], "biographies": bios, "legacy": bool(row["legacy"]), "is_legacy_pilot": bool(row["legacy"]), "published": True, "status": "published"})
            registered.update(b["id"] for b in bios)
    remaining = {}
    for bio in _legacy_rows(db):
        if bio["id"] not in registered:
            remaining.setdefault((bio["source_id"], bio["sha256"]), []).append({"id": bio["id"], "source_ordinal": bio["source_ordinal"], "heading": bio["heading"]})
    for (source_id, sha), bios in remaining.items():
        identifier = _legacy_id(sha)
        items.append({"id": identifier, "batch_id": identifier, "label": "العينة الأصلية: التراجم الخمس الأولى", "source_id": source_id, "start_ordinal": bios[0]["source_ordinal"], "end_ordinal": bios[-1]["source_ordinal"], "biographies": bios, "legacy": True, "is_legacy_pilot": True, "published": True, "status": "published"})
    return {"items": items}


def batch_biography_ids(db, batch_id):
    for batch in list_batches(db)["items"]:
        if batch["id"] == batch_id:
            return [bio["id"] for bio in batch["biographies"]]
    raise ValueError("الدفعة المنشورة غير موجودة أو تغير مصدرها")


def biography_batch(db, biography_id):
    return next((batch for batch in list_batches(db)["items"] if any(bio["id"] == biography_id for bio in batch["biographies"])), None)


def _source(db, source_path):
    path = Path(source_path)
    if not path.is_file():
        raise ValueError("المصدر مفقود؛ يلزم النص الأصلي المطابق للاستيراد")
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    row = db.execute("SELECT id,sha256,full_text FROM sources WHERE sha256=?", (sha,)).fetchone()
    if row is None or raw.decode("utf-8") != row["full_text"]:
        raise ValueError("بصمة الملف ونصه لا يطابقان مصدرًا مستوردًا")
    return row


def _snapshot(db, bio):
    segments = [dict(row) for row in db.execute("SELECT * FROM segments WHERE biography_id=? ORDER BY segment_ordinal", (bio["id"],))]
    hits = [dict(row) for row in db.execute("SELECT h.* FROM topic_hits h JOIN segments s ON s.id=h.segment_id WHERE s.biography_id=? ORDER BY h.id", (bio["id"],))]
    candidates = []
    # Parentheses are candidates, not automatically footnotes. Nested and
    # interpretive parentheses remain visible unless explicitly examined.
    for match in re.finditer(r"\([^()\r\n]{1,180}\)", bio["full_text"]):
        a, b = bio["source_char_start"] + match.start(), bio["source_char_start"] + match.end()
        candidates.append({"start": a, "end": b, "raw": match.group()})
    return {"biography_id": bio["id"], "source_ordinal": bio["source_ordinal"], "heading": bio["heading"], "source_char_start": bio["source_char_start"], "source_char_end": bio["source_char_end"], "full_text": bio["full_text"], "segments": segments, "existing_hits": hits, "footnote_candidates": candidates}


def export_batch(start, end, *, db_path=DEFAULT_DB, source_path=DEFAULT_SOURCE, work_dir=WORK_DIR):
    if type(start) is not int or type(end) is not int or start < 1 or not start <= end or end-start+1 > 5:
        raise ValueError("النطاق شامل وبحد أقصى خمس تراجم من source_ordinal")
    with closing(_readonly(db_path)) as db:
        source = _source(db, source_path)
        rows = list(db.execute("SELECT * FROM biographies WHERE source_id=? AND source_ordinal BETWEEN ? AND ? ORDER BY source_ordinal", (source["id"], start, end)))
        if [row["source_ordinal"] for row in rows] != list(range(start,end+1)):
            raise ValueError("رتب التراجم المطلوبة غير موجودة كاملة في المصدر")
        identifier = f"{source['sha256'][:12]}-{start}-{end}"
        directory = Path(work_dir).resolve() / source["sha256"] / identifier
        manifest_path = directory / "manifest.json"
        if manifest_path.exists():
            previous = _read_json(manifest_path)
            if previous.get("source_sha256") != source["sha256"] or previous.get("batch_id") != identifier:
                raise ValueError("مساحة الاستئناف لا تطابق الدفعة")
            return {"status": "resumed", "batch": str(directory), "manifest": previous}
        manifest = {"version": VERSION, "batch_id": identifier, "source_id": source["id"], "source_sha256": source["sha256"], "start_ordinal": start, "end_ordinal": end, "curator": "", "biographies": []}
        for bio in rows:
            snapshot = _snapshot(db, bio)
            skip = bool(has_pilot(db) and db.execute("SELECT 1 FROM pilot_documents WHERE biography_id=?", (bio["id"],)).fetchone())
            entry = {"id": bio["id"], "source_ordinal": bio["source_ordinal"], "heading": bio["heading"], "source_char_start": bio["source_char_start"], "source_char_end": bio["source_char_end"], "status": "skipped" if skip else "pending", "directory": f"biography-{bio['source_ordinal']}", "baseline_sha256": _digest(snapshot)}
            manifest["biographies"].append(entry)
            if skip:
                continue
            target = directory / entry["directory"]
            common = {"source_sha256": source["sha256"], "biography_id": bio["id"], "source_char_start": bio["source_char_start"], "source_char_end": bio["source_char_end"], "reviewer": ""}
            pilot = {**common, "segments": [{"segment_id": segment["id"], "assessment": "unexamined", "note": "", "topics": [], "existing_hits": [{"hit_id": hit["id"], "decision": "uncertain", "reason": ""} for hit in snapshot["existing_hits"] if hit["segment_id"] == segment["id"]]} for segment in snapshot["segments"]], "presentation": {key: [] for key in PRESENTATION_KEYS}}
            reading = {**common, "version": READING_VERSION, "paragraph_groups": [], "paragraph_notes": [], "criticism": []}
            footnotes = {"candidates": [{**candidate, "decision": "ambiguous", "reason": ""} for candidate in snapshot["footnote_candidates"]], "bodies_status": "uncertain", "body_ranges": [], "notice": ""}
            from .diacritics import export_document
            diacritics = export_document(bio, source["sha256"])
            for filename, value in (("source.json", snapshot), ("pilot.json", pilot), ("reading.json", reading), ("footnotes.json", footnotes), ("diacritics.json", diacritics), ("gold.json", []), ("audit.json", {"authoring_sha256": "", "reviewer": "", "all_segments_checked": False, "all_footnotes_checked": False, "findings": [], "checks": {key: False for key in AUDIT_CHECKS}})):
                _write_json(target / filename, value)
        _write_json(manifest_path, manifest)
        _write_json(directory / "qa.json", {"revision": "", "gold_frozen_sha256": "", "reviewer": "", "checks": {key: False for key in QA_CHECKS}, "screenshots": []})
        return {"status": "exported", "batch": str(directory), "manifest": manifest}


def upgrade_batch(batch, *, db_path=DEFAULT_DB, source_path=DEFAULT_SOURCE):
    """Upgrade a draft through a staged directory swap; the old tree is backup."""
    directory, manifest, bundles = _load(batch)
    with closing(_readonly(db_path)) as db:
        source = _source(db, source_path)
        if (source["id"], source["sha256"]) != (manifest["source_id"], manifest["source_sha256"]):
            raise ValueError("مصدر الدفعة تغير؛ لا يمكن ترقيتها")
        if _has_batches(db) and db.execute("SELECT 1 FROM curation_batches WHERE id=?", (manifest["batch_id"],)).fetchone():
            raise ValueError("لا تجوز ترقية دفعة منشورة أو استبدالها")
        for entry in manifest["biographies"]:
            if entry["status"] == "skipped":
                continue
            bio = db.execute("SELECT * FROM biographies WHERE id=? AND source_id=? AND source_ordinal=?", (entry["id"], source["id"], entry["source_ordinal"])).fetchone()
            if bio is None or _digest(_snapshot(db, bio)) != entry["baseline_sha256"] or _digest(bundles[entry["id"]]["source"]) != entry["baseline_sha256"]:
                raise ValueError("تغير النص أو المقاطع أو المرشحات؛ يلزم تصدير مطابق")
            if has_pilot(db) and db.execute("SELECT 1 FROM pilot_documents WHERE biography_id=?", (entry["id"],)).fetchone():
                raise ValueError("نشرت ترجمة أثناء التأليف؛ أعد التصدير قبل الترقية")
        if manifest["version"] == VERSION:
            return {"status": "unchanged", "batch": str(directory), "version": VERSION}
        from .diacritics import export_document
        staged = Path(tempfile.mkdtemp(prefix=directory.name + "-upgrade-", dir=directory.parent))
        backup = directory.with_name(directory.name + "-before-v2-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
        moved = False
        try:
            shutil.copytree(directory, staged, dirs_exist_ok=True, ignore=shutil.ignore_patterns("*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm"))
            for entry in manifest["biographies"]:
                if entry["status"] == "skipped":
                    continue
                target = staged / entry["directory"]
                reading = bundles[entry["id"]]["reading"]
                reading.setdefault("paragraph_notes", [{**group, "context_start": group["start"], "context_end": group["end"], "reason": ""} for group in reading["paragraph_groups"]])
                _write_json(target / "reading.json", reading)
                bio = db.execute("SELECT * FROM biographies WHERE id=?", (entry["id"],)).fetchone()
                _write_json(target / "diacritics.json", export_document(bio, source["sha256"], manifest["curator"]))
                _write_json(target / "audit.json", {"authoring_sha256": "", "reviewer": "", "all_segments_checked": False, "all_footnotes_checked": False, "findings": [], "checks": {key: False for key in AUDIT_CHECKS}})
            manifest["version"] = VERSION
            _write_json(staged / "manifest.json", manifest)
            _write_json(staged / "qa.json", {"revision": "", "gold_frozen_sha256": "", "reviewer": "", "checks": {key: False for key in QA_CHECKS}, "screenshots": []})
            (staged / "preview.json").unlink(missing_ok=True)
            os.replace(directory, backup)
            moved = True
            try:
                os.replace(staged, directory)
            except BaseException:
                os.replace(backup, directory)
                moved = False
                raise
        finally:
            # Only our freshly created staging sibling may be cleaned up.
            if staged.exists() and staged.resolve().parent == directory.parent.resolve() and staged.name.startswith(directory.name + "-upgrade-"):
                shutil.rmtree(staged)
        return {"status": "upgraded", "batch": str(directory), "version": VERSION, "backup": str(backup) if moved else None, "invalidated": ["audit", "preview", "qa"]}


def _bundle(directory, entry, version=None):
    directory = Path(directory)
    if version is None:
        version = _read_json(directory / "manifest.json")["version"]
    target = directory / f"biography-{entry['source_ordinal']}"
    if entry.get("directory") != target.name:
        raise ValueError("مسار ملف الترجمة غير صالح")
    keys = ("source", "pilot", "reading", "footnotes", "gold", "audit")
    if version == VERSION:
        keys += ("diacritics",)
    return {key: _read_json(target / f"{key}.json") for key in keys}


def authoring_digest(bundle):
    keys = ("source", "pilot", "reading", "footnotes", "gold")
    if "diacritics" in bundle:
        keys += ("diacritics",)
    return _digest({key:bundle[key] for key in keys})


def _load(directory):
    directory = Path(directory).resolve()
    manifest = _read_json(directory / "manifest.json")
    if manifest.get("version") not in (LEGACY_VERSION, VERSION) or not re.fullmatch(r"[a-f0-9]{64}", str(manifest.get("source_sha256", ""))):
        raise ValueError("بيان الدفعة غير صالح")
    start, end = manifest.get("start_ordinal"), manifest.get("end_ordinal")
    if type(start) is not int or type(end) is not int or not 1 <= start <= end or end-start+1 > 5:
        raise ValueError("نطاق الدفعة غير صالح")
    if manifest.get("batch_id") != f"{manifest['source_sha256'][:12]}-{start}-{end}":
        raise ValueError("معرف الدفعة لا يطابق نطاقها")
    entries = manifest.get("biographies", [])
    if [entry.get("source_ordinal") for entry in entries] != list(range(start,end+1)) or any(entry.get("status") not in ("pending", "skipped") for entry in entries):
        raise ValueError("بيان التغطية غير مكتمل أو مكرر")
    bundles = {entry["id"]: _bundle(directory, entry, manifest["version"]) for entry in entries if entry["status"] == "pending"}
    return directory, manifest, bundles


def batch_revision(manifest, bundles):
    return _digest({"manifest":manifest, "authoring":{str(identifier):authoring_digest(bundle) for identifier,bundle in bundles.items()}, "audit":{str(identifier):bundle["audit"] for identifier,bundle in bundles.items()}})


def _validate(db, source, manifest, bundles, *, require_audit=True, allow_published=False):
    if source["sha256"] != manifest["source_sha256"] or source["id"] != manifest["source_id"]:
        raise ValueError("مصدر الدفعة تغير")
    if bundles and not str(manifest.get("curator", "")).strip():
        raise ValueError("اسم وكيل التأليف مطلوب")
    reports = []
    for entry in manifest["biographies"]:
        bio = db.execute("SELECT * FROM biographies WHERE source_id=? AND source_ordinal=?", (source["id"], entry["source_ordinal"])).fetchone()
        if bio is None or (bio["id"],bio["heading"],bio["source_char_start"],bio["source_char_end"]) != (entry["id"],entry["heading"],entry["source_char_start"],entry["source_char_end"]):
            raise ValueError("تعريف الترجمة لا يطابق المصدر")
        existing = bool(has_pilot(db) and db.execute("SELECT 1 FROM pilot_documents WHERE biography_id=?", (bio["id"],)).fetchone())
        if entry["status"] == "skipped":
            if not existing:
                raise ValueError("ترجمة معلّمة بالتجاوز ليست منشورة")
            continue
        if existing and not allow_published:
            raise ValueError("الترجمة نشرت أثناء التأليف؛ أعد التصدير لتجاوزها دون استبدالها")
        bundle = bundles[bio["id"]]
        snapshot = _snapshot(db,bio)
        if _digest(snapshot) != entry["baseline_sha256"] or _digest(bundle["source"]) != entry["baseline_sha256"]:
            raise ValueError("تغيرت المقاطع أو المرشحات أو ملف النص المصدّر؛ يلزم إعادة الفحص")
        pilot, reading = bundle["pilot"], bundle["reading"]
        if pilot.get("reviewer") != manifest["curator"] or reading.get("reviewer") != manifest["curator"]:
            raise ValueError("اسم الفاحص لا يطابق وكيل التأليف")
        def reject_human(value):
            if isinstance(value,dict):
                if set(value).intersection(("human_status","human_checked","human_reviewer","review_status","identity_review_status")) or value.get("method")=="reviewed":
                    raise ValueError("لا يجوز أن يحمل ملف التأليف اعتمادًا بشريًا")
                for child in value.values():reject_human(child)
            elif isinstance(value,list):
                for child in value:reject_human(child)
        for authored in (pilot,reading,bundle["footnotes"],bundle["audit"],bundle.get("diacritics",{})):reject_human(authored)
        validate_document(db,pilot)
        validate_reading_document(db,reading,base_document=pilot)
        vocalization = None
        if manifest["version"] == VERSION:
            from .diacritics import validate_document as validate_diacritics, validate_editorial_headings
            validate_paragraph_notes(reading, required=True)
            validate_editorial_headings(pilot["presentation"]["headings"])
            vocalization = validate_diacritics(bundle["diacritics"], bio, reading, source["sha256"], manifest["curator"])
        # The render proof runs before publication as well as at read time.
        grouped_reading_view(build_reading_view(bio["full_text"],bio["source_char_start"],starts_at_line_start=True),reading["paragraph_groups"])
        expected = {(item["start"],item["end"],item["raw"]) for item in snapshot["footnote_candidates"]}
        ledger = bundle["footnotes"].get("candidates", [])
        if len(ledger) != len(expected) or {(item.get("start"),item.get("end"),item.get("raw")) for item in ledger} != expected:
            raise ValueError("يلزم فحص جميع مرشحات الحواشي مرة واحدة")
        if any(item.get("decision") not in ("verified_marker","body_literal","ambiguous") or not str(item.get("reason","")).strip() for item in ledger):
            raise ValueError("سجل الحواشي غير مكتمل")
        moved = {(item["start"],item["end"]) for item in ledger if item["decision"] == "verified_marker"}
        if moved != {(item["start"],item["end"]) for item in pilot["presentation"]["footnotes"]}:
            raise ValueError("الإشارات المنقولة لا تطابق سجل الحواشي")
        # v1 only moves confirmed numeric references. Bodies, book names and
        # uncertain parentheses stay in the source body.
        if any(not re.fullmatch(r"\([0-9]+\)",item["raw"]) for item in ledger if item["decision"] == "verified_marker"):
            raise ValueError("لا تنقل الأقواس التفسيرية أو متون الحواشي")
        footnotes=bundle["footnotes"]
        if footnotes.get("bodies_status") not in ("absent","present_unlinked","linked","uncertain") or not str(footnotes.get("notice","")).strip():
            raise ValueError("يلزم وصف حالة متون الحواشي وتنبيهها من الفحص الفعلي")
        if footnotes["bodies_status"]=="linked":
            raise ValueError("ربط متون الحواشي غير مدعوم في هذه النسخة؛ أبقها present_unlinked وظاهرة في المتن")
        body_ranges=footnotes.get("body_ranges")
        if not isinstance(body_ranges,list):raise ValueError("نطاقات متون الحواشي غير صالحة")
        for body in body_ranges:
            a,b=body.get("start"),body.get("end")
            if type(a) is not int or type(b) is not int or not bio["source_char_start"]<=a<b<=bio["source_char_end"] or any(a<y and b>x for x,y in moved):
                raise ValueError("متن حاشية خارج الترجمة أو مخفي من العرض")
        if footnotes["bodies_status"] in ("present_unlinked","linked") and not body_ranges:
            raise ValueError("متون موجودة بلا نطاقات موثقة")
        if footnotes["bodies_status"]=="absent" and body_ranges:
            raise ValueError("وصف غياب الحواشي يناقض النطاقات")
        if pilot["presentation"].get("footnote_notice")!=footnotes["notice"]:
            raise ValueError("تنبيه الحواشي في العرض لا يطابق سجل فحصها")
        gold_document = bundle["gold"]
        gold=gold_document.get("items") if isinstance(gold_document,dict) else gold_document
        if not isinstance(gold,list) or len(gold)>3 or (not gold and (not isinstance(gold_document,dict) or not str(gold_document.get("unavailable_reason","")).strip())):
            raise ValueError("يلزم شاهد تقييم إلى ثلاثة أو تعليل صريح لعدم وجود مادة قابلة لسؤال")
        for item in gold:
            a,b=item.get("start"),item.get("end")
            if type(a) is not int or type(b) is not int or not bio["source_char_start"] <= a < b <= bio["source_char_end"] or not str(item.get("question","")).strip():
                raise ValueError("سؤال التقييم أو شاهده غير صالح")
        digest = authoring_digest(bundle)
        if require_audit:
            audit=bundle["audit"]
            if audit.get("authoring_sha256") != digest or not str(audit.get("reviewer","")).strip() or audit["reviewer"].strip() == manifest["curator"].strip():
                raise ValueError("يلزم تدقيق مستقل مرتبط ببصمة ملفات التأليف الحالية")
            required_checks = AUDIT_CHECKS if manifest["version"] == VERSION else BASE_AUDIT_CHECKS
            if audit.get("all_segments_checked") is not True or audit.get("all_footnotes_checked") is not True or any(audit.get("checks",{}).get(key) is not True for key in required_checks):
                raise ValueError("التدقيق المستقل غير مكتمل")
            findings=audit.get("findings")
            if not isinstance(findings,list) or any(not isinstance(item,dict) or item.get("resolved") is not True or not str(item.get("note","")).strip() for item in findings):
                raise ValueError("توجد ملاحظات تدقيق غير محلولة")
        report = {"biography_id":bio["id"],"source_ordinal":bio["source_ordinal"],"authoring_sha256":digest,"segments":len(pilot["segments"]),"footnote_candidates":len(ledger),"paragraph_groups":len(reading["paragraph_groups"]),"criticism":len(reading["criticism"])}
        if vocalization is not None:
            report["diacritics_coverage"] = vocalization
        reports.append(report)
    return reports


def status_batch(batch, *, db_path=DEFAULT_DB, source_path=DEFAULT_SOURCE):
    directory,manifest,bundles=_load(batch)
    revision=batch_revision(manifest,bundles)
    with closing(_readonly(db_path)) as db:
        source=_source(db,source_path)
        if source["sha256"]!=manifest["source_sha256"]:
            raise ValueError("مصدر الدفعة تغير")
        published=db.execute("SELECT revision FROM curation_batches WHERE id=?",(manifest["batch_id"],)).fetchone() if _has_batches(db) else None
    result={"status":"published" if published else "draft","batch":str(directory),"batch_id":manifest["batch_id"],"revision":revision,"published_revision":published[0] if published else None,"authoring":[{"biography_id":identifier,"authoring_sha256":authoring_digest(bundle)} for identifier,bundle in bundles.items()],"skipped":[entry["source_ordinal"] for entry in manifest["biographies"] if entry["status"]=="skipped"]}
    gates={"authoring_valid":False,"audit_valid":False,"preview_current":False,"qa_current":False,"published":bool(published and published[0]==revision)}
    reasons={}
    for gate,require_audit in (("authoring_valid",False),("audit_valid",True)):
        try:
            validation=validate_batch(batch,db_path=db_path,source_path=source_path,require_audit=require_audit)
            gates[gate]=True
            if require_audit:result["validation"]=validation
        except (ValueError,KeyError,TypeError,IndexError) as error:
            reasons[gate]=str(error)
            if require_audit:result["validation"]={"valid":False,"reason":str(error)}
    gold_hash=_digest({str(i):bundle["gold"] for i,bundle in bundles.items()})
    try:
        proof=_read_json(directory/"preview.json")
        gates["preview_current"]=proof.get("revision")==revision and proof.get("gold_frozen_sha256")==gold_hash and Path(proof.get("database","")).is_file()
    except (OSError,ValueError,TypeError):pass
    try:
        _require_qa(directory,revision,bundles)
        gates["qa_current"]=gates["preview_current"]
    except (OSError,ValueError,TypeError) as error:reasons["qa_current"]=str(error)
    result["gates"]=gates
    result["counts"]={"requested":len(manifest["biographies"]),"pending":len(bundles),"skipped":len(result["skipped"])}
    result["coverage"]=[{"biography_id":identifier,"total_segments":len(bundle["source"]["segments"]),"assessed_segments":sum(segment.get("assessment") in ("supported","no_clear_topic","uncertain") and bool(str(segment.get("note","")).strip()) for segment in bundle["pilot"]["segments"]),"total_footnote_candidates":len(bundle["source"]["footnote_candidates"]),"assessed_footnote_candidates":sum(candidate.get("decision") in ("verified_marker","body_literal","ambiguous") and bool(str(candidate.get("reason","")).strip()) for candidate in bundle["footnotes"]["candidates"]), **({"total_words":len(bundle["diacritics"]["words"]),"checked_words":sum(word.get("status") in ("complete","uncertain","not_applicable") for word in bundle["diacritics"]["words"]),"uncertain_words":sum(word.get("status")=="uncertain" for word in bundle["diacritics"]["words"])} if "diacritics" in bundle else {})} for identifier,bundle in bundles.items()]
    result["phase"]="published" if gates["published"] else "published_conflict" if published else "qa_ready" if gates["qa_current"] and gates["audit_valid"] else "preview_ready" if gates["preview_current"] and gates["audit_valid"] else "audit_ready" if gates["audit_valid"] else "authoring_ready" if gates["authoring_valid"] else "authoring_required"
    result["remaining"]=[gate for gate in ("authoring_valid","audit_valid","preview_current","qa_current","published") if not gates[gate]]
    result["reasons"]=reasons
    return result


def validate_batch(batch, *, db_path=DEFAULT_DB, source_path=DEFAULT_SOURCE, require_audit=True):
    _,manifest,bundles=_load(batch)
    revision=batch_revision(manifest,bundles)
    with closing(_readonly(db_path)) as db:
        source=_source(db,source_path)
        published=db.execute("SELECT revision FROM curation_batches WHERE id=?",(manifest["batch_id"],)).fetchone() if _has_batches(db) else None
        if published and published[0]==revision:
            return {"valid":True,"status":"published","revision":revision,"biographies":[]}
        reports=_validate(db,source,manifest,bundles,require_audit=require_audit)
    return {"valid":True,"status":"validated","revision":revision,"biographies":reports,"skipped":[entry["source_ordinal"] for entry in manifest["biographies"] if entry["status"]=="skipped"]}


def _schema(db):
    for path in (ROOT/"backend/pilot_schema.sql",ROOT/"backend/curation_schema.sql"):
        for statement in path.read_text(encoding="utf-8").split(";"):
            if statement.strip():
                db.execute(statement)
    db.execute(READING_SCHEMA)
    from .diacritics import SCHEMA as DIACRITICS_SCHEMA
    db.execute(DIACRITICS_SCHEMA)


def _register_legacy(db):
    by_source={}
    for row in _legacy_rows(db):
        if not is_published_biography(db,row["id"]):
            by_source.setdefault((row["source_id"],row["sha256"]),[]).append(row)
    for (source_id,sha),rows in by_source.items():
        identifier=_legacy_id(sha)
        db.execute("INSERT OR IGNORE INTO curation_batches(id,source_id,source_sha256,start_ordinal,end_ordinal,revision,label,legacy,manifest_json) VALUES (?,?,?,?,?,?,?,1,?)",(identifier,source_id,sha,rows[0]["source_ordinal"],rows[-1]["source_ordinal"],"legacy-preserved","العينة الأصلية: التراجم الخمس الأولى",json.dumps({"registered_without_reimport":True})))
        for row in rows:
            db.execute("INSERT INTO curation_batch_members VALUES (?,?,?)",(identifier,row["id"],row["source_ordinal"]))


def _install_document(db,pilot,reading,revision):
    bio=validate_document(db,pilot)
    db.execute("INSERT INTO pilot_documents VALUES (?,?,?,?,?)",(bio["id"],bio["source_id"],pilot["source_sha256"],revision,json.dumps(pilot,ensure_ascii=False)))
    db.execute("INSERT INTO pilot_reading_overlays(biography_id,source_id,source_sha256,revision,payload) VALUES (?,?,?,?,?)",(bio["id"],bio["source_id"],pilot["source_sha256"],revision,json.dumps(reading,ensure_ascii=False)))
    from .normalization import normalize_arabic
    for segment in pilot["segments"]:
        db.execute("INSERT INTO pilot_coverage(segment_id,biography_id,assessment,note,reviewer) VALUES (?,?,?,?,?)",(segment["segment_id"],bio["id"],segment["assessment"],segment["note"],pilot["reviewer"]))
        for topic in segment.get("topics",[]):
            parent=db.execute("SELECT id FROM topics WHERE parent_id IS NULL AND name=?",(topic["category"],)).fetchone()
            if not parent: raise ValueError("باب موضوعي غير موجود")
            existing=db.execute("SELECT id FROM topics WHERE parent_id=? AND name=?",(parent[0],topic["topic"])).fetchone()
            tid=existing[0] if existing else db.execute("INSERT INTO topics(parent_id,name,search_terms_csv,dictionary_status) VALUES (?,?,?,?)",(parent[0],topic["topic"],",".join(topic.get("aliases",[])),"proposed_from_text")).lastrowid
            for alias in topic.get("aliases",[]):
                db.execute("INSERT OR IGNORE INTO pilot_topic_aliases VALUES (?,?)",(tid,alias))
            original=db.execute("SELECT id FROM topic_hits WHERE segment_id=? AND topic_id=?",(segment["segment_id"],tid)).fetchone()
            key="curation:"+_digest([pilot["source_sha256"],bio["source_ordinal"],segment["segment_id"],topic["category"],topic["topic"],topic["evidence_start"],topic["evidence_end"]])
            _insert_evidence(db,pilot,bio,segment["segment_id"],tid,original[0] if original else None,topic,"supported" if segment["assessment"]=="supported" else "uncertain",key)
        for old in segment.get("existing_hits",[]):
            if old["decision"] in ("keep","modify") and db.execute("SELECT 1 FROM pilot_evidence WHERE original_hit_id=?",(old["hit_id"],)).fetchone():continue
            hit=db.execute("SELECT h.*,s.source_char_start ca,s.source_char_end cb FROM topic_hits h JOIN segments s ON s.id=h.segment_id WHERE h.id=?",(old["hit_id"],)).fetchone()
            topic={"evidence_start":hit["source_char_start"],"evidence_end":hit["source_char_end"],"context_start":hit["ca"],"context_end":hit["cb"],"rationale":old["reason"],"relation_type":"passing","subject_scope":"general_context","stance":"reported"}
            key="curation:"+_digest([pilot["source_sha256"],bio["source_ordinal"],"old",hit["id"]])
            _insert_evidence(db,pilot,bio,segment["segment_id"],hit["topic_id"],hit["id"],topic,"exclude" if old["decision"]=="exclude" else "uncertain",key)
    # Only append new witness rows. Shared aliases may change an existing
    # topic's lookup vocabulary, so update that derived text without touching
    # evidence/review rows or IDs. Semantic vectors remain untouched.
    for row in db.execute("SELECT e.*,t.name,t.search_terms_csv FROM pilot_evidence e JOIN topics t ON t.id=e.topic_id"):
        aliases=" ".join(r[0] for r in db.execute("SELECT alias FROM pilot_topic_aliases WHERE topic_id=?",(row["topic_id"],)))
        text=normalize_arabic(row["name"]+" "+(row["search_terms_csv"] or "")+" "+aliases)
        if db.execute("SELECT 1 FROM pilot_topics_fts WHERE evidence_id=?",(row["id"],)).fetchone():
            db.execute("UPDATE pilot_topics_fts SET topic_text=? WHERE evidence_id=?",(text,row["id"]))
        else:
            db.execute("INSERT INTO pilot_topics_fts(evidence_id,topic_text,context_text) VALUES (?,?,?)",(row["id"],text,normalize_arabic(_literal(db,row["source_id"],row["context_char_start"],row["context_char_end"]))))


def _require_qa(directory,revision,bundles):
    try:
        proof=_read_json(directory/"preview.json")
        qa=_read_json(directory/"qa.json")
    except OSError as error:
        raise ValueError("يلزم إنشاء المعاينة وتقرير QA قبل النشر") from error
    gold_hash=_digest({str(i):b["gold"] for i,b in bundles.items()})
    if proof.get("revision")!=revision or proof.get("gold_frozen_sha256")!=gold_hash or not Path(proof.get("database","")).is_file():
        raise ValueError("يلزم إنشاء معاينة للنسخة الحالية وتجميد أسئلة التقييم قبل البحث")
    required_checks = QA_CHECKS if any("diacritics" in bundle for bundle in bundles.values()) else BASE_QA_CHECKS
    if qa.get("revision")!=revision or qa.get("gold_frozen_sha256")!=gold_hash or not str(qa.get("reviewer","")).strip() or any(qa.get("checks",{}).get(key) is not True for key in required_checks):
        raise ValueError("تقرير QA ناقص أو قديم أو يتضمن إخفاقًا")
    screenshots=qa.get("screenshots",[])
    if not isinstance(screenshots,list) or not screenshots or any(not isinstance(path,str) or not _screenshot_valid(Path(path) if Path(path).is_absolute() else directory/path) for path in screenshots):
        raise ValueError("يلزم إرفاق صور فحص فعلية للمعاينة")


def _publish(batch,db_path,source_path,*,preview=False):
    directory,manifest,bundles=_load(batch)
    revision=batch_revision(manifest,bundles)
    with closing(_readonly(db_path)) as db:
        source=_source(db,source_path)
        current=db.execute("SELECT revision FROM curation_batches WHERE id=?",(manifest["batch_id"],)).fetchone() if _has_batches(db) else None
        if current:
            if current[0]!=revision:raise ValueError("الدفعة منشورة بإصدار آخر؛ لا تستبدل المنشور تلقائيًا")
            return {"status":"unchanged","batch_id":manifest["batch_id"],"revision":revision}
        _validate(db,source,manifest,bundles)
    if not bundles:return {"status":"skipped","batch_id":manifest["batch_id"],"revision":revision}
    if not preview:
        _require_qa(directory,revision,bundles)
    backup=None if preview else backup_database(db_path)
    with closing(connect(db_path)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            source=_source(db,source_path)
            _validate(db,source,manifest,bundles)
            _schema(db)
            _register_legacy(db)
            db.execute("INSERT INTO curation_batches(id,source_id,source_sha256,start_ordinal,end_ordinal,revision,label,manifest_json) VALUES (?,?,?,?,?,?,?,?)",(manifest["batch_id"],source["id"],source["sha256"],manifest["start_ordinal"],manifest["end_ordinal"],revision,f"دفعة التراجم {manifest['start_ordinal']}–{manifest['end_ordinal']}",json.dumps(manifest,ensure_ascii=False)))
            for entry in manifest["biographies"]:
                if entry["status"]=="skipped":continue
                bundle=bundles[entry["id"]]
                _install_document(db,bundle["pilot"],bundle["reading"],revision)
                if manifest["version"] == VERSION:
                    from .diacritics import install_document as install_diacritics
                    install_diacritics(db, bundle["diacritics"], revision)
                db.execute("INSERT INTO curation_batch_members VALUES (?,?,?)",(manifest["batch_id"],entry["id"],entry["source_ordinal"]))
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return {"status":"preview" if preview else "published","batch_id":manifest["batch_id"],"revision":revision,"backup":str(backup) if backup else None,"biographies":len(bundles)}


def publish_batch(batch,*,db_path=DEFAULT_DB,source_path=DEFAULT_SOURCE):
    return _publish(batch,db_path,source_path)


def preview_batch(batch,*,db_path=DEFAULT_DB,source_path=DEFAULT_SOURCE,output=None):
    directory,manifest,bundles=_load(batch)
    validate_batch(batch,db_path=db_path,source_path=source_path)
    target=Path(output).resolve() if output else directory/"preview.sqlite3"
    if target==Path(db_path).resolve() or target.exists():
        raise ValueError("قاعدة المعاينة يجب أن تكون مسارًا جديدًا مستقلًا؛ لا تستبدل المصدر أو معاينة سابقة")
    target.parent.mkdir(parents=True,exist_ok=True)
    with closing(_readonly(db_path)) as source,closing(sqlite3.connect(target)) as destination:
        source.backup(destination)
    try:
        result=_publish(batch,target,source_path,preview=True)
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    gold_hash=_digest({str(i):b["gold"] for i,b in bundles.items()})
    proof={"revision":result["revision"],"gold_frozen_sha256":gold_hash,"database":str(target),"source_sha256":manifest["source_sha256"],"preview_only":True}
    _write_json(directory/"preview.json",proof)
    return {**result,**proof,"commands":[f"$env:SIYAR_DB = '{target}'",f"$env:SIYAR_SOURCE = '{Path(source_path).resolve()}'","python -m uvicorn backend.app:app --host 127.0.0.1 --port 8001"],"frontend_commands":["$env:VITE_API_TARGET = 'http://127.0.0.1:8001'","npm --prefix frontend run dev -- --port 5174"]}


def curation_router(db_path):
    router=APIRouter()
    @router.get("/api/curation/batches")
    def batches():
        with closing(_readonly(db_path)) as db:
            return list_batches(db)
    return router


def main():
    parser=argparse.ArgumentParser(description="دفعات تحريرية موثقة دون تعديل المصدر أو اعتماد الوكلاء بشريًا")
    sub=parser.add_subparsers(dest="command",required=True)
    for name in ("export","validate","status","preview","publish","upgrade"):
        command=sub.add_parser(name)
        command.add_argument("--db",type=Path,default=DEFAULT_DB)
        command.add_argument("--source",type=Path,default=DEFAULT_SOURCE)
        if name=="export":
            command.add_argument("--start",type=int,required=True);command.add_argument("--end",type=int,required=True);command.add_argument("--work-dir",type=Path,default=WORK_DIR)
        else:command.add_argument("--batch",type=Path,required=True)
        if name=="preview":command.add_argument("--output",type=Path)
        if name=="validate":command.add_argument("--authoring-only",action="store_true",help="التحقق قبل التدقيق المستقل؛ لا يسمح بالنشر")
    args=parser.parse_args()
    try:
        if args.command=="export":result=export_batch(args.start,args.end,db_path=args.db,source_path=args.source,work_dir=args.work_dir)
        elif args.command=="validate":result=validate_batch(args.batch,db_path=args.db,source_path=args.source,require_audit=not args.authoring_only)
        elif args.command=="status":result=status_batch(args.batch,db_path=args.db,source_path=args.source)
        elif args.command=="preview":result=preview_batch(args.batch,db_path=args.db,source_path=args.source,output=args.output)
        elif args.command=="upgrade":result=upgrade_batch(args.batch,db_path=args.db,source_path=args.source)
        else:result=publish_batch(args.batch,db_path=args.db,source_path=args.source)
    except (ValueError,KeyError,TypeError,IndexError,OSError,json.JSONDecodeError) as error:
        parser.exit(2,str(error)+"\n")
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
