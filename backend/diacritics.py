"""Additive, source-bound editorial vocalization. Never rewrites source text."""
from collections import Counter
from bisect import bisect_left, bisect_right
from copy import deepcopy
import json
import unicodedata

from .reading import build_reading_view

VERSION = "curation-diacritics-v1"
MARKS = frozenset(chr(value) for value in range(0x064B, 0x0653))
VOWELS = MARKS - {"ّ"}
NOTICE = "تشكيل تحريري أضافه الوكيل، وليس ضبطًا من النسخة الأصلية أو اعتمادًا بشريًا."
SCHEMA = """CREATE TABLE IF NOT EXISTS curation_diacritics (
    biography_id INTEGER PRIMARY KEY REFERENCES biographies(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    source_sha256 TEXT NOT NULL,
    revision TEXT NOT NULL,
    payload TEXT NOT NULL
)"""


def _arabic_letter(char):
    return unicodedata.category(char).startswith("L") and "ARABIC" in unicodedata.name(char, "")


def _mark(char):
    return unicodedata.category(char).startswith("M")


def word_spans(text, offset=0):
    """Visible source words, including numbers/Latin; technical markup excluded.

    Arabic prefixes stay in their word. Offsets are absolute Unicode points;
    every source mark belongs to its preceding base, without normalization.
    """
    rows = []
    view = build_reading_view(text, offset, starts_at_line_start=True)
    for block in view["blocks"]:
        for run in block["runs"]:
            if run["type"] != "text":
                continue
            chars = run["text"]
            index = 0
            while index < len(chars):
                if not chars[index].isalnum():
                    index += 1
                    continue
                start = index
                arabic = _arabic_letter(chars[index])
                index += 1
                while index < len(chars) and (_mark(chars[index]) or chars[index].isalnum() and _arabic_letter(chars[index]) == arabic):
                    index += 1
                rows.append({"start": run["source_char_start"] + start, "end": run["source_char_start"] + index,
                             "kind": "arabic" if arabic else "other"})
    return rows


def export_document(bio, source_sha256, reviewer=""):
    bio = dict(bio)
    words = []
    for span in word_spans(bio["full_text"], bio["source_char_start"]):
        words.append({"start": span["start"], "end": span["end"],
                      "status": "unexamined" if span["kind"] == "arabic" else "not_applicable",
                      "reason": "" if span["kind"] == "arabic" else "عدد أو رمز/لفظ غير عربي؛ محفوظ حرفيًا."})
    return {"version": VERSION, "biography_id": bio.get("id", bio.get("biography_id")),
            "source_sha256": source_sha256, "source_char_start": bio["source_char_start"],
            "source_char_end": bio["source_char_end"], "reviewer": reviewer,
            "insertions": [], "words": words}


def derive_insertions(original, vocalized, offset=0):
    """Compare an authored reading, retaining source marks in their exact order.

    The proposal supplies only additional marks. Its combining-mark order may
    differ, so additions are attached AFTER the untouched original cluster.
    Letters, punctuation, whitespace and every original mark must remain.
    This helper does not determine vowels or certify their linguistic truth.
    """
    rows, i, j = [], 0, 0
    while i < len(original):
        if j >= len(vocalized) or original[i] != vocalized[j]:
            raise ValueError(f"التشكيل المقابل يغير حرفًا أو مسافة عند {offset+i}")
        base = i
        i += 1
        j += 1
        a, b = i, j
        while i < len(original) and _mark(original[i]):
            i += 1
        while j < len(vocalized) and _mark(vocalized[j]):
            j += 1
        source_marks, proposal_marks = original[a:i], vocalized[b:j]
        pending = Counter(source_marks)
        new = []
        for char in proposal_marks:
            if pending[char]:
                pending[char] -= 1
            else:
                new.append(char)
        if any(pending.values()):
            raise ValueError(f"لا يجوز حذف حركة أصلية عند {offset+base}")
        if new:
            if not _arabic_letter(original[base]) or any(char not in MARKS for char in new):
                raise ValueError("المسموح إضافته هو الحركات الثماني على حرف عربي فقط")
            rows.append({"at": offset+i, "base_start": offset+base, "marks": "".join(new)})
    if j != len(vocalized):
        raise ValueError("التشكيل المقابل يضيف محتوى خارج الأصل")
    _validate_insertions(original, offset, rows)
    return rows


def _validate_insertions(text, offset, rows):
    if not isinstance(rows, list):
        raise ValueError("إضافات التشكيل ليست قائمة")
    visible = word_spans(text, offset)
    starts = [word["start"] for word in visible]
    previous = -1
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"at", "base_start", "marks"}:
            raise ValueError("عقد إضافة حركة غير صالح")
        at, base, marks = row["at"], row["base_start"], row["marks"]
        if type(at) is not int or type(base) is not int or not offset <= base < at <= offset+len(text) or at <= previous:
            raise ValueError("مواضع تشكيل خارج المصدر أو مكررة أو غير مرتبة")
        if not isinstance(marks, str) or not marks or any(char not in MARKS for char in marks):
            raise ValueError("المسموح هو الحركات العربية الثماني فقط")
        local = base-offset
        word_index = bisect_right(starts, base)-1
        if not _arabic_letter(text[local]) or word_index < 0 or base >= visible[word_index]["end"]:
            raise ValueError("حركة بلا حرف أساس عربي ظاهر")
        end = local+1
        while end < len(text) and _mark(text[end]):
            end += 1
        if at != offset+end:
            raise ValueError("الحركة لا تقع بعد مجموعة الحرف وحركاته الأصلية")
        combined = text[local+1:end]+marks
        if any(combined.count(mark) > 1 for mark in MARKS) or sum(char in VOWELS for char in combined) > 1:
            raise ValueError("حركة مكررة أو متعارضة مع ضبط المصدر")
        previous = at


def apply_insertions(text, insertions, offset=0):
    """Apply already validated insertions; all original codepoints survive."""
    chunks, cursor = [], 0
    for row in insertions:
        point = row["at"]-offset
        if not 0 <= point <= len(text):
            raise ValueError("إضافة خارج نطاق النص")
        chunks.extend((text[cursor:point], row["marks"]))
        cursor = point
    chunks.append(text[cursor:])
    return "".join(chunks)


def remove_insertions(vocalized, insertions, offset=0):
    """Remove the recorded additions ONLY, preserving original source marks."""
    chunks, cursor, added = [], 0, 0
    for row in insertions:
        point = row["at"]-offset+added
        if vocalized[point:point+len(row["marks"])] != row["marks"]:
            raise ValueError("النص لا يحمل الإضافة المسجلة في موضعها")
        chunks.append(vocalized[cursor:point])
        cursor = point+len(row["marks"])
        added += len(row["marks"])
    chunks.append(vocalized[cursor:])
    return "".join(chunks)


def validate_document(document, bio, reading, source_sha256, reviewer):
    bio = dict(bio)
    if not isinstance(document, dict) or document.get("version") != VERSION:
        raise ValueError("إصدار التشكيل غير صالح")
    identity = (bio.get("id", bio.get("biography_id")), source_sha256, bio["source_char_start"], bio["source_char_end"], reviewer)
    if tuple(document.get(key) for key in ("biography_id", "source_sha256", "source_char_start", "source_char_end", "reviewer")) != identity or not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("هوية التشكيل أو بصمته أو اسم المراجع لا يطابق الدفعة")
    text, offset = bio["full_text"], bio["source_char_start"]
    _validate_insertions(text, offset, document.get("insertions"))
    expected, words = word_spans(text, offset), document.get("words")
    if not isinstance(words, list) or len(words) != len(expected):
        raise ValueError("يلزم فحص كل كلمات النص مرة واحدة")
    counts = Counter()
    insertion_bases = [row["base_start"] for row in document["insertions"]]
    for actual, span in zip(words, expected):
        if not isinstance(actual, dict) or any(type(actual.get(key)) is not int for key in ("start", "end")) or (actual.get("start"), actual.get("end")) != (span["start"], span["end"]):
            raise ValueError("حدود الكلمات لا تطابق الأصل أو ترتيبها")
        status = actual.get("status")
        if status not in ("complete", "uncertain", "not_applicable"):
            raise ValueError("توجد كلمة لم يراجع تشكيلها")
        if status in ("uncertain", "not_applicable") and (not isinstance(actual.get("reason"), str) or not actual["reason"].strip()):
            raise ValueError("يلزم تعليل الموضع الملتبس أو غير المنطبق")
        additions = bisect_left(insertion_bases, span["end"]) > bisect_left(insertion_bases, span["start"])
        if status in ("uncertain", "not_applicable") and additions:
            raise ValueError("الموضع الملتبس أو غير المنطبق يجب أن يبقى على ضبط المصدر")
        original_word = text[span["start"]-offset:span["end"]-offset]
        if span["kind"] == "arabic" and status == "complete" and not additions and not any(char in MARKS for char in original_word):
            raise ValueError("كلمة معلنة مكتملة الضبط بلا أي حركة أصلية أو مضافة")
        if span["kind"] == "other" and status != "not_applicable":
            raise ValueError("الأعداد والألفاظ غير العربية تبقى حرفية")
        counts[status] += 1
    shaped = apply_insertions(text, document["insertions"], offset)
    if remove_insertions(shaped, document["insertions"], offset) != text:
        raise ValueError("تعذر إثبات حفظ جميع حروف الأصل وحركاته")
    return {"total_words": len(words), "checked_words": len(words), "complete_words": counts["complete"],
            "uncertain_words": counts["uncertain"], "not_applicable": counts["not_applicable"]}


def validate_editorial_headings(headings):
    for heading in headings:
        label = heading["label"]
        for word in word_spans(label):
            if word["kind"] == "arabic" and not any(char in MARKS for char in label[word["start"]:word["end"]]):
                raise ValueError("عنوان تنظيمي لم يكتمل فحص تشكيل كلماته")


def install_document(db, document, revision):
    row = db.execute("SELECT source_id FROM biographies WHERE id=?", (document["biography_id"],)).fetchone()
    db.execute("INSERT INTO curation_diacritics(biography_id,source_id,source_sha256,revision,payload) VALUES (?,?,?,?,?)",
               (document["biography_id"], row[0], document["source_sha256"], revision, json.dumps(document, ensure_ascii=False)))


def apply_overlay(db, presentation):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='curation_diacritics'").fetchone():
        return presentation
    row = db.execute("SELECT * FROM curation_diacritics WHERE biography_id=?", (presentation["biography_id"],)).fetchone()
    if row is None:
        return presentation
    try:
        bio = db.execute("SELECT b.*,s.sha256 FROM biographies b JOIN sources s ON s.id=b.source_id WHERE b.id=?", (presentation["biography_id"],)).fetchone()
        reading_row = db.execute("SELECT payload,revision FROM pilot_reading_overlays WHERE biography_id=?", (presentation["biography_id"],)).fetchone()
        if row["source_sha256"] != presentation["source_sha256"] or row["source_sha256"] != bio["sha256"] or row["source_id"] != bio["source_id"] or row["revision"] != presentation.get("presentation_revision") or row["revision"] != reading_row["revision"]:
            raise ValueError("تغيرت بصمة المصدر أو نسخة القراءة المرتبطة بالتشكيل")
        document = json.loads(row["payload"])
        reading = json.loads(reading_row["payload"])
        coverage = validate_document(document, bio, reading, bio["sha256"], reading["reviewer"])
        from .pilot import _literal
        if _literal(db, bio["source_id"], bio["source_char_start"], bio["source_char_end"]) != bio["full_text"]:
            raise ValueError("الأصل لا يطابق مخزن النصوص")
        uncertainties = [{"start": word["start"], "end": word["end"], "reason": word["reason"]} for word in document["words"] if word["status"] == "uncertain"]
        notice = NOTICE + (f" توجد {len(uncertainties)} كلمة بقيت على ضبط المصدر لالتباس موثق." if uncertainties else "")
        presentation["vocalized_plain_text"] = _vocalized_plain_text(presentation, document["insertions"])+"\n\n"+notice
        presentation["diacritics"] = {"revision": row["revision"], "status": "partial" if uncertainties else "ready",
                                      "insertions": document["insertions"], "coverage": coverage,
                                      "uncertainties": uncertainties, "notice": notice}
    except (ValueError, KeyError, TypeError, IndexError, AttributeError, json.JSONDecodeError):
        presentation["diacritics"] = {"revision": row["revision"], "status": "invalid", "insertions": [], "uncertainties": [],
                                      "notice": "تعذر التحقق من طبقة التشكيل؛ عُرض ضبط المصدر دون الحركات المضافة."}
    return presentation


def _vocalized_plain_text(presentation, additions):
    """Reuse the proven plain formatter with temporary uniquely mapped markers.

    Insert additions only in source-owned output characters. Appendix contexts
    and all original markers stay literal; editorial headings are authored.
    """
    from .pilot import presentation_plain_text
    # Formatting's per-run offset arithmetic must continue to use original
    # lengths, so substitute same-length private placeholders at base points,
    # format, then expand the placeholders. Choose unused scalars to avoid any
    # collision even when a source happens to contain private-use characters.
    view, annotations = deepcopy(presentation["reading_view"]), deepcopy(presentation["annotations"])
    used = set(presentation["plain_text"])
    replacement = {}
    scalar = 0xF0000
    bases = [row["base_start"] for row in additions]
    for block in view["blocks"]:
        for run in block["runs"]:
            if run["type"] != "text":
                continue
            chars = list(run["text"])
            left = bisect_left(bases, run["source_char_start"])
            right = bisect_left(bases, run["source_char_end"])
            for addition in additions[left:right]:
                if not run["source_char_start"] <= addition["base_start"] < addition["at"] <= run["source_char_end"]:
                    continue
                position = addition["at"]-run["source_char_start"]-1
                while chr(scalar) in used:
                    scalar += 1
                token = chr(scalar)
                scalar += 1
                used.add(token)
                replacement[token] = chars[position]+addition["marks"]
                chars[position] = token
            run["text"] = "".join(chars)
    result = presentation_plain_text(view, annotations)
    return "".join(replacement.get(char, char) for char in result)
