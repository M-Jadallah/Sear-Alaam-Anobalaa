"""Exact witness pagination and code point preview regressions (synthetic text)."""
import sqlite3

from fastapi.testclient import TestClient
import pytest

from conftest import DICTIONARY


@pytest.fixture
def page_book(tmp_path):
    from backend.app import create_app
    from backend.importer import import_book

    text = (
        "######OpenITI#\n#META#Header#End#\n"
        "### $ 1 - شخصية مصطنعة أولى\n"
        "# 🧪 قبل العبور نص مصطنع. PageV01P001 هنا الإخلاص مع ذكر شخصية تجريبية ثانية في نص مصطنع. PageV01P002\n"
        "# الصدق لفظ مصطنع قبل علامة صفرية غير معلومة. PageV00P000\n"
        "# " + "امتداد مصطنع للاختبار فقط. " * 30 + "PageV01P003\n"
        "### $ 2 - شخصية تجريبية ثانية\n"
        "# متن مصطنع قصير لا ينسب إلى الكتاب. PageV01P004\n"
    )
    source = tmp_path / "synthetic-pages.mARkdown"
    source.write_bytes(text.encode("utf-8"))
    database = tmp_path / "pages.sqlite3"
    import_book(source, database, DICTIONARY)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    with TestClient(create_app(database, source)) as client:
        yield {"text": text, "db": connection, "client": client}
    connection.close()


def test_topic_page_is_resolved_at_witness_after_inline_marker_not_paragraph_start(page_book):
    db, client = page_book["db"], page_book["client"]
    hit = db.execute("SELECT h.*,s.page_marker AS paragraph_page FROM topic_hits h JOIN topics t ON t.id=h.topic_id JOIN segments s ON s.id=h.segment_id WHERE t.name=?", ("الإخلاص",)).fetchone()
    assert hit["paragraph_page"] == "PageV01P001"
    result = client.get(f"/api/topics/{hit['topic_id']}/evidence").json()["items"][0]
    assert result["page_marker"] == result["first_page_marker"] == result["last_page_marker"] == "PageV01P002"
    assert result["page_marker_crossed"] is False
    assert result["page_reference_status"] == "raw_unverified"
    assert result["evidence_text"] == page_book["text"][result["source_char_start"]:result["source_char_end"]]
    queue = client.get("/api/reviews", params={"kind": "topics", "page_size": 100}).json()["items"]
    assert next(row for row in queue if row["id"] == hit["id"])["page_marker"] == "PageV01P002"


def test_person_mention_page_is_resolved_at_mention_after_inline_marker(page_book):
    db, client = page_book["db"], page_book["client"]
    mention = db.execute("SELECT m.*,s.page_marker AS paragraph_page FROM person_mentions m JOIN segments s ON s.id=m.segment_id ORDER BY m.id LIMIT 1").fetchone()
    assert mention["paragraph_page"] == "PageV01P001"
    person_id = db.execute("SELECT person_id FROM mention_candidates WHERE mention_id=? LIMIT 1", (mention["id"],)).fetchone()[0]
    for rows in (
        client.get("/api/reviews", params={"kind": "mentions"}).json()["items"],
        client.get(f"/api/people/{person_id}/mentions").json()["items"],
    ):
        row = next(item for item in rows if item["id"] == mention["id"])
        assert row["page_marker"] == row["first_page_marker"] == row["last_page_marker"] == "PageV01P002"
        assert row["page_marker_crossed"] is False
        assert row["page_reference_status"] == "raw_unverified"
        assert row["mention_literal"] == page_book["text"][row["source_char_start"]:row["source_char_end"]]


def test_manual_evidence_crossing_inline_marker_retains_both_raw_page_endpoints(page_book):
    db, client, text = page_book["db"], page_book["client"], page_book["text"]
    segment = db.execute("SELECT * FROM segments WHERE text_original LIKE ?", ("%قبل العبور%",)).fetchone()
    topic_id = db.execute("SELECT id FROM topics WHERE name=?", ("الغيبة",)).fetchone()[0]
    start = text.index("قبل العبور")
    end = text.index("شخصية تجريبية ثانية في") + len("شخصية تجريبية ثانية")
    response = client.post(f"/api/segments/{segment['id']}/topic-hits", json={"topic_id": topic_id, "source_char_start": start, "source_char_end": end, "review_status": "reviewed", "reviewer": "مختبر آلي"})
    assert response.status_code == 200
    row = client.get(f"/api/topics/{topic_id}/evidence", params={"status": "reviewed"}).json()["items"][0]
    assert row["page_marker"] == row["first_page_marker"] == "PageV01P001"
    assert row["last_page_marker"] == "PageV01P002"
    assert row["page_marker_crossed"] is True
    assert row["page_reference_status"] == "raw_unverified"
    assert row["evidence_text"] == text[start:end]


def test_zero_placeholder_is_retained_raw_but_never_claimed_as_known_page(page_book):
    db, client = page_book["db"], page_book["client"]
    topic_id = db.execute("SELECT id FROM topics WHERE name=?", ("الصدق",)).fetchone()[0]
    row = client.get(f"/api/topics/{topic_id}/evidence").json()["items"][0]
    assert row["page_marker"] is None
    assert row["first_page_marker"] == row["last_page_marker"] == "PageV00P000"
    assert row["page_marker_crossed"] is False
    assert row["page_reference_status"] == "raw_unverified"


def test_title_result_preview_is_400_unicode_code_points_with_its_own_literal_range(page_book):
    db, client, text = page_book["db"], page_book["client"], page_book["text"]
    first = db.execute("SELECT * FROM biographies ORDER BY id LIMIT 1").fetchone()
    assert len(first["full_text"]) > 400 and "🧪" in first["full_text"][:400]
    response = client.get("/api/search", params={"q": "شخصية مصطنعة أولى", "scope": "titles"})
    assert response.status_code == 200
    result = response.json()["items"][0]
    assert result["biography_id"] == first["id"]
    assert result["preview_truncated"] == 1
    assert len(result["text_original"]) == 400
    assert result["text_original"] == first["full_text"][:400]
    assert result["source_char_start"] == first["source_char_start"]
    assert result["source_char_end"] == first["source_char_start"] + 400
    assert result["biography_char_end"] == first["source_char_end"] > result["source_char_end"]
    assert text[result["source_char_start"]:result["source_char_end"]] == result["text_original"]
    # A short biography keeps its full literal range and has no truncation claim.
    short = client.get("/api/search", params={"q": "شخصية تجريبية ثانية", "scope": "titles"}).json()["items"][0]
    assert short["preview_truncated"] == 0
    assert short["source_char_end"] == short["biography_char_end"]
    assert short["text_original"] == text[short["source_char_start"]:short["source_char_end"]]
