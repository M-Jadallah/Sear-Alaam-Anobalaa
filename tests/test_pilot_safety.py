"""Independent pilot safety checks; all writes use explicitly synthetic books.

The real text and the production database are never changed by this module.
No optional model is loaded: hybrid channels are exercised with literal stubs.
"""
from copy import deepcopy
import json

import pytest
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.db import connect
from backend.pilot import prepare, presentation, search_scope, topic_candidates, validate_document


@pytest.fixture
def audited_sample(book, monkeypatch):
    monkeypatch.setenv("SIYAR_SEMANTIC_DISABLED", "1")
    documents = []
    with connect(book["database"]) as db:
        sha = db.execute("SELECT sha256 FROM sources").fetchone()[0]
        for bio in db.execute("SELECT * FROM biographies ORDER BY id"):
            original_segments = list(db.execute("SELECT * FROM segments WHERE biography_id=? ORDER BY segment_ordinal", (bio["id"],)))
            segments = []
            for ordinal, segment in enumerate(original_segments):
                topics = []
                if bio["id"] == 1 and ordinal == 0:
                    topics = [{
                        "topic": "موضوع مصطنع مؤهل", "category": "القلب والسلوك", "aliases": ["معنى خاص للاختبار"],
                        "rationale": "تفسير اختباري مصطنع لا حكم تاريخي", "relation_type": "discussion",
                        "subject_scope": "general_context", "stance": "contested",
                        "evidence_start": segment["source_char_start"], "evidence_end": segment["source_char_end"],
                        "context_start": segment["source_char_start"], "context_end": original_segments[1]["source_char_end"],
                    }]
                segments.append({
                    "segment_id": segment["id"], "assessment": "supported" if topics else "no_clear_topic",
                    "note": "فحص اصطناعي خاص بالاختبار؛ لا يمثل مادة الكتاب", "topics": topics,
                    "existing_hits": [{"hit_id": row[0], "decision": "exclude", "reason": "توصية استبعاد اصطناعية"}
                                      for row in db.execute("SELECT id FROM topic_hits WHERE segment_id=?", (segment["id"],))],
                })
            documents.append({
                "source_sha256": sha, "biography_id": bio["id"], "source_char_start": bio["source_char_start"],
                "source_char_end": bio["source_char_end"], "reviewer": "وكيل اصطناعي", "segments": segments,
                "presentation": {key: [] for key in ("headings", "quotes", "poetry", "lists", "bold", "keywords", "names", "footnotes")}, "gold": [],
            })
    prepare(book["database"], documents)
    with connect(book["database"]) as db:
        supported = dict(db.execute("SELECT * FROM pilot_evidence WHERE agent_decision='supported'").fetchone())
        excluded = dict(db.execute("SELECT * FROM pilot_evidence WHERE agent_decision='exclude' ORDER BY id LIMIT 1").fetchone())
    return {**book, "documents": documents, "supported": supported, "excluded": excluded}


def rows(db_path, table):
    # Table identifiers below are constants belonging to the tests.
    with connect(db_path) as db:
        return [tuple(row) for row in db.execute("SELECT * FROM " + table + " ORDER BY id")]


def test_public_biography_topics_do_not_publish_exclusion_recommendations(audited_sample):
    p = audited_sample
    with TestClient(create_app(p["database"], p["source"])) as client:
        payload = client.get("/api/biographies/1/topics").json()
    assert payload["items"]
    for topic in payload["items"]:
        assert topic["evidence_count"] > 0
        assert all(item["agent_decision"] != "exclude" for item in topic["evidence"])
        assert all(item["human_status"] != "rejected" for item in topic["evidence"])


def test_human_acceptance_of_exclusion_never_becomes_topic_evidence(audited_sample):
    p = audited_sample
    item = p["excluded"]
    with TestClient(create_app(p["database"], p["source"])) as client:
        response = client.post(f"/api/pilot/reviews/{item['id']}", json={"decision": "reviewed", "reviewer": "مراجع اصطناعي"})
        assert response.status_code == 200
        public = client.get(f"/api/biographies/{item['biography_id']}/topics").json()
        assert not any(e["id"] == item["id"] for t in public["items"] for e in t["evidence"])
    assert not any(row["topic_audit_id"] == item["id"] for row in topic_candidates(p["database"], "", {"topic_ids": [item["topic_id"]]}))
    with connect(p["database"]) as db:
        assert db.execute("SELECT review_status FROM topic_hits WHERE id=?", (item["original_hit_id"],)).fetchone()[0] == "rejected"


def test_rejecting_proposal_preserves_canonical_values_and_logs_transaction(audited_sample):
    p = audited_sample
    canonical = rows(p["database"], "topic_hits")
    before = deepcopy(p["supported"])
    with TestClient(create_app(p["database"], p["source"])) as client:
        response = client.post(f"/api/pilot/reviews/{before['id']}", json={"decision": "rejected", "reviewer": "مراجع اصطناعي", "note": "رفض تفسير الوكيل"})
        assert response.status_code == 200
        assert response.json()["human_status"] == "rejected"
        assert client.post(f"/api/pilot/reviews/{before['id']}", json={"decision": "reviewed", "reviewer": "آخر"}).status_code == 409
    assert rows(p["database"], "topic_hits") == canonical
    with connect(p["database"]) as db:
        event = db.execute("SELECT * FROM pilot_events").fetchone()
        assert json.loads(event["before_json"])["human_status"] == "pending"
        assert json.loads(event["after_json"])["human_status"] == "rejected"
        assert event["actor_kind"] == "human"
        assert db.execute("SELECT COUNT(*) FROM review_log WHERE entity_type='pilot_topic'").fetchone()[0] == 1
    assert topic_candidates(p["database"], "", {"topic_ids": [before["topic_id"]]}) == []


def test_approved_canonical_topic_cannot_be_overwritten_by_pilot(audited_sample):
    p = audited_sample
    excluded = p["excluded"]
    with connect(p["database"]) as db:
        db.execute("UPDATE topic_hits SET review_status='reviewed',reviewer_note='اعتماد سابق اصطناعي' WHERE id=?", (excluded["original_hit_id"],))
    canonical = rows(p["database"], "topic_hits")
    with TestClient(create_app(p["database"], p["source"])) as client:
        response = client.post(f"/api/pilot/reviews/{excluded['id']}", json={"decision": "reviewed", "reviewer": "مراجع اصطناعي"})
        assert response.status_code == 409
    assert rows(p["database"], "topic_hits") == canonical
    assert rows(p["database"], "pilot_events") == []
    with connect(p["database"]) as db:
        assert db.execute("SELECT human_status FROM pilot_evidence WHERE id=?", (excluded["id"],)).fetchone()[0] == "pending"


def test_coverage_human_decision_prevents_prepare_replacement(audited_sample):
    p = audited_sample
    segment_id = p["documents"][0]["segments"][0]["segment_id"]
    with TestClient(create_app(p["database"], p["source"])) as client:
        assert client.post(f"/api/pilot/coverage/{segment_id}", json={"reviewer": "إنسان اصطناعي", "note": "قراءة كاملة"}).status_code == 200
        assert client.post(f"/api/pilot/coverage/{segment_id}", json={"reviewer": "آخر"}).status_code == 409
    changed = deepcopy(p["documents"])
    changed[0]["segments"][0]["note"] += " تغيير"
    with pytest.raises(ValueError, match="قرارات بشرية"):
        prepare(p["database"], changed)
    with connect(p["database"]) as db:
        assert db.execute("SELECT human_checked FROM pilot_coverage WHERE segment_id=?", (segment_id,)).fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM pilot_events").fetchone()[0] == 1
    assert prepare(p["database"], p["documents"])["status"] == "unchanged"


@pytest.mark.parametrize("invalid", ["foreign_context", "short_context", "crossing_witness", "duplicate_segment", "invalid_relation"])
def test_invalid_authored_ranges_fail_before_preparing(audited_sample, invalid):
    p = audited_sample
    bad = deepcopy(p["documents"][0])
    topic = bad["segments"][0]["topics"][0]
    if invalid == "foreign_context":
        topic["context_end"] = bad["source_char_end"] + 1
    elif invalid == "short_context":
        topic["context_start"] = topic["evidence_start"] + 1
    elif invalid == "crossing_witness":
        topic["evidence_end"] = topic["context_end"]
    elif invalid == "duplicate_segment":
        bad["segments"][-1]["segment_id"] = bad["segments"][0]["segment_id"]
    else:
        topic["relation_type"] = "verified_scientific_fact"
    with connect(p["database"]) as db, pytest.raises(ValueError):
        validate_document(db, bad)


def test_stale_source_closes_all_pilot_read_and_write_paths(audited_sample):
    p = audited_sample
    with connect(p["database"]) as db:
        db.execute("UPDATE sources SET sha256=?", ("9" * 64,))
    assert presentation(p["database"], 1)["status"] == "stale"
    assert topic_candidates(p["database"], "", {"topic_ids": [p["supported"]["topic_id"]]}) == []
    assert search_scope(p["database"], {"pilot_only": True, "reviewed_only": True}) == ([], [])
    with TestClient(create_app(p["database"], p["source"])) as client:
        assert client.get("/api/pilot/reviews").json()["total"] == 0
        assert client.get("/api/pilot/coverage").json()["total"] == 0
        assert client.get("/api/biographies/1/topics").json()["pilot"] is False
        assert client.post(f"/api/pilot/reviews/{p['supported']['id']}", json={"decision": "reviewed", "reviewer": "إنسان"}).status_code == 409
        assert client.post(f"/api/pilot/coverage/{p['supported']['segment_id']}", json={"reviewer": "إنسان"}).status_code == 409
    assert rows(p["database"], "pilot_events") == []


def test_topic_channel_keeps_context_qualifications_after_overlap_deduplication(audited_sample, monkeypatch):
    from backend import smart_search
    p = audited_sample
    selected = p["supported"]
    with connect(p["database"]) as db:
        lexical = dict(db.execute("SELECT s.*,b.heading FROM segments s JOIN biographies b ON b.id=s.biography_id WHERE s.id=?", (selected["segment_id"],)).fetchone())
    lexical["clean_text"] = lexical["text_original"]
    monkeypatch.setattr(smart_search, "_lexical_candidates", lambda *args: [lexical])
    smart_search._CACHE.clear()
    result = smart_search.smart_search({"q": "معنى خاص للاختبار", "plan": {"topic_ids": [selected["topic_id"]]}}, p["database"])
    assert result["ranking"]["topic_candidates"] == 1
    assert len(result["items"]) == 1
    item = result["items"][0]
    assert "topic_candidate" in item["retrieval_methods"]
    # A narrower originally retrieved witness can survive deduplication, but
    # the audited qualifying context and its reading link must also survive.
    assert item["source_char_start"] == lexical["source_char_start"]
    assert item["source_char_end"] == lexical["source_char_end"]
    assert item["context_char_start"] == selected["context_char_start"]
    assert item["context_char_end"] == selected["context_char_end"]
    assert item["context_text"] == p["text"][selected["context_char_start"]:selected["context_char_end"]]
    assert item["context_reading_view"]["source_char_start"] == selected["context_char_start"]
    assert item["context_reading_view"]["source_char_end"] == selected["context_char_end"]
    assert f"start={selected['context_char_start']}&end={selected['context_char_end']}" in item["reader_url"]
    assert item["stance"] == "contested"
    assert item["review_status"] == "candidate"
    # Different ranges do not create an additional RRF vote for this witness.
    assert item["scores"]["rrf"] == pytest.approx(1 / 61)


def test_human_retargeting_retires_old_candidate_without_deleting_its_history(audited_sample):
    p = audited_sample
    old = p["excluded"]
    target_topic = p["supported"]["topic_id"]
    assert target_topic != old["topic_id"]
    with connect(p["database"]) as db:
        db.execute("UPDATE pilot_evidence SET agent_decision='supported',rationale='تصحيح اصطناعي للموضوع' WHERE id=?", (old["id"],))
    with TestClient(create_app(p["database"], p["source"])) as client:
        result = client.post(f"/api/pilot/reviews/{old['id']}", json={
            "decision": "reviewed", "reviewer": "مراجع اصطناعي", "note": "تعديل موضوع مصطنع", "topic_id": target_topic,
        })
        assert result.status_code == 200
    with connect(p["database"]) as db:
        previous = db.execute("SELECT * FROM topic_hits WHERE id=?", (old["original_hit_id"],)).fetchone()
        assert previous is not None
        assert previous["topic_id"] == old["topic_id"]
        assert previous["review_status"] == "rejected"
        replacement = db.execute("SELECT * FROM topic_hits WHERE segment_id=? AND topic_id=?", (old["segment_id"], target_topic)).fetchone()
        assert replacement and replacement["review_status"] == "reviewed"
        assert replacement["evidence_text"] == p["text"][replacement["source_char_start"]:replacement["source_char_end"]]
        assert db.execute("SELECT COUNT(*) FROM pilot_events WHERE evidence_id=?", (old["id"],)).fetchone()[0] == 1


def test_three_rrf_channels_preserve_provenance_without_automatic_approval():
    from backend.smart_search import rrf_fuse
    literal = {"id": 1, "source_id": 1, "biography_id": 1, "source_char_start": 10, "source_char_end": 30, "clean_text": "نص اصطناعي"}
    topical = {**literal, "topic_audit_id": 17, "topic_id": 11, "topic_name": "تجربة", "topic_review_status": "pending", "stance": "accusation", "subject_scope": "biography_subject", "rationale": "اتهام منقول"}
    result = rrf_fuse([literal], [{**literal, "cosine": 0.9}], topical=[topical, topical])
    assert len(result) == 1
    row = result[0]
    assert row["scores"]["rrf"] == pytest.approx(3 / 61)
    assert row["scores"]["lexical_rank"] == row["scores"]["semantic_rank"] == row["scores"]["topic_rank"] == 1
    assert row["topic_review_status"] == "pending" and row["stance"] == "accusation"
    assert "review_status" not in row


def test_explicit_pilot_and_reviewed_filters_do_not_expand_to_global_results(audited_sample):
    from backend import smart_search
    p = audited_sample
    smart_search._CACHE.clear()
    global_result = smart_search.smart_search({"q": "مصطنعة"}, p["database"])
    restricted = smart_search.smart_search({"q": "مصطنعة", "plan": {"pilot_only": True}}, p["database"])
    assert global_result["items"]
    assert all(row.get("biography_id") in [d["biography_id"] for d in p["documents"]] for row in restricted["items"])
    assert smart_search.smart_search({"q": "مصطنعة", "plan": {"reviewed_only": True}}, p["database"])["total"] == 0
    assert smart_search.smart_search({"q": "مصطنعة", "plan": {"topic_ids": [], "topic_mode": "filter"}}, p["database"])["total"] == 0

