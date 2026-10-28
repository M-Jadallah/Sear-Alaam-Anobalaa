import hashlib
import sqlite3

import pytest

from conftest import DICTIONARY


def test_import_keeps_canonical_source_biographies_segments_and_unverified_markers(book, db):
    source = db.execute("SELECT * FROM sources").fetchone()
    assert source["full_text"] == book["text"]
    assert source["sha256"] == hashlib.sha256(book["source"].read_bytes()).hexdigest()
    biographies = db.execute("SELECT * FROM biographies ORDER BY source_char_start").fetchall()
    assert len(biographies) == 3
    for row in biographies:
        assert row["full_text"] == book["text"][row["source_char_start"]:row["source_char_end"]]
        assert "\r\n" in row["full_text"]
        assert "### |PARATEXT|" not in row["full_text"]
    for row in db.execute("SELECT * FROM segments"):
        assert row["text_original"] == book["text"][row["source_char_start"]:row["source_char_end"]]
    assert db.execute("SELECT COUNT(*) FROM page_markers WHERE verified_print_page != 0").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM page_markers").fetchone()[0] == 4
    assert db.execute("PRAGMA foreign_key_check").fetchall() == []


def test_import_is_idempotent_and_dictionary_has_all_81_topics_9_categories(book, db):
    from backend.importer import import_book

    tables = ["sources", "biographies", "segments", "people", "person_aliases", "person_mentions", "topic_hits", "topics"]
    before = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
    result = import_book(book["source"], book["database"], DICTIONARY)
    after = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables}
    assert before == after
    assert result["unchanged"] is True
    assert db.execute("SELECT COUNT(*) FROM topics WHERE parent_id IS NULL").fetchone()[0] == 9
    assert db.execute("SELECT COUNT(*) FROM topics WHERE parent_id IS NOT NULL").fetchone()[0] == 81


def test_same_name_people_are_not_automatically_merged(db):
    rows = db.execute("SELECT p.* FROM biographies b JOIN people p ON p.id=b.subject_person_id WHERE b.heading LIKE ?", ("%شخصية تجريبية ثانية%",)).fetchall()
    assert len(rows) == 2
    assert len({row["id"] for row in rows}) == 2
    assert all(row["death_year_ah"] is None for row in rows)


def test_topic_hits_and_mentions_are_literal_candidates_within_their_segment(book, db):
    hits = db.execute("SELECT h.*,s.source_char_start AS segment_start,s.source_char_end AS segment_end FROM topic_hits h JOIN segments s ON s.id=h.segment_id").fetchall()
    assert hits
    for hit in hits:
        assert hit["review_status"] == "candidate"
        assert hit["method"] == "keyword_candidate"
        assert book["text"][hit["source_char_start"]:hit["source_char_end"]] == hit["evidence_text"]
        assert hit["segment_start"] <= hit["source_char_start"] < hit["source_char_end"] <= hit["segment_end"]
    # Literal fire can at most produce a candidate, never a validated theological assertion.
    fire = db.execute("SELECT h.* FROM topic_hits h JOIN topics t ON t.id=h.topic_id WHERE t.name=?", ("الجنة والنار",)).fetchall()
    assert all(hit["review_status"] == "candidate" for hit in fire)
    mentions = db.execute("SELECT m.*,b.subject_person_id FROM person_mentions m JOIN biographies b ON b.id=m.biography_id").fetchall()
    assert mentions
    for mention in mentions:
        literal = book["text"][mention["source_char_start"]:mention["source_char_end"]]
        assert literal == mention["mention_literal"]
        assert mention["identity_review_status"] != "reviewed"
        assert mention["person_id"] != mention["subject_person_id"]


def test_missing_source_and_invalid_source_do_not_create_fabricated_records(tmp_path):
    from backend.importer import import_book

    missing = tmp_path / "missing.mARkdown"
    database = tmp_path / "missing.sqlite3"
    with pytest.raises(FileNotFoundError):
        import_book(missing, database, DICTIONARY)
    bad = tmp_path / "bad.mARkdown"
    bad.write_text("ليس هذا ملف OpenITI معتمدًا", encoding="utf-8")
    with pytest.raises(ValueError):
        import_book(bad, database, DICTIONARY)
    if database.exists():
        with sqlite3.connect(database) as connection:
            existing = connection.execute("SELECT name FROM sqlite_master WHERE name='biographies'").fetchall()
            if existing:
                assert connection.execute("SELECT COUNT(*) FROM biographies").fetchone()[0] == 0


def test_title_only_keyword_does_not_tag_unrelated_body(tmp_path):
    from backend.importer import import_book

    source = tmp_path / "title-only.mARkdown"
    source.write_bytes(("######OpenITI#\n#META#Header#End#\n### $ 1 - الإخلاص\n# متن مصطنع منفصل لا يحتوي اللفظ المقصود في العنوان.\nPageV01P001\n").encode("utf-8"))
    database = tmp_path / "title-only.sqlite3"
    import_book(source, database, DICTIONARY)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM topic_hits h JOIN topics t ON t.id=h.topic_id WHERE t.name=?", ("الإخلاص",)).fetchone()[0] == 0


def test_one_segment_can_have_multiple_candidate_topics(db):
    rows = db.execute("SELECT h.segment_id,COUNT(DISTINCT t.id) AS n FROM topic_hits h JOIN topics t ON t.id=h.topic_id WHERE t.name IN (?,?) GROUP BY h.segment_id", ("الإخلاص", "الصدق")).fetchall()
    assert any(row["n"] == 2 for row in rows)


def test_numeric_heading_does_not_create_a_person_named_by_its_number(tmp_path):
    from backend.importer import import_book

    source = tmp_path / "malformed-heading.mARkdown"
    source.write_bytes(("######OpenITI#\n#META#Header#End#\n### $ 89\n# - اسم مصطنع في سطر تالف للاختبار.\n").encode("utf-8"))
    database = tmp_path / "malformed-heading.sqlite3"
    import_book(source, database, DICTIONARY)
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM biographies").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM people WHERE canonical_name=?", ("89",)).fetchone()[0] == 0
