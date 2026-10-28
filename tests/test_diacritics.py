"""Insertion-only mechanics; linguistic examples are explicit synthetic fixtures."""
from copy import deepcopy
import pytest

from backend.diacritics import (apply_insertions, derive_insertions, export_document,
                               remove_insertions, validate_document, word_spans,
                               validate_editorial_headings, _vocalized_plain_text)
from backend.reading import build_reading_view
from backend.pilot import presentation_plain_text

RAW = "### $ رَاوٍ 🧪\r\n# قَالَ رَاوٍ: عُلم خبرٌ (12). PageV01P005\r\n"
SHAPED = "### $ رَاوٍ 🧪\r\n# قَالَ رَاوٍ: عُلِمَ خَبَرٌ (12). PageV01P005\r\n"
OFFSET = 100


def fixture():
    bio = {"id": 8, "full_text": RAW, "source_char_start": OFFSET, "source_char_end": OFFSET+len(RAW)}
    doc = export_document(bio, "source-sha", "مراجع مصطنع")
    for word in doc["words"]:
        if word["status"] == "unexamined":
            word["status"] = "complete"
    doc["insertions"] = derive_insertions(RAW, SHAPED, OFFSET)
    return bio, doc


def validate(bio, doc):
    return validate_document(doc, bio, {}, "source-sha", "مراجع مصطنع")


def test_roundtrip_preserves_original_diacritics_unicode_crlf_and_technical_markers():
    bio, doc = fixture()
    shaped = apply_insertions(RAW, doc["insertions"], OFFSET)
    assert shaped == SHAPED
    assert remove_insertions(shaped, doc["insertions"], OFFSET) == RAW
    assert shaped.count("\r\n") == RAW.count("\r\n")
    coverage = validate(bio, doc)
    assert coverage["total_words"] == len(doc["words"])
    assert coverage["checked_words"] == coverage["total_words"]
    assert coverage["uncertain_words"] == 0


def test_proposal_combining_order_cannot_reorder_or_delete_source_marks():
    raw, proposal = "نّ", "نَّ"
    rows = derive_insertions(raw, proposal)
    assert apply_insertions(raw, rows) == "نَّ"
    assert remove_insertions(apply_insertions(raw, rows), rows) == raw
    with pytest.raises(ValueError, match="حذف حركة"):
        derive_insertions("نَّ", "نّ")


@pytest.mark.parametrize("original,proposed", [("علم", "عالم"), ("خبر.", "خبر!"), ("قال رجل", "قال  رجل"), ("ه", "هِـ"), ("ا", "إ"), ("ا", "أ")])
def test_editorial_proposal_cannot_change_letters_spaces_punctuation_or_add_hamza(original, proposed):
    with pytest.raises(ValueError):
        derive_insertions(original, proposed)


@pytest.mark.parametrize("marks", ["ََ", "ِ", "ْ", "ٓ", "x"])
def test_duplicate_conflicting_and_nonpermitted_marks_rejected(marks):
    bio, doc = fixture()
    at = RAW.index("عُ")
    doc["insertions"] = [{"base_start": OFFSET+at, "at": OFFSET+at+2, "marks": marks}]
    with pytest.raises(ValueError):
        validate(bio, doc)


def test_word_coverage_excludes_pages_markup_and_records_numbers():
    spans = word_spans(RAW, OFFSET)
    words = [RAW[row["start"]-OFFSET:row["end"]-OFFSET] for row in spans]
    assert words == ["رَاوٍ", "قَالَ", "رَاوٍ", "عُلم", "خبرٌ", "12"]
    assert spans[-1]["kind"] == "other"
    bio, doc = fixture()
    doc["words"].pop()
    with pytest.raises(ValueError, match="كل كلمات"):
        validate(bio, doc)


def test_uncertain_word_has_reason_and_retains_original_marks_only():
    bio, doc = fixture()
    word = next(w for w in doc["words"] if w["start"] == OFFSET+RAW.index("عُلم"))
    word.update(status="uncertain", reason="الفاعل والمفعول لا يحسمهما السياق المصطنع")
    with pytest.raises(ValueError, match="ضبط المصدر"):
        validate(bio, doc)
    doc["insertions"] = [r for r in doc["insertions"] if not word["start"] <= r["base_start"] < word["end"]]
    assert validate(bio, doc)["uncertain_words"] == 1
    word["reason"] = ""
    with pytest.raises(ValueError, match="تعليل"):
        validate(bio, doc)


def test_unexamined_words_wrong_source_and_duplicate_positions_fail():
    bio, doc = fixture()
    original = deepcopy(doc)
    doc["words"][0]["status"] = "unexamined"
    with pytest.raises(ValueError, match="لم يراجع"):
        validate(bio, doc)
    doc = deepcopy(original)
    doc["source_sha256"] = "different"
    with pytest.raises(ValueError, match="بصمته"):
        validate(bio, doc)
    doc = deepcopy(original)
    doc["insertions"].append(doc["insertions"][-1])
    with pytest.raises(ValueError, match="مكررة"):
        validate(bio, doc)


def test_copy_formatter_preserves_offset_slices_and_literal_appendix_contexts():
    bio, doc = fixture()
    start = OFFSET+RAW.index("(12)")
    annotations = {"headings": [{"at": OFFSET+RAW.index("# قَالَ"), "label": "خَبَرٌ تَجْرِيبِيٌّ"}],
                   "footnotes": [{"start": start, "end": start+4, "raw": "(12)", "context": "عُلم خبرٌ (12)."}],
                   "quotes": [], "poetry": [], "lists": [], "bold": [], "names": [], "keywords": []}
    view = build_reading_view(RAW, OFFSET, starts_at_line_start=True)
    plain = presentation_plain_text(view, annotations)
    p = {"reading_view": view, "annotations": annotations, "plain_text": plain}
    out = _vocalized_plain_text(p, doc["insertions"])
    assert "عُلِمَ خَبَرٌ" in out
    assert "عُلم خبرٌ (12)." in out  # Appendix evidence stays literal.
    assert "[عنوان تنظيمي: خَبَرٌ تَجْرِيبِيٌّ]" in out
    assert p["plain_text"] == plain
    assert p["reading_view"] == view
    assert not any(0xF0000 <= ord(char) <= 0x10FFFF for char in out)


def test_editorial_headings_are_checked_word_by_word():
    validate_editorial_headings([{"label": "خَبَرُ الرِّوَايَةِ"}])
    with pytest.raises(ValueError, match="عنوان تنظيمي"):
        validate_editorial_headings([{"label": "خَبَرُ الرواية"}])


def test_a_fully_unvocalized_word_cannot_be_declared_complete():
    text = "# محمد عالم"
    bio = {"id": 9, "full_text": text, "source_char_start": 0, "source_char_end": len(text)}
    doc = export_document(bio, "source-sha", "مراجع مصطنع")
    for word in doc["words"]:
        word["status"] = "complete"
    with pytest.raises(ValueError, match="بلا أي حركة"):
        validate(bio, doc)


@pytest.mark.parametrize("reason", [None, 42])
def test_null_or_nontextual_explanation_cannot_publish_an_uncertain_word(reason):
    bio, doc = fixture()
    doc["words"][0].update(status="uncertain", reason=reason)
    with pytest.raises(ValueError, match="تعليل"):
        validate(bio, doc)
