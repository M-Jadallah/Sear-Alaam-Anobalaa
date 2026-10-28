"""Independent checks of the authored corpus; never write to the real library."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import pytest

from backend.pilot import validate_document

ROOT = Path(__file__).resolve().parents[1]
PINNED_SHA = "7ae7f691ffe236f5b888f5dfdf18becd9e5ea17ab6b5851528db5efd333886c0"
FILES = [ROOT / "reference/pilot" / f"biography-{bid}.json" for bid in range(1, 6)]


@pytest.fixture(scope="module")
def pilot_corpus():
    source_path = ROOT / "data/source.mARkdown"
    database = ROOT / "data/library.sqlite3"
    if not source_path.exists() or not database.exists() or not all(path.exists() for path in FILES):
        pytest.skip("The full, explicitly source-bound five-biography pilot is not installed")
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == PINNED_SHA
    with source_path.open(encoding="utf-8", newline="") as handle:
        source = handle.read()
    db = sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    documents = [json.loads(path.read_text(encoding="utf-8")) for path in FILES]
    yield db, source, documents
    db.close()


def test_authored_documents_are_literally_source_bound(pilot_corpus):
    db, source, documents = pilot_corpus
    assert [item["biography_id"] for item in documents] == [1, 2, 3, 4, 5]
    for document in documents:
        biography = validate_document(db, document)
        assert document["source_sha256"] == PINNED_SHA
        assert biography["full_text"] == source[document["source_char_start"]:document["source_char_end"]]
        assert document["reviewer"] == "مراجعة نصية بواسطة وكيل"


def test_every_pilot_segment_has_exactly_one_agent_assessment(pilot_corpus):
    db, _, documents = pilot_corpus
    expected_counts = [185, 191, 293, 248, 358]
    all_ids = []
    for document, expected_count in zip(documents, expected_counts):
        rows = db.execute("SELECT id FROM segments WHERE biography_id=? ORDER BY segment_ordinal", (document["biography_id"],)).fetchall()
        ids = [item["segment_id"] for item in document["segments"]]
        assert ids == [row["id"] for row in rows]
        assert len(ids) == expected_count
        assert all(item["assessment"] in {"supported", "no_clear_topic", "uncertain"} and item["note"].strip() for item in document["segments"])
        assert all("reviewed" not in item for item in document["segments"])
        all_ids.extend(ids)
    assert len(set(all_ids)) == len(all_ids) == 1275


def test_numeric_footnotes_have_literal_unique_ranges(pilot_corpus):
    _, source, documents = pilot_corpus
    total = 0
    for document, expected in zip(documents, [65, 58, 80, 73, 98]):
        notes = document["presentation"]["footnotes"]
        a, b = document["source_char_start"], document["source_char_end"]
        expected_ranges = {(a + match.start(), a + match.end()) for match in re.finditer(r"\([0-9]+\)", source[a:b])}
        actual_ranges = {(note["start"], note["end"]) for note in notes}
        assert len(notes) == len(actual_ranges) == expected
        assert actual_ranges == expected_ranges
        assert all(re.fullmatch(r"\([0-9]+\)", source[start:end]) for start, end in actual_ranges)
        total += len(notes)
    assert total == 374


def test_all_witnesses_are_inside_their_canonical_segment_and_context(pilot_corpus):
    db, source, documents = pilot_corpus
    for document in documents:
        rows = {row["id"]: row for row in db.execute("SELECT * FROM segments WHERE biography_id=?", (document["biography_id"],))}
        for assessment in document["segments"]:
            row = rows[assessment["segment_id"]]
            assert row["text_original"] == source[row["source_char_start"]:row["source_char_end"]]
            for topic in assessment["topics"]:
                a, b = topic["evidence_start"], topic["evidence_end"]
                assert row["source_char_start"] <= a < b <= row["source_char_end"]
                assert document["source_char_start"] <= topic["context_start"] <= a < b <= topic["context_end"] <= document["source_char_end"]
                assert source[a:b].strip()
                assert topic["rationale"].strip()
                assert topic["subject_scope"] in {"biography_subject", "mentioned_person", "general_context"}
                assert topic["stance"] in {"reported", "negated", "accusation", "contested", "criticism"}


def test_legacy_false_positives_have_explicit_exclusion_reasons(pilot_corpus):
    _, _, documents = pilot_corpus
    recommendations = {item["hit_id"]: item for document in documents for segment in document["segments"] for item in segment["existing_hits"]}
    # A narrator's nisba, a narrator's name, an appointment document,
    # a book title, a sura name, and a title of another book.
    for hit_id in [705, 726, 720, 710, 722, 754, 758, 767]:
        assert recommendations[hit_id]["decision"] == "exclude"
        assert len(recommendations[hit_id]["reason"]) >= 15


def test_gold_questions_were_authored_as_source_witnesses(pilot_corpus):
    _, source, documents = pilot_corpus
    questions = []
    for document in documents:
        assert len(document["gold"]) == 3
        for gold in document["gold"]:
            assert document["source_char_start"] <= gold["start"] < gold["end"] <= document["source_char_end"]
            assert source[gold["start"]:gold["end"]].strip()
            assert len(gold["question"].split()) >= 5
            questions.append(gold["question"])
    assert len(set(questions)) == 15


def test_quoted_accusation_and_explicit_source_criticism_stay_visible(pilot_corpus):
    _, source, documents = pilot_corpus
    document = documents[3]
    for segment in document["segments"]:
        for topic in segment["topics"]:
            evidence = source[topic["evidence_start"]:topic["evidence_end"]]
            context = source[topic["context_start"]:topic["context_end"]]
            if topic["topic"] == "الرياء" and "عبد الرحمن" in context:
                assert topic["stance"] == "accusation"
                assert "المنافقين" in context
            if "لا يدخل الجنة" in evidence and "حبوا" in evidence:
                assert "حديث منكر" in context


def test_person_colors_do_not_match_a_name_inside_a_different_word(pilot_corpus):
    _, source, documents = pilot_corpus
    for document in documents:
        for name in document["presentation"]["names"]:
            a, b = name["start"], name["end"]
            assert not (a and re.match(r"\w", source[a - 1]))
            assert not (b < len(source) and re.match(r"\w", source[b]))


def test_validation_rejects_missing_coverage_and_wrong_source(pilot_corpus):
    db, _, documents = pilot_corpus
    bad = deepcopy(documents[0])
    bad["segments"].pop()
    with pytest.raises(ValueError, match="كل مقطع"):
        validate_document(db, bad)
    bad = deepcopy(documents[0])
    bad["source_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="بصمة"):
        validate_document(db, bad)


def test_validation_rejects_relocated_witness_or_non_numeric_note(pilot_corpus):
    db, _, documents = pilot_corpus
    bad = deepcopy(documents[0])
    topic = next(topic for segment in bad["segments"] for topic in segment["topics"])
    topic["evidence_start"] = bad["source_char_start"] - 1
    with pytest.raises(ValueError, match="نطاق"):
        validate_document(db, bad)
    bad = deepcopy(documents[0])
    bad["presentation"]["footnotes"][0] = {"start": bad["source_char_start"], "end": bad["source_char_start"] + 3}
    with pytest.raises(ValueError, match="قوسًا رقميًا"):
        validate_document(db, bad)
