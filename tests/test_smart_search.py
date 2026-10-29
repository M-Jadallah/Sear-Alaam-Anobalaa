"""Read-only smart-search contracts, fallback, and ranking without real inference."""
import copy
from concurrent.futures import Future
from concurrent.futures.process import BrokenProcessPool
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def smart_module(book, monkeypatch, tmp_path):
    from backend import smart_search as module

    module._CACHE.clear()
    monkeypatch.setenv("SIYAR_SEMANTIC_DIR", str(tmp_path / "no-semantic-index"))
    monkeypatch.setenv("SIYAR_SEMANTIC_DISABLED", "1")
    yield module
    module._CACHE.clear()


def test_interpretation_preserves_original_query_without_quotes_counts_or_results(book, smart_module):
    query = "أريد مواقف رفض فيها عالم مالًا أو منصبًا حفاظًا على دينه"
    result = smart_module.interpret_query(query, book["database"])
    assert result["query"] == query
    assert result["method"] == "local_rules"
    assert 1 <= len(result["interpretations"]) <= 3
    assert not {"items", "results", "total", "count", "evidence_text", "text_original"}.intersection(result)
    assert any(card["meaning"] == query for card in result["interpretations"])
    assert any("بدائل" in flag for flag in result["uncertainty_flags"])
    for card in result["interpretations"]:
        assert card["query_text"] == query
        assert card["uncertainty"]
        assert card["include_terms"] == []
        assert isinstance(card["expansion_terms"], list)
        assert not {"items", "results", "total", "count", "evidence_text", "text_original"}.intersection(card)


def test_local_interpretation_handles_negation_cautiously_and_keeps_exclusions_editable(book, smart_module):
    query = "أريد مواقف الزهد مع استبعاد الشهرة ولكن أبحث عن قبول النصيحة"
    result = smart_module.interpret_query(query, book["database"])
    assert result["query"] == query
    for card in result["interpretations"]:
        assert card["exclude_terms"] == ["الشهرة"]
        assert card["uncertainty"]
    tawhid = smart_module.interpret_query("لا إله إلا الله", book["database"])
    assert all(not card["exclude_terms"] for card in tawhid["interpretations"])


def test_explicit_exclusion_stops_before_unprefixed_lakin(book, smart_module):
    query = "لا أريد الشهرة لكن أريد طلب العلم"
    result = smart_module.interpret_query(query, book["database"])
    assert all(card["exclude_terms"] == ["الشهرة"] for card in result["interpretations"])
    assert all("طلب العلم" in card["meaning"] for card in result["interpretations"])


def test_empty_default_meaning_keeps_original_query_fallback(book, smart_module):
    omitted = smart_module.smart_search({"q": "الإخلاص", "plan": {}}, book["database"])
    empty = smart_module.smart_search({"q": "الإخلاص", "plan": {"meaning": ""}}, book["database"])
    assert omitted["items"] and empty["items"]
    assert [item["source_char_start"] for item in omitted["items"]] == [item["source_char_start"] for item in empty["items"]]


def test_missing_models_return_explicit_lexical_candidates_exact_source_and_never_persist_labels(book, db, smart_module):
    before = {table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in ("review_log", "topic_hits", "person_mentions")}
    result = smart_module.smart_search({"q": "الإخلاص", "plan": {"meaning": "الإخلاص", "include_terms": [], "exclude_terms": [], "expansion_terms": [], "topic_ids": []}}, book["database"])
    assert result["mode"] == "lexical_only"
    assert result["warning"]
    assert result["semantic_status"]["ready"] is False
    assert result["total_scope"] == "retrieved_candidates_not_book_coverage"
    assert result["ranking"]["semantic_candidates"] == 0
    assert result["items"]
    for item in result["items"]:
        assert item["match_type"] == "keyword_candidate"
        assert item["review_status"] == "candidate"
        assert item["text_original"] == item["evidence_text"] == book["text"][item["source_char_start"]:item["source_char_end"]]
        assert item["reading_view"]["coverage"]["complete"] is True
        assert item["page_reference_status"] == "raw_unverified"
    assert before == {table: db.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] for table in before}


def test_required_words_all_filter_and_expansion_words_do_not_become_required(book, smart_module):
    required = smart_module.smart_search({"q": "الإخلاص الصدق", "plan": {"meaning": "بحث محدد", "include_terms": ["الإخلاص", "الصدق"], "exclude_terms": [], "expansion_terms": [], "topic_ids": []}}, book["database"])
    assert required["total"] == 1
    assert "الإخلاص" in required["items"][0]["text_original"] and "الصدق" in required["items"][0]["text_original"]
    impossible = smart_module.smart_search({"q": "الإخلاص", "plan": {"include_terms": ["لفظغيرموجودقطعًا"], "expansion_terms": []}}, book["database"])
    assert impossible["total"] == 0
    expanded = smart_module.smart_search({"q": "الإخلاص", "plan": {"expansion_terms": ["لفظغيرموجودقطعًا"], "include_terms": []}}, book["database"])
    assert expanded["total"] > 0
    constrained = smart_module.smart_search({"q": "وصفغيرموجودقطعًا", "plan": {"meaning": "معنىغيرموجودقطعًا", "include_terms": ["الإخلاص"], "expansion_terms": []}}, book["database"])
    assert constrained["total"] > 0
    assert all("الإخلاص" in row["reading_view"]["plain_text"] or "الإِخْلَاصُ" in row["reading_view"]["plain_text"] for row in constrained["items"])


def test_excluded_phrase_removes_result_but_cannot_rewrite_other_source_text(book, smart_module):
    result = smart_module.smart_search({"q": "الإخلاص", "plan": {"exclude_terms": ["الصدق"], "include_terms": [], "expansion_terms": []}}, book["database"])
    assert result["total"] == 1
    assert "الصدق" not in result["items"][0]["text_original"]
    assert result["items"][0]["text_original"] in book["text"]


def test_selected_dictionary_concept_expands_retrieval_without_classifying_result(book, db, smart_module):
    topic_id = db.execute("SELECT id FROM topics WHERE name=?", ("الإخلاص",)).fetchone()[0]
    result = smart_module.smart_search({"q": "لفظغيرموجودقطعًا", "plan": {"topic_ids": [topic_id]}}, book["database"])
    assert result["items"]
    assert all(item["review_status"] == "candidate" for item in result["items"])


def test_edited_selected_meaning_drives_retrieval_while_original_query_stays_in_metadata(book, smart_module, monkeypatch):
    original_query = "الإخلاص"
    selected_meaning = "الصدق"
    original_lexical = smart_module._lexical_candidates
    observed_queries = []

    def observe(database, query, status):
        observed_queries.append(query)
        return original_lexical(database, query, status)

    monkeypatch.setattr(smart_module, "_lexical_candidates", observe)
    result = smart_module.smart_search({"q": original_query, "plan": {"meaning": selected_meaning, "include_terms": [], "exclude_terms": [], "expansion_terms": [], "topic_ids": []}}, book["database"])
    assert result["query"] == original_query
    assert result["interpretation"]["meaning"] == selected_meaning
    assert observed_queries and all("الصدق" in query and "الاخلاص" not in query for query in observed_queries)
    assert result["items"]
    assert all("الصدق" in item["text_original"] for item in result["items"])


def test_rrf_agreement_improves_rank_and_overlaps_do_not_merge_people(smart_module):
    first = {"id": 1, "source_id": 1, "section_id": 1, "biography_id": 1, "source_char_start": 100, "source_char_end": 200}
    shared = {"id": 2, "source_id": 1, "section_id": 2, "biography_id": 2, "source_char_start": 300, "source_char_end": 400}
    another = {"id": 3, "source_id": 1, "section_id": 3, "biography_id": 3, "source_char_start": 500, "source_char_end": 600}
    rows = smart_module.rrf_fuse([first, shared], [shared, another])
    assert rows[0]["id"] == shared["id"]
    assert rows[0]["scores"]["lexical_rank"] == 2
    assert rows[0]["scores"]["semantic_rank"] == 1
    assert rows[0]["scores"]["rrf"] == pytest.approx(1 / 62 + 1 / 61)
    overlapping = {**first, "id": 4, "source_char_start": 120, "source_char_end": 210}
    other_source = {**first, "id": 5, "source_id": 2}
    unique = smart_module.deduplicate_overlaps([first, overlapping, shared, other_source])
    assert {row["id"] for row in unique} == {1, 2, 5}
    assert {row["biography_id"] for row in unique} == {1, 2}


def test_rrf_each_channel_votes_once_per_literal_span_and_preserves_first_rank(smart_module):
    first = {"id": 1, "source_id": 1, "section_id": 1, "biography_id": 1,
             "heading": "اسم مشترك مصطنع", "source_char_start": 100, "source_char_end": 200,
             "granularity": "context", "cosine": 0.9}
    duplicate = {**first, "id": -1, "granularity": "paragraph", "cosine": 0.7}
    # The same heading cannot merge separate biographies or separate sources.
    another_person = {**first, "id": 2, "section_id": 2, "biography_id": 2,
                      "source_char_start": 300, "source_char_end": 400}
    another_source = {**first, "id": 3, "source_id": 2, "section_id": 3, "biography_id": 3}
    rows = smart_module.rrf_fuse([first, duplicate, another_person],
                                 [first, duplicate, another_person, duplicate, another_source])
    assert len(rows) == 3
    by_id = {row["id"]: row for row in rows}
    assert by_id[1]["scores"]["lexical_rank"] == by_id[1]["scores"]["semantic_rank"] == 1
    assert by_id[1]["scores"]["rrf"] == pytest.approx(2 / 61)
    assert by_id[1]["scores"]["cosine"] == first["cosine"]
    assert by_id[2]["scores"]["lexical_rank"] == by_id[2]["scores"]["semantic_rank"] == 2
    assert by_id[2]["scores"]["rrf"] == pytest.approx(2 / 62)
    assert by_id[3]["scores"]["semantic_rank"] == 3
    assert by_id[3]["scores"]["rrf"] == pytest.approx(1 / 63)
    assert {row["biography_id"] for row in rows} == {1, 2, 3}


def test_cache_has_page_independence_deep_copy_ttl_and_review_log_invalidation(book, db, smart_module, monkeypatch):
    request = {"q": "الإخلاص", "page_size": 1}
    first = smart_module.smart_search(request, book["database"])
    assert first["cache_hit"] is False
    first["items"][0]["text_original"] = "modified test result"
    second = smart_module.smart_search({**request, "page": 2}, book["database"])
    assert second["cache_hit"] is True
    again = smart_module.smart_search(request, book["database"])
    assert again["items"][0]["text_original"] != "modified test result"
    assert second["items"][0]["source_char_start"] != again["items"][0]["source_char_start"]
    db.execute("INSERT INTO review_log(entity_type,entity_id,change_summary,reviewer) VALUES (?,?,?,?)", ("test", 0, "synthetic isolated cache test", "test"))
    db.commit()
    changed = smart_module.smart_search(request, book["database"])
    assert changed["cache_hit"] is False
    now = smart_module.time.monotonic()
    monkeypatch.setattr(smart_module.time, "monotonic", lambda: now + smart_module.CACHE_SECONDS + 1)
    expired = smart_module.smart_search(request, book["database"])
    assert expired["cache_hit"] is False


def test_result_cache_obeys_heap_budget_and_skips_an_oversized_entry(book, smart_module, monkeypatch):
    module = smart_module
    first = {"q": "الإخلاص"}
    module.smart_search(first, book["database"])
    size = next(iter(module._CACHE.values()))[3]
    monkeypatch.setattr(module, "CACHE_BYTES_LIMIT", int(size * 1.6))
    module.smart_search({"q": "الإخلاص الصدق"}, book["database"])
    assert sum(entry[3] for entry in module._CACHE.values()) <= module.CACHE_BYTES_LIMIT
    assert len(module._CACHE) == 1
    assert module.smart_search(first, book["database"])["cache_hit"] is False
    module._CACHE.clear()
    monkeypatch.setattr(module, "CACHE_BYTES_LIMIT", 1)
    assert module.smart_search(first, book["database"])["items"]
    assert not module._CACHE
    assert module.smart_search(first, book["database"])["cache_hit"] is False


def test_cache_invalidates_for_model_source_and_index_revision(book, smart_module, monkeypatch):
    original_status = smart_module.semantic_status(book["database"])
    mutable = {**original_status, "model_fingerprint": "model-a", "fingerprint": "index-a"}
    monkeypatch.setattr(smart_module, "semantic_status", lambda *args, **kwargs: copy.deepcopy(mutable))
    request = {"q": "الإخلاص"}
    assert smart_module.smart_search(request, book["database"])["cache_hit"] is False
    assert smart_module.smart_search(request, book["database"])["cache_hit"] is True
    mutable["model_fingerprint"] = "model-b"
    assert smart_module.smart_search(request, book["database"])["cache_hit"] is False
    mutable["fingerprint"] = "index-b"
    assert smart_module.smart_search(request, book["database"])["cache_hit"] is False
    mutable["sources"] = [dict(source, sha256="new-source-sha") for source in mutable["sources"]]
    assert smart_module.smart_search(request, book["database"])["cache_hit"] is False


def test_failed_optional_inference_falls_back_without_semantic_label_or_source_mutation(book, smart_module, monkeypatch):
    status = smart_module.semantic_status(book["database"])
    monkeypatch.setattr(smart_module, "semantic_status", lambda *args, **kwargs: {**status, "ready": True, "status": "ready", "fingerprint": "synthetic-ready", "model_fingerprint": "fake"})
    monkeypatch.setattr(smart_module, "_vectors", lambda status: None)
    original_lexical = smart_module._lexical_candidates
    monkeypatch.setattr(smart_module, "_lexical_candidates", lambda db_path, query, state: original_lexical(db_path, query, {**state, "ready": False}))

    def unavailable(*args, **kwargs):
        raise RuntimeError("synthetic model failure")

    monkeypatch.setattr(smart_module, "_semantic_candidates", unavailable)
    result = smart_module.smart_search({"q": "الإخلاص"}, book["database"])
    assert result["mode"] == "lexical_only"
    assert "synthetic model failure" in result["warning"]
    assert all(row["match_type"] == "keyword_candidate" for row in result["items"])
    assert result["ranking"]["semantic_candidates"] == result["ranking"]["reranked"] == 0


def test_smart_api_validates_local_request_and_interpretation_has_no_source_evidence(book, monkeypatch, tmp_path):
    from backend.app import create_app

    monkeypatch.setenv("SIYAR_SEMANTIC_DISABLED", "1")
    with TestClient(create_app(book["database"], book["source"])) as client:
        result = client.post("/api/search/interpret", json={"q": "مواقف الإخلاص"})
        assert result.status_code == 200
        assert "interpretations" in result.json() and "items" not in result.json()
        assert client.post("/api/search/interpret", json={"q": "   "}).status_code == 422
        assert client.post("/api/search/smart", json={"q": "الإخلاص", "plan": {"meaning": " \t\n "}}).status_code == 422
        assert client.post("/api/search/smart", json={"q": "الإخلاص", "page_size": 11}).status_code == 422
        assert client.post("/api/search/smart", json={"q": "الإخلاص", "page": 0}).status_code == 422
        assert client.post("/api/search/interpret", json={"q": "الإخلاص"}, headers={"Origin": "https://untrusted.example"}).status_code == 403


@pytest.mark.parametrize("failure_stage", ["submit", "result"])
def test_broken_worker_returns_503_and_next_request_creates_a_new_worker(book, monkeypatch, failure_stage):
    from backend.app import create_app
    from backend import smart_search as module

    class BrokenExecutor:
        stopped = False

        def submit(self, *args, **kwargs):
            error = BrokenProcessPool("synthetic failed worker")
            if failure_stage == "submit":
                raise error
            future = Future()
            future.set_exception(error)
            return future

        def shutdown(self, **kwargs):
            self.stopped = True

    class FreshExecutor(BrokenExecutor):
        submitted = 0

        def submit(self, *args, **kwargs):
            self.submitted += 1
            future = Future()
            future.set_result({"result": {"items": [], "mode": "synthetic_recovered_worker"}})
            return future

    failed, fresh = BrokenExecutor(), FreshExecutor()
    created = []

    def factory(**kwargs):
        created.append(kwargs)
        return fresh

    monkeypatch.setattr(module, "_EXECUTOR", failed)
    monkeypatch.setattr(module, "ProcessPoolExecutor", factory)
    with TestClient(create_app(book["database"], book["source"])) as client:
        first = client.post("/api/search/smart", json={"q": "الإخلاص"})
        assert first.status_code == 503 and "تعطل عامل البحث" in first.json()["detail"]
        assert failed.stopped and module._EXECUTOR is None
        assert not created and fresh.submitted == 0, "The failed request must not retry automatically"
        assert client.get("/api/search", params={"q": "الإخلاص"}).status_code == 200
        second = client.post("/api/search/smart", json={"q": "الإخلاص"})
        assert second.status_code == 200 and second.json()["mode"] == "synthetic_recovered_worker"
        assert len(created) == fresh.submitted == 1
        assert created[0]["max_workers"] == 1
    module.shutdown_search_worker()


@pytest.mark.parametrize("paragraph_ready", [False, True])
def test_arabic_profile_keeps_valid_context_search_when_optional_sidecar_is_unavailable(book, smart_module, monkeypatch, tmp_path, paragraph_ready):
    from backend import morphology
    module = smart_module
    status = module.semantic_status(book["database"])
    original_lexical = module._lexical_candidates
    rows = original_lexical(book["database"], module._lexical_query("الإخلاص", {}), {"ready": False})
    semantic = [{**row, "granularity": "context", "cosine": .8} for row in rows]
    state = {**status, "ready": True, "status": "ready", "fingerprint": "synthetic-context",
             "model_fingerprint": "synthetic-model", "paragraph_status": {"ready": paragraph_ready, "directory": str(tmp_path)}}
    monkeypatch.setattr(module, "PROFILE_ID", "arabic-retrieval-v1")
    monkeypatch.setattr(module, "semantic_status", lambda *args: copy.deepcopy(state))
    monkeypatch.setattr(module, "_vectors", lambda ignored: None)
    monkeypatch.setattr(module, "_paragraph_vectors", lambda ignored: None)
    monkeypatch.setattr(module, "_lexical_candidates", lambda path, query, ignored: original_lexical(path, query, {"ready": False}))
    monkeypatch.setattr(module, "_semantic_candidates", lambda *args: copy.deepcopy(semantic))
    monkeypatch.setattr(module, "_rerank", lambda query, values, fingerprint: values)
    def unavailable(*args):
        if not paragraph_ready:
            pytest.fail("A paragraph-dependent sidecar was consulted without a valid paragraph index")
        raise RuntimeError("synthetic damaged sidecar")
    monkeypatch.setattr(morphology, "candidates", unavailable)
    result = module.smart_search({"q": "الإخلاص"}, book["database"])
    assert result["mode"] == "hybrid" and result["items"]
    assert all(row["evidence_text"] == book["text"][row["source_char_start"]:row["source_char_end"]] for row in result["items"])
    assert any(row["match_type"] == "semantic_candidate" for row in result["items"])
    if paragraph_ready:
        assert "اشتقاق" in result["warning"]
