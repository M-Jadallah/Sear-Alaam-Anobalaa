"""Independent, source-mapped reading revisions for the five-biography pilot.

This module never changes canonical segments, topic findings or human reviews.
Authored paragraph boundaries and criticism spans are presentation annotations.
"""
from __future__ import annotations

from bisect import bisect_right
from contextlib import closing
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import unicodedata

from .db import DEFAULT_DB, ROOT, connect
from .reading import build_reading_view, _plain_and_map

VERSION = "pilot-reading-v2"
DATA_DIR = ROOT / "reference" / "pilot-reading"
SCHEMA = """CREATE TABLE IF NOT EXISTS pilot_reading_overlays (
    biography_id INTEGER PRIMARY KEY REFERENCES biographies(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    source_sha256 TEXT NOT NULL,
    revision TEXT NOT NULL,
    payload TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)"""


def _digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _has_table(db):
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='pilot_reading_overlays'").fetchone() is not None


def effective_annotations(annotations, document):
    """Move only authored headings when an old position split a sentence."""
    result = deepcopy(annotations)
    seen = set()
    for move in document.get("heading_overrides", []):
        old, new = move.get("from_at"), move.get("at")
        if type(old) is not int or type(new) is not int or old in seen or not document["source_char_start"] <= new < document["source_char_end"]:
            raise ValueError("نقل عنوان تنظيمي غير صالح")
        targets = [heading for heading in result["headings"] if heading["at"] == old]
        if len(targets) != 1:
            raise ValueError("موضع العنوان التنظيمي السابق غير محدد")
        targets[0]["at"] = new
        seen.add(old)
    result["headings"] = sorted(result.get("headings", []), key=lambda heading: heading["at"])
    # A published reading revision may vocalize an existing editorial label.
    # The label's wording stays intact; only the eight allowed marks may grow.
    labelled = set()
    for override in document.get("heading_labels", []):
        if not isinstance(override, dict) or set(override) != {"at", "label"} or type(override["at"]) is not int or not isinstance(override["label"], str) or override["at"] in labelled:
            raise ValueError("تشكيل عنوان تنظيمي غير صالح أو مكرر")
        targets = [heading for heading in result["headings"] if heading["at"] == override["at"]]
        if len(targets) != 1:
            raise ValueError("العنوان التنظيمي المراد تشكيله غير موجود")
        from .diacritics import derive_insertions
        derive_insertions(targets[0]["label"], override["label"])
        targets[0]["label"] = override["label"]
        labelled.add(override["at"])
    return result


def _hard_boundaries(view, annotations):
    """Only structural boundaries; quote edges must not break prose."""
    boundaries = {view["source_char_start"], view["source_char_end"]}
    for block in view["blocks"]:
        if block["type"] == "heading":
            boundaries.update((block["source_char_start"], block["source_char_end"]))
    boundaries.update(heading["at"] for heading in annotations.get("headings", []))
    for poetry in annotations.get("poetry", []):
        boundaries.update((poetry["start"], poetry["end"]))
        for block in view["blocks"]:
            if poetry["start"] < block["source_char_start"] < poetry["end"]:
                boundaries.add(block["source_char_start"])
    for listing in annotations.get("lists", []):
        boundaries.update((listing["start"], listing["end"]))
        boundaries.update(item["start"] for item in listing["items"])
    return boundaries


def validate_paragraph_notes(document, *, required=False):
    """Validate source-bound editorial reasons, never infer contextual quality."""
    if "paragraph_notes" not in document and not required:
        return
    notes = document.get("paragraph_notes")
    groups = document.get("paragraph_groups", [])
    if not isinstance(notes, list) or len(notes) != len(groups):
        raise ValueError("يلزم سبب سياقي لكل مجموعة فقرات")
    lo, hi = document["source_char_start"], document["source_char_end"]
    for group, note in zip(groups, notes):
        if not isinstance(note, dict) or (note.get("start"), note.get("end")) != (group["start"], group["end"]):
            raise ValueError("أسباب الفقرات لا تطابق حدودها وترتيبها")
        ca, cb = note.get("context_start"), note.get("context_end")
        if type(ca) is not int or type(cb) is not int or not lo <= ca <= group["start"] < group["end"] <= cb <= hi:
            raise ValueError("سياق تعليل الفقرة خارج الترجمة أو لا يحتويها")
        if not isinstance(note.get("reason"), str) or not note["reason"].strip():
            raise ValueError("تعليل سياق الفقرة مطلوب")


def validate_reading_document(db, document, *, base_document=None):
    """Validate complete ordered coverage and literal, contextual criticism."""
    if document.get("version") != VERSION or not str(document.get("reviewer", "")).strip():
        raise ValueError("إصدار القراءة أو اسم الفاحص غير صالح")
    biography_id = document.get("biography_id")
    if type(biography_id) is not int:
        raise ValueError("معرّف الترجمة غير صالح")
    eligible = {row[0] for row in db.execute("SELECT id FROM biographies ORDER BY source_id,source_char_start LIMIT 5")}
    if biography_id not in eligible and base_document is None:
        from .curation import is_published_biography
        if not is_published_biography(db, biography_id):
            raise ValueError("نسخة القراءة خارج أول خمس تراجم أو دفعة منشورة")
    bio = db.execute("SELECT b.*,s.sha256 FROM biographies b JOIN sources s ON s.id=b.source_id WHERE b.id=?", (biography_id,)).fetchone()
    if bio is None or bio["sha256"] != document.get("source_sha256"):
        raise ValueError("بصمة مصدر القراءة لا تطابق الترجمة")
    lo, hi = bio["source_char_start"], bio["source_char_end"]
    if (document.get("source_char_start"), document.get("source_char_end")) != (lo, hi):
        raise ValueError("حدود نسخة القراءة لا تطابق الترجمة")
    # A full source hash check is cached by the existing immutable-source helper.
    from .pilot import _literal, has_pilot
    if _literal(db, bio["source_id"], lo, hi) != bio["full_text"]:
        raise ValueError("نص الترجمة لا يطابق المصدر الحرفي")
    if not has_pilot(db) and base_document is None:
        raise ValueError("يلزم إعداد تجربة التراجم الخمس قبل نسخة القراءة")
    if base_document is None:
        pilot = db.execute("SELECT payload,source_sha256 FROM pilot_documents WHERE biography_id=?", (biography_id,)).fetchone()
        if not pilot or pilot["source_sha256"] != bio["sha256"]:
            raise ValueError("طبقة تنسيق التجربة غائبة أو قديمة")
        base_document = json.loads(pilot["payload"])
    if base_document.get("biography_id") != biography_id or base_document.get("source_sha256") != bio["sha256"]:
        raise ValueError("طبقة العرض لا تطابق نسخة القراءة")
    annotations = effective_annotations(base_document["presentation"], document)
    groups = document.get("paragraph_groups")
    if not isinstance(groups, list) or not groups:
        raise ValueError("يلزم تقسيم الترجمة كاملة إلى مجموعات فقرات")
    cursor, boundaries = lo, {lo}
    for group in groups:
        if not isinstance(group, dict):
            raise ValueError("مجموعة فقرة غير صالحة")
        a, b = group.get("start"), group.get("end")
        if type(a) is not int or type(b) is not int or a != cursor or not a < b <= hi:
            raise ValueError("فجوة أو تداخل أو ترتيب غير صالح في الفقرات")
        cursor = b
        boundaries.add(b)
    if cursor != hi:
        raise ValueError("تغطية الفقرات غير كاملة")
    validate_paragraph_notes(document)
    view = build_reading_view(bio["full_text"], lo, starts_at_line_start=True)
    if not _hard_boundaries(view, annotations).issubset(boundaries):
        raise ValueError("فقرة تعبر حد عنوان أو قائمة أو بيت شعر")
    if "paragraph_notes" in document:
        cuts = boundaries | _hard_boundaries(view, annotations)
        for listing in annotations.get("lists", []):
            cuts.update(item["end"] for item in listing["items"])
        for cut in cuts:
            local = cut - lo
            if 0 < local < len(bio["full_text"]) and unicodedata.category(bio["full_text"][local]).startswith("M"):
                raise ValueError("حد بنيوي يقطع حرفًا وحركاته الأصلية")
    for block in view["blocks"]:
        for run in block["runs"]:
            if run["type"] not in ("text", "virtual_space") and any(run["source_char_start"] < cut < run["source_char_end"] for cut in boundaries):
                raise ValueError("حد فقرة يقطع علامة تقنية أو إحالة")
    previous_end = lo
    criticism = document.get("criticism", [])
    if not isinstance(criticism, list):
        raise ValueError("تعليقات التحقيق غير صالحة")
    for span in criticism:
        if not isinstance(span, dict):
            raise ValueError("تعليق تحقيق غير صالح")
        a, b, ca, cb = (span.get(key) for key in ("start", "end", "context_start", "context_end"))
        if any(type(value) is not int for value in (a, b, ca, cb)) or not lo <= ca <= a < b <= cb <= hi:
            raise ValueError("نطاق التحقيق أو سياقه خارج الترجمة")
        if a < previous_end:
            raise ValueError("تعليقات التحقيق متداخلة أو غير مرتبة")
        previous_end = b
        if span.get("kind") not in ("hadith", "isnad", "narrator", "report") or not str(span.get("reason", "")).strip():
            raise ValueError("تعليق تحقيق بلا نوع أو سبب صالح")
        local = build_reading_view(bio["full_text"][a-lo:b-lo], a)
        if not local["plain_text"].strip():
            raise ValueError("تعليق تحقيق بلا نص مرئي")
    return bio


def prepare_reading(db_path=DEFAULT_DB, documents=None):
    """Validate all five, back up once, publish atomically; do not reimport."""
    documents = documents if documents is not None else [json.loads(path.read_text(encoding="utf-8")) for path in sorted(DATA_DIR.glob("biography-*.json"))]
    documents = sorted(documents, key=lambda document: document["biography_id"])
    if not documents:
        raise ValueError("لا توجد ملفات نسخة القراءة")
    revision = _digest(documents)
    with closing(connect(db_path)) as db:
        from .curation import has_new_batches
        if has_new_batches(db):
            raise ValueError("توجد دفعات تحريرية منشورة؛ استخدم backend.curation دون استبدالها")
        eligible = [row[0] for row in db.execute("SELECT id FROM biographies ORDER BY source_id,source_char_start LIMIT 5")]
        if [doc["biography_id"] for doc in documents] != sorted(eligible):
            raise ValueError("يلزم إعداد أول خمس تراجم معًا دون تكرار")
        for document in documents:
            validate_reading_document(db, document)
        if _has_table(db):
            previous = {row["biography_id"]: row["revision"] for row in db.execute("SELECT biography_id,revision FROM pilot_reading_overlays")}
            if previous == {doc["biography_id"]: revision for doc in documents}:
                return {"status": "unchanged", "presentation_revision": revision}
    from .pilot import backup_database
    backup = backup_database(db_path)
    with closing(connect(db_path)) as db:
        # DDL and all rows share one transaction; a failure preserves the old view.
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute(SCHEMA)
            for document in documents:
                bio = validate_reading_document(db, document)
                db.execute("""INSERT INTO pilot_reading_overlays(biography_id,source_id,source_sha256,revision,payload)
                    VALUES (?,?,?,?,?) ON CONFLICT(biography_id) DO UPDATE SET source_id=excluded.source_id,
                    source_sha256=excluded.source_sha256,revision=excluded.revision,payload=excluded.payload,
                    updated_at=CURRENT_TIMESTAMP""", (bio["id"], bio["source_id"], bio["sha256"], revision, json.dumps(document, ensure_ascii=False)))
            db.commit()
        except BaseException:
            db.rollback()
            raise
    return {"status": "ready", "presentation_revision": revision, "backup": str(backup),
            "biographies": len(documents), "paragraph_groups": sum(len(doc["paragraph_groups"]) for doc in documents),
            "criticism": sum(len(doc.get("criticism", [])) for doc in documents)}


def grouped_reading_view(view, groups):
    """Repartition literal runs, inserting only zero-width formatting spaces."""
    result = deepcopy(view)
    blocks = []
    ends = [group["end"] for group in groups]
    for index, group in enumerate(groups):
        blocks.append({"id": f"reading-paragraph-{index + 1}", "type": "paragraph",
                       "source_char_start": group["start"], "source_char_end": group["end"], "runs": []})
    for original in view["blocks"]:
        if original["type"] == "heading":
            index = bisect_right(ends, original["source_char_start"])
            blocks[index].update(type="heading", level=original.get("level", 1))
        for index, group in enumerate(groups):
            a, b = group["start"], group["end"]
            if original["source_char_start"] >= b or original["source_char_end"] <= a:
                continue
            clipped = []
            for run in original["runs"]:
                ra, rb = run["source_char_start"], run["source_char_end"]
                if run["type"] == "virtual_space":
                    if a <= ra < b:
                        clipped.append(dict(run))
                elif ra < b and rb > a:
                    left, right = max(a, ra), min(b, rb)
                    piece = dict(run, text=run["text"][left-ra:right-ra], source_char_start=left, source_char_end=right)
                    if run["type"] != "text" and (left != ra or right != rb):
                        raise ValueError("حد فقرة يقطع علامة تقنية أو إحالة")
                    clipped.append(piece)
            runs = blocks[index]["runs"]
            previous = next((r["text"] for r in reversed(runs) if r["type"] in ("text", "virtual_space") and r["text"]), "")
            following = next((r["text"] for r in clipped if r["type"] in ("text", "virtual_space") and r["text"]), "")
            if previous and following and not previous[-1].isspace() and not following[0].isspace():
                at = max(a, original["source_char_start"])
                runs.append({"type": "virtual_space", "text": " ", "source_char_start": at,
                             "source_char_end": at, "reason": "paragraph_join"})
            runs.extend(clipped)
    result["blocks"] = blocks
    result["renderer_version"] = VERSION
    spans = [(part["source_char_start"], part["source_char_end"]) for part in result["hidden"]]
    spans.extend((run["source_char_start"], run["source_char_end"]) for block in blocks for run in block["runs"] if run["type"] != "virtual_space")
    cursor = result["source_char_start"]
    for a, b in sorted(spans):
        if a != cursor or b <= a:
            raise ValueError("تغطية نسخة القراءة بها فجوة أو تكرار")
        cursor = b
    if cursor != result["source_char_end"]:
        raise ValueError("تغطية نسخة القراءة غير مكتملة")
    result["coverage"] = {"source_chars": result["source_char_end"] - result["source_char_start"],
                          "accounted_chars": sum(b-a for a,b in spans), "complete": True}
    result["plain_text"] = _plain_and_map(result, with_map=False)[0]
    return result


def apply_reading_overlay(db, presentation):
    """Enrich only the presentation endpoint, preserving its topic revision."""
    if not _has_table(db):
        return presentation
    row = db.execute("SELECT * FROM pilot_reading_overlays WHERE biography_id=?", (presentation["biography_id"],)).fetchone()
    if not row:
        return presentation
    if row["source_sha256"] != presentation["source_sha256"]:
        presentation["warning"] = "تغير مصدر نسخة تنسيق الفقرات؛ أوقف تطبيقها وعُرض التنسيق السابق."
        return presentation
    try:
        document = json.loads(row["payload"])
        # Read-time validation also protects changed base presentation boundaries.
        validate_reading_document(db, document)
        view = grouped_reading_view(presentation["reading_view"], document["paragraph_groups"])
        annotations = effective_annotations(presentation["annotations"], document)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        presentation["warning"] = "تعذر التحقق من حدود نسخة القراءة؛ عُرض التنسيق السابق دون تطبيق التعليقات الجديدة."
        return presentation
    from .pilot import presentation_plain_text
    presentation.update(presentation_revision=row["revision"], paragraph_groups=document["paragraph_groups"], annotations=annotations,
                        criticism=document.get("criticism", []), reading_view=view,
                        plain_text=presentation_plain_text(view, annotations))
    if "paragraph_notes" in document:
        presentation["paragraph_notes"] = document["paragraph_notes"]
    from .diacritics import apply_overlay
    return apply_overlay(db, presentation)
