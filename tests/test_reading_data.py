"""Independent corpus checks; the installed library is opened read-only only.

These tests validate the authored reading files before or after publication.
They never prepare overlays, migrate the library, or record human decisions.
"""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import pytest

from backend.pilot import presentation_plain_text
from backend.pilot_reading import (
    VERSION, apply_reading_overlay, effective_annotations,
    grouped_reading_view, validate_reading_document,
)
from backend.reading import build_reading_view

ROOT = Path(__file__).resolve().parents[1]
PINNED_SHA = "7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0"


@pytest.fixture(scope="module")
def reading_corpus():
    source_path, db_path = ROOT / "data/source.mARkdown", ROOT / "data/library.sqlite3"
    paths = [ROOT / "reference/pilot-reading" / f"biography-{bid}.json" for bid in range(1, 6)]
    if not source_path.exists() or not db_path.exists() or not all(path.exists() for path in paths):
        pytest.skip("The real source and authored five-biography reading experiment are not installed")
    raw = source_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PINNED_SHA
    source = raw.decode("utf-8")
    db = sqlite3.connect(db_path.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    pilots = [json.loads((ROOT / "reference/pilot" / f"biography-{bid}.json").read_text(encoding="utf-8")) for bid in range(1, 6)]
    try:
        yield db, source, documents, pilots
    finally:
        assert db.total_changes == 0
        db.close()
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == PINNED_SHA


def all_runs(view):
    return [run for block in view["blocks"] for run in block["runs"]]


def selected(document, start, end):
    return any(span["start"] <= start and end <= span["end"] for span in document["criticism"])


def literal_position(source, document, literal):
    start = source.index(literal, document["source_char_start"], document["source_char_end"])
    return start, start + len(literal)


def test_real_documents_validate_and_partition_every_source_character(reading_corpus):
    db, source, documents, _ = reading_corpus
    assert [doc["biography_id"] for doc in documents] == [1, 2, 3, 4, 5]
    total_chars = 0
    for doc in documents:
        bio = validate_reading_document(db, doc)
        assert doc["version"] == VERSION and doc["source_sha256"] == PINNED_SHA
        assert doc["reviewer"] == "مراجعة نصية بواسطة وكيل"
        groups, start, end = doc["paragraph_groups"], doc["source_char_start"], doc["source_char_end"]
        assert groups[0]["start"] == start and groups[-1]["end"] == end
        assert all(left["end"] == right["start"] for left, right in zip(groups, groups[1:]))
        assert "".join(source[g["start"]:g["end"]] for g in groups) == bio["full_text"] == source[start:end]
        total_chars += end - start
    assert total_chars == 98058


def test_grouped_view_preserves_literal_runs_unicode_positions_and_all_pages(reading_corpus):
    _, source, documents, _ = reading_corpus
    for doc in documents:
        start, end = doc["source_char_start"], doc["source_char_end"]
        base = build_reading_view(source[start:end], start, starts_at_line_start=True)
        pristine = deepcopy(base)
        grouped = grouped_reading_view(base, doc["paragraph_groups"])
        assert base == pristine
        assert grouped["coverage"] == base["coverage"]
        assert grouped["coverage"]["complete"]
        assert len(grouped["blocks"]) < len(base["blocks"])
        assert grouped["hidden"] == base["hidden"]
        assert "".join(r["text"] for r in all_runs(grouped) if r["type"] != "virtual_space") == "".join(r["text"] for r in all_runs(base) if r["type"] != "virtual_space")
        assert [(r["text"], r["source_char_start"], r["source_char_end"]) for r in all_runs(grouped) if r["type"] == "page"] == [(r["text"], r["source_char_start"], r["source_char_end"]) for r in all_runs(base) if r["type"] == "page"]
        for run in all_runs(grouped):
            a, b = run["source_char_start"], run["source_char_end"]
            if run["type"] == "virtual_space":
                assert a == b and run["text"] == " "
            else:
                assert run["text"] == source[a:b]


def test_every_heading_poem_line_and_list_item_keeps_its_structural_boundary(reading_corpus):
    _, source, documents, pilots = reading_corpus
    for doc, pilot in zip(documents, pilots):
        groups = doc["paragraph_groups"]
        cuts = {group["start"] for group in groups} | {groups[-1]["end"]}
        base = build_reading_view(source[doc["source_char_start"]:doc["source_char_end"]], doc["source_char_start"], starts_at_line_start=True)
        annotations = effective_annotations(pilot["presentation"], doc)
        assert base["blocks"][0]["source_char_end"] in cuts
        assert {h["at"] for h in annotations["headings"]} <= cuts
        for poem in annotations["poetry"]:
            assert {poem["start"], poem["end"]} <= cuts
            for block in base["blocks"]:
                if poem["start"] < block["source_char_start"] < poem["end"]:
                    assert block["source_char_start"] in cuts
        for listing in annotations["lists"]:
            assert {listing["start"], listing["end"]} | {item["start"] for item in listing["items"]} <= cuts


def test_known_continuations_and_isnads_are_connected_without_joining_new_reports(reading_corpus):
    _, source, documents, _ = reading_corpus
    # Page breaks and # prefixes split these sentences in the imported text.
    cases = [
        (1, "وأشار به يوم", "السقيفة؛ لكمال أهليته"),
        (1, "إنه لم", "يكن نبي بعد نوح"),
        (2, "قال رسول الله -صلى الله عليه وسلم-: (من", "أراد أن ينظر إلى شهيد"),
        (4, "أخبرنا أبو الحسين علي بن محمد", "أنبأنا عبد الله بن عمر"),
        (4, "فهو وغيره منام", "له -ولله الحمد- قدميه"),
        (5, "قال عمر لسعد: قد", "شكوك في كل شيء"),
    ]
    for bid, left, right in cases:
        doc = documents[bid - 1]
        a, _ = literal_position(source, doc, left)
        b, e = literal_position(source, doc, right)
        assert a < b
        assert any(group["start"] <= a and e <= group["end"] for group in doc["paragraph_groups"])
    # Explicitly independent following isnads remain different prose units.
    for bid, new_report in [(1, "# قال ابن سعد في (الطبقات)"), (2, "# أخبرنا أبو المعالي بن أبي عصرون"), (3, "# أخبرنا أبو سعيد سنقر"), (5, "# ابن وهب: حدثني أسامة بن زيد الليثي")]:
        doc = documents[bid - 1]
        a, _ = literal_position(source, doc, new_report)
        assert a in {group["start"] for group in doc["paragraph_groups"]}


def test_criticism_preserves_negation_attribution_and_qualifying_dream_context(reading_corpus):
    _, source, documents, _ = reading_corpus
    required = {
        1: ["وهذا حديث حسن غريب", "هذا مرسل (2) .", "إن كان هاجر إليها"],
        2: ["رواته ثقات.", "إسناده منقطع، مع ضعف الكديمي.", "وشذ أبو يعلى الموصلي"],
        3: ["لم يقل\n~~أبو يعلى متعمدا.", "-ولا إخاله متهما علينا-", "في إسناده إرسال، وفي لفظه نكارة", "ولا نحكم عليهم بخلود النار، بل نقف."],
        4: ["قال النسائي: الصواب", "صححه الترمذي", "وقال: حديث منكر.", "إسناده واه.", "لم يحتج به النسائي.", "والحسن مدلس، لم يسمع من المغيرة.", "فهو وغيره منام، والمنام له تأويل", "فلو تأخر عبد الرحمن"],
        5: ["إسناده منقطع.", "وهو أصح.", "مينا: متروك (2) .", "والأول هو\n~~الصحيح."],
    }
    for doc in documents:
        for literal in required[doc["biography_id"]]:
            assert selected(doc, *literal_position(source, doc, literal)), (doc["biography_id"], literal)
        for span in doc["criticism"]:
            a, b, ca, cb = (span[key] for key in ("start", "end", "context_start", "context_end"))
            assert doc["source_char_start"] <= ca <= a < b <= cb <= doc["source_char_end"]
            assert source[a:b].strip() and span["reason"].strip()
    doc = documents[3]
    ahmad = next(c for c in doc["criticism"] if "حديث منكر" in source[c["start"]:c["end"]])
    assert "أحمد في (مسنده)" in source[ahmad["start"]:ahmad["end"]]
    assert ahmad["context_start"] == 1544417
    assert source[ahmad["context_start"]:ahmad["context_end"]].startswith("أخبرنا أبو الحسين علي بن محمد")
    dream = next(c for c in doc["criticism"] if "فهو وغيره منام" in source[c["start"]:c["end"]])
    assert "إسناده حسن" in source[dream["start"]:dream["end"]]
    assert "والمنام له تأويل" in source[dream["start"]:dream["end"]]


def test_report_comparison_and_narrator_discussion_keep_their_complete_argument(reading_corpus):
    _, source, documents, _ = reading_corpus
    doc = documents[3]
    credit = next(c for c in doc["criticism"] if "فأصحاب رسول الله" in source[c["start"]:c["end"]])
    passage = source[credit["start"]:credit["end"]]
    assert "يقول: ائت بمن يشهد معك." in passage
    assert "وصدق أبو بكر" in passage
    assert "فلم يحتج علي أن" in passage and "والله أعلم." in passage
    genealogy = next(c for c in doc["criticism"] if "وأسقط البخاري والفسوي" in source[c["start"]:c["end"]])
    assert genealogy["kind"] == "report"
    assert "وقاله قبلهما عروة والزهري." in source[genealogy["start"]:genealogy["end"]]
    context = source[genealogy["context_start"]:genealogy["context_end"]]
    assert "ولد الحارث بن زهرة" in context and "وقال الهيثم الشاشي" in context


def test_an_unnamed_narrators_reported_credit_preserves_negation_without_identifying_him(reading_corpus):
    _, source, documents, _ = reading_corpus
    doc = documents[4]
    credit = next(c for c in doc["criticism"] if "حدثني من لا أتهم" in source[c["start"]:c["end"]])
    assert credit["kind"] == "narrator"
    assert "من لا أتهم" in source[credit["start"]:credit["end"]]
    context = source[credit["context_start"]:credit["context_end"]]
    assert "عن ابن شهاب، حدثني من لا أتهم، عن" in context
    assert "مبهم" in credit["reason"]
    assert "تعيين" in credit["reason"] or "هوي" in credit["reason"]


def test_ordinary_adjectives_book_titles_and_dialogue_are_not_relabelled_as_criticism(reading_corpus):
    _, source, documents, _ = reading_corpus
    negatives = {
        1: ["حسن الخلق، لين الشيمة", "له في (صحيح مسلم)", "قلت: إني سمعت رسول الله"],
        2: ["حسن الوجه", "ابن أبي الجود", "لو قلت: باسم الله"],
        3: ["من كذب علي متعمدا", "قلت: نعم."],
        4: ["حسن الوجه، رقيق البشرة", "في (الصحيحين) حديثان"],
        5: ["أبو عون الثقفي: هو محمد بن عبيد الله، متفق عليه.", "قال: (لا) .", "إنه لا يحسن أن يصلي."],
    }
    for doc in documents:
        for literal in negatives[doc["biography_id"]]:
            a, b = literal_position(source, doc, literal)
            assert not any(c["start"] < b and a < c["end"] for c in doc["criticism"]), (doc["biography_id"], literal)


def test_all_374_numeric_notes_stay_in_the_formatted_copy_appendix(reading_corpus):
    _, source, documents, pilots = reading_corpus
    total = 0
    for doc, pilot, expected in zip(documents, pilots, [65, 58, 80, 73, 98]):
        a, b = doc["source_char_start"], doc["source_char_end"]
        annotations = effective_annotations(pilot["presentation"], doc)
        notes = annotations["footnotes"]
        assert len(notes) == expected
        for note in notes:
            note["raw"] = source[note["start"]:note["end"]]
            note["context"] = source[max(a, note["start"] - 55):min(b, note["end"] + 55)]
            assert re.fullmatch(r"\([0-9]+\)", note["raw"])
        view = grouped_reading_view(build_reading_view(source[a:b], a, starts_at_line_start=True), doc["paragraph_groups"])
        copied = presentation_plain_text(view, annotations)
        body, appendix = copied.split("[ملحق إشارات الحواشي:", 1)
        assert not re.search(r"\([0-9]+\)", body)
        for note in notes:
            assert f"{note['raw']} · [{note['start']}, {note['end']}) · {note['context']}" in appendix
        assert appendix.count(" · [") == expected
        total += expected
    assert total == 374


def test_unpublished_biography_keeps_original_view_and_cannot_receive_the_pilot_overlay(reading_corpus):
    db, source, documents, _ = reading_corpus
    # Subsequent authorized batches may already include biography6. Exercise
    # an actual unpublished biography rather than assuming publication stops5.
    bio = db.execute("SELECT b.* FROM biographies b WHERE b.source_ordinal>5 AND NOT EXISTS (SELECT 1 FROM pilot_documents p WHERE p.biography_id=b.id) ORDER BY b.source_id,b.source_ordinal LIMIT 1").fetchone()
    if bio is None:
        pytest.skip("Every biography is already curated; no unpublished reader remains")
    assert bio["full_text"] == source[bio["source_char_start"]:bio["source_char_end"]]
    base = build_reading_view(bio["full_text"], bio["source_char_start"], starts_at_line_start=True)
    presentation = {"biography_id": bio["id"], "source_sha256": PINNED_SHA, "reading_view": base}
    before = deepcopy(presentation)
    assert apply_reading_overlay(db, presentation) == before
    assert "presentation_revision" not in presentation
    assert db.execute("SELECT 1 FROM pilot_documents WHERE biography_id=?", (bio["id"],)).fetchone() is None
    invalid = deepcopy(documents[0])
    invalid.update(biography_id=bio["id"], source_char_start=bio["source_char_start"], source_char_end=bio["source_char_end"])
    with pytest.raises(ValueError, match="أول خمس تراجم"):
        validate_reading_document(db, invalid)
