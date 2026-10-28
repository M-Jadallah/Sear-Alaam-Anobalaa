"""API tests use an isolated SQLite database, never the user's review database."""
from fastapi.testclient import TestClient


def test_missing_source_returns_empty_catalogue_and_complete_dictionary(tmp_path):
    from backend.app import create_app

    with TestClient(create_app(db_path=tmp_path / "empty.sqlite3", source_path=tmp_path / "missing.mARkdown")) as client:
        assert client.get("/api/biographies").json()["total"] == 0
        assert client.get("/api/people").json()["total"] == 0
        topics = client.get("/api/topics", params={"page_size": 100}).json()
        categories = client.get("/api/categories", params={"page_size": 100}).json()
        assert topics["total"] == 81
        assert categories["total"] == 9
        stats = client.get("/api/stats")
        assert stats.status_code == 200
        assert "missing" in str(stats.json()).lower() or "غير موجود" in str(stats.json())


def test_reader_returns_whole_original_and_segments_are_contained(client, db, book):
    rows = db.execute("SELECT * FROM biographies ORDER BY id").fetchall()
    for biography in rows:
        response = client.get(f"/api/biographies/{biography['id']}")
        assert response.status_code == 200
        item = response.json()
        assert item["full_text"] == biography["full_text"]
        assert item["full_text"] == book["text"][item["source_char_start"]:item["source_char_end"]]
        segments = client.get(f"/api/biographies/{biography['id']}/segments").json()
        assert segments["total"] > 0
        for segment in segments["items"]:
            assert biography["source_char_start"] <= segment["source_char_start"] < segment["source_char_end"] <= biography["source_char_end"]


def test_search_normalizes_arabic_and_paginates_without_rewriting_original(client, book):
    params = {"scope": "text", "page_size": 1}
    with_hamza = client.get("/api/search", params={**params, "q": "الإخلاص"})
    without_hamza = client.get("/api/search", params={**params, "q": "الاخلاص"})
    assert with_hamza.status_code == without_hamza.status_code == 200
    first = with_hamza.json()
    assert first["total"] == without_hamza.json()["total"] >= 2
    assert len(first["items"]) == 1
    second = client.get("/api/search", params={**params, "q": "الاخلاص", "page": 2}).json()
    assert len(second["items"]) == 1
    assert second["items"] != first["items"]
    # Open the actual source through its result link: no normalized text replaces the original.
    for result in first["items"] + second["items"]:
        assert result["text_original"] == book["text"][result["source_char_start"]:result["source_char_end"]]
        response = client.get(f"/api/biographies/{result['biography_id']}")
        assert response.status_code == 200
        original = response.json()["full_text"]
        assert original in book["text"]
        assert "الإخلاص" in original or "الإِخْلَاصُ" in original


def test_search_is_bound_and_fts_syntax_cannot_break_database(client):
    for query in ["' OR 1=1; DROP TABLE biographies; --", '"', "AND OR NOT", "*", "الإخلاص NEAR("]:
        response = client.get("/api/search", params={"q": query, "scope": "text"})
        assert response.status_code in (200, 400, 422)
    assert client.get("/api/biographies").json()["total"] == 3
    for scope in ["titles", "people", "topics"]:
        response = client.get("/api/search", params={"q": "شخصية" if scope != "topics" else "الإخلاص", "scope": scope})
        assert response.status_code == 200
        assert "items" in response.json()
        assert response.json()["total"] > 0


def test_exact_phrase_mode_and_multiword_mode_have_distinct_meaning(client):
    phrase = client.get("/api/search", params={"scope": "text", "q": "الإخلاص الصدق", "mode": "phrase"}).json()
    reverse_phrase = client.get("/api/search", params={"scope": "text", "q": "الصدق الإخلاص", "mode": "phrase"}).json()
    words = client.get("/api/search", params={"scope": "text", "q": "الصدق الإخلاص", "mode": "words"}).json()
    assert phrase["total"] == words["total"] == 1
    assert reverse_phrase["total"] == 0


def test_topic_search_filters_before_count_and_pagination(client):
    # This topic is near the end of the CSV, beyond the first 24 unfiltered rows.
    response = client.get("/api/search", params={"scope": "topics", "q": "الغيبة", "page_size": 1})
    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["topic_id"]
    direct = client.get("/api/topics", params={"q": "الغيبة", "page_size": 1}).json()
    assert direct["total"] == 1 and direct["items"][0]["name"] == "الغيبة"
    second = client.get("/api/topics", params={"q": "الغيبة", "page_size": 1, "page": 2}).json()
    assert second["total"] == 1 and second["items"] == []


def test_topic_evidence_links_to_literal_source_and_review_changes_are_audited(client, db, book):
    hit = db.execute("SELECT * FROM topic_hits ORDER BY id LIMIT 1").fetchone()
    listing = client.get(f"/api/topics/{hit['topic_id']}/evidence", params={"status": "candidate"}).json()
    assert listing["total"] > 0
    for item in listing["items"]:
        assert item["evidence_text"] == book["text"][item["source_char_start"]:item["source_char_end"]]
    before = db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0]
    approved = client.post(f"/api/reviews/topic-hits/{hit['id']}", json={"review_status": "reviewed", "reviewer": "مختبر آلي", "note": "اعتماد على بيانات مصطنعة للاختبار فقط"})
    assert approved.status_code == 200, approved.text
    assert db.execute("SELECT review_status FROM topic_hits WHERE id=?", (hit["id"],)).fetchone()[0] == "reviewed"
    assert db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == before + 1
    reviewed = client.get(f"/api/topics/{hit['topic_id']}/evidence", params={"status": "reviewed"}).json()
    assert reviewed["total"] == 1
    rejected = client.post(f"/api/reviews/topic-hits/{hit['id']}", json={"review_status": "rejected", "reviewer": "مختبر آلي", "note": "تغيير قرار تجريبي"})
    assert rejected.status_code == 200
    assert db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == before + 2


def test_review_rejects_out_of_source_evidence_and_invalid_status_without_mutation(client, db):
    hit = db.execute("SELECT * FROM topic_hits ORDER BY id LIMIT 1").fetchone()
    before = db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0]
    invalid = client.post(f"/api/reviews/topic-hits/{hit['id']}", json={"review_status": "reviewed", "source_char_start": -1, "source_char_end": 999999, "reviewer": "مختبر آلي"})
    assert invalid.status_code in (400, 422)
    invalid_state = client.post(f"/api/reviews/topic-hits/{hit['id']}", json={"review_status": "verified-by-ai", "reviewer": "مختبر آلي"})
    assert invalid_state.status_code == 422
    assert db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == before
    assert db.execute("SELECT review_status FROM topic_hits WHERE id=?", (hit["id"],)).fetchone()[0] == "candidate"


def test_untrusted_web_origin_cannot_write_review(client, db):
    hit_id = db.execute("SELECT id FROM topic_hits LIMIT 1").fetchone()[0]
    response = client.post(f"/api/reviews/topic-hits/{hit_id}", headers={"Origin": "https://untrusted.example"}, json={"review_status": "reviewed", "reviewer": "طرف خارجي"})
    assert response.status_code == 403
    assert db.execute("SELECT review_status FROM topic_hits WHERE id=?", (hit_id,)).fetchone()[0] == "candidate"


def test_people_page_separates_biographies_and_mentions_and_requires_identity_review(client, db):
    people = db.execute("SELECT p.id FROM people p JOIN biographies b ON b.subject_person_id=p.id WHERE b.heading LIKE ? ORDER BY p.id", ("%شخصية تجريبية ثانية%",)).fetchall()
    assert len(people) == 2 and people[0][0] != people[1][0]
    for person in people:
        response = client.get(f"/api/people/{person[0]}")
        assert response.status_code == 200
        mentions = client.get(f"/api/people/{person[0]}/mentions")
        assert mentions.status_code == 200
        assert "items" in mentions.json()
    mention = db.execute("SELECT * FROM person_mentions LIMIT 1").fetchone()
    assert mention is not None
    decision = client.post(f"/api/reviews/mentions/{mention['id']}", json={"review_status": "reviewed", "person_id": people[0][0], "reviewer": "مختبر آلي", "note": "حل هوية تجريبي على بيانات مصطنعة"})
    assert decision.status_code == 200, decision.text
    current = db.execute("SELECT * FROM person_mentions WHERE id=?", (mention["id"],)).fetchone()
    assert current["person_id"] == people[0][0]
    assert current["identity_review_status"] == "reviewed"


def test_ambiguous_mention_cannot_be_approved_without_identity_or_as_its_owner(client, db):
    mention = db.execute("SELECT m.*,b.subject_person_id FROM person_mentions m JOIN biographies b ON b.id=m.biography_id ORDER BY m.id LIMIT 1").fetchone()
    assert mention["person_id"] is None
    missing_identity = client.post(f"/api/reviews/mentions/{mention['id']}", json={"review_status": "reviewed", "reviewer": "مختبر آلي"})
    assert missing_identity.status_code == 422
    owner = client.post(f"/api/reviews/mentions/{mention['id']}", json={"review_status": "reviewed", "person_id": mention["subject_person_id"], "reviewer": "مختبر آلي"})
    assert owner.status_code == 422
    assert db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == 0


def test_invalid_pagination_and_unknown_records_are_handled(client):
    assert client.get("/api/biographies", params={"page": 0}).status_code == 422
    assert client.get("/api/search", params={"q": "الإخلاص", "page_size": 100000}).status_code == 422
    for path in ["/api/biographies/999999", "/api/people/999999", "/api/topics/999999/evidence"]:
        assert client.get(path).status_code == 404


def test_segment_review_changes_structural_type_but_never_source_text(client, db, book):
    segment = db.execute("SELECT * FROM segments WHERE biography_id IS NOT NULL ORDER BY id LIMIT 1").fetchone()
    before = db.execute("SELECT full_text FROM sources LIMIT 1").fetchone()[0]
    response = client.post(f"/api/reviews/segments/{segment['id']}", json={"source_char_start": segment["source_char_start"], "source_char_end": segment["source_char_end"], "segment_type": "story", "reviewer": "مختبر آلي", "note": "تصنيف بنيوي تجريبي"})
    assert response.status_code == 200, response.text
    current = db.execute("SELECT * FROM segments WHERE id=?", (segment["id"],)).fetchone()
    assert current["segment_type"] == "story"
    assert current["text_original"] == book["text"][current["source_char_start"]:current["source_char_end"]]
    assert db.execute("SELECT full_text FROM sources LIMIT 1").fetchone()[0] == before
    assert db.execute("SELECT full_text FROM biographies WHERE id=?", (segment["biography_id"],)).fetchone()[0] in before
    invalid = client.post(f"/api/reviews/segments/{segment['id']}", json={"source_char_start": 0, "source_char_end": len(book["text"]), "segment_type": "story", "reviewer": "مختبر آلي"})
    assert invalid.status_code in (400, 422)


def test_manual_topic_link_must_be_literal_bounded_nonduplicate_and_logged(client, db, book):
    segment = db.execute("SELECT * FROM segments WHERE biography_id IS NOT NULL ORDER BY id LIMIT 1").fetchone()
    topic = db.execute("SELECT id FROM topics WHERE name=?", ("الغيبة",)).fetchone()[0]
    start, end = segment["source_char_start"], segment["source_char_end"]
    payload = {"topic_id": topic, "source_char_start": start, "source_char_end": end, "review_status": "reviewed", "reviewer": "مختبر آلي", "note": "وسم يدوي على بيانات مصطنعة فقط"}
    created = client.post(f"/api/segments/{segment['id']}/topic-hits", json=payload)
    assert created.status_code == 200, created.text
    row = created.json()
    assert row["method"] == "manual" and row["review_status"] == "reviewed"
    assert row["evidence_text"] == book["text"][start:end]
    assert db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == 1
    duplicate = client.post(f"/api/segments/{segment['id']}/topic-hits", json=payload)
    assert duplicate.status_code == 409
    invalid = client.post(f"/api/segments/{segment['id']}/topic-hits", json={**payload, "source_char_start": -1})
    assert invalid.status_code == 422
    assert db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == 1


def test_mention_boundaries_use_original_source_and_identity_can_be_unassigned(client, db, book):
    mention = db.execute("SELECT * FROM person_mentions WHERE biography_id IS NOT NULL ORDER BY id LIMIT 1").fetchone()
    segment = db.execute("SELECT * FROM segments WHERE id=?", (mention["segment_id"],)).fetchone()
    new_start, new_end = segment["source_char_start"], segment["source_char_end"]
    response = client.post(f"/api/reviews/mentions/{mention['id']}", json={"review_status": "candidate", "source_char_start": new_start, "source_char_end": new_end, "reviewer": "مختبر آلي"})
    assert response.status_code == 200, response.text
    assert response.json()["mention_literal"] == book["text"][new_start:new_end]
    assert response.json()["evidence_text"] == response.json()["mention_literal"]
    identity = db.execute("SELECT person_id FROM mention_candidates WHERE mention_id=? LIMIT 1", (mention["id"],)).fetchone()[0]
    approved = client.post(f"/api/reviews/mentions/{mention['id']}", json={"review_status": "reviewed", "person_id": identity, "reviewer": "مختبر آلي"})
    assert approved.status_code == 200
    cleared = client.post(f"/api/reviews/mentions/{mention['id']}", json={"review_status": "candidate", "person_id": None, "reviewer": "مختبر آلي"})
    assert cleared.status_code == 200
    assert cleared.json()["person_id"] is None
    invalid = client.post(f"/api/reviews/mentions/{mention['id']}", json={"review_status": "candidate", "source_char_start": -1, "source_char_end": 999999, "reviewer": "مختبر آلي"})
    assert invalid.status_code == 422


def test_segment_boundaries_cannot_orphan_linked_evidence(client, db):
    hit = db.execute("SELECT h.*,s.source_char_start AS segment_start,s.source_char_end AS segment_end FROM topic_hits h JOIN segments s ON s.id=h.segment_id WHERE h.source_char_start>s.source_char_start ORDER BY h.id LIMIT 1").fetchone()
    response = client.post(f"/api/reviews/segments/{hit['segment_id']}", json={"source_char_start": hit["segment_start"], "source_char_end": hit["source_char_start"], "segment_type": "paragraph", "reviewer": "مختبر آلي"})
    assert response.status_code == 409
    segment = db.execute("SELECT * FROM segments WHERE id=?", (hit["segment_id"],)).fetchone()
    assert segment["source_char_start"] == hit["segment_start"] and segment["source_char_end"] == hit["segment_end"]
    assert db.execute("SELECT COUNT(*) FROM review_log").fetchone()[0] == 0
