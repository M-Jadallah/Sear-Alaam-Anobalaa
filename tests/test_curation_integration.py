"""Independent integration probes for range curation, on synthetic books only."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend.db import connect
from backend.importer import import_book
from backend.reading import build_reading_view


@pytest.fixture
def ranged_book(book, tmp_path):
    # A second source makes its first biography's ID differ from its ordinal.
    text = "######OpenITI#\n#META# 020.BookTITLE :: عينة دفعات مصطنعة وليست من الكتاب\n#META#Header#End#\n"
    for number in range(1, 8):
        text += f"### $ {1 if number > 4 else number} - شخصية مصطنعة {number}\n"
        text += f"# هذا نص مصطنع عن الإخلاص في الاختبار {number}، سنة (293).\n"
        text += "# قال الراوي: حديث غريب، لا أجزم بصحته (1).\n"
        text += f"PageV01P{number:03}\n"
    source = tmp_path / "range-synthetic.mARkdown"
    source.write_bytes(text.encode("utf-8"))
    report = import_book(source_path=source, db_path=book["database"], dictionary_path=Path(__file__).resolve().parents[1]/"reference/topic_dictionary.csv")
    return {"database": book["database"], "source": source, "source_id": report["source_id"], "work": tmp_path/"work"}


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def author_synthetic_batch(p, start, end):
    """Explicit invented test editorial decisions, never a corpus classifier."""
    from backend.curation import export_batch, authoring_digest, AUDIT_CHECKS
    output = export_batch(start, end, db_path=p["database"], source_path=p["source"], work_dir=p["work"])
    directory = Path(output["batch"])
    manifest = read(directory/"manifest.json")
    manifest["curator"] = "مؤلف العينة الاصطناعية"
    for entry in manifest["biographies"]:
        if entry["status"] == "skipped": continue
        folder = directory/entry["directory"]
        source, pilot, reading, ledger = [read(folder/(name+".json")) for name in ("source", "pilot", "reading", "footnotes")]
        pilot["reviewer"] = reading["reviewer"] = manifest["curator"]
        for candidate in ledger["candidates"]:
            candidate["decision"] = "verified_marker" if candidate["raw"] == "(1)" else "body_literal"
            candidate["reason"] = "إشارة مصطنعة للمراجعة بعد الكلام" if candidate["raw"] == "(1)" else "العدد جزء من تاريخ صريح في متن العينة المصطنعة"
        ledger.update(bodies_status="absent", body_ranges=[], notice="اختبار مصطنع: لم ترفق متون حواشٍ بهذه العينة")
        pilot["presentation"]["footnote_notice"] = ledger["notice"]
        pilot["presentation"]["footnotes"] = [{"start": item["start"], "end": item["end"]} for item in ledger["candidates"] if item["decision"] == "verified_marker"]
        for assessment, segment in zip(pilot["segments"], source["segments"]):
            assessment.update(assessment="no_clear_topic", note="مقطع اصطناعي قُرئ في سياق فحص نطاق الترجمة")
            for recommendation in assessment["existing_hits"]:
                recommendation.update(decision="uncertain", reason="مرشح اختباري محفوظ للمراجعة البشرية المستقلة")
        first = source["segments"][0]
        literal = "الإخلاص"
        at = first["source_char_start"] + first["text_original"].index(literal)
        pilot["segments"][0]["assessment"] = "supported"
        pilot["segments"][0]["topics"] = [{"topic": "موضوع اختبار دفعات", "category": "القلب والسلوك", "aliases": ["الإخلاص الاختباري"], "rationale": "الموضوع مذكور صراحة في نص اصطناعي", "relation_type": "discussion", "subject_scope": "general_context", "stance": "reported", "evidence_start": at, "evidence_end": at+len(literal), "context_start": first["source_char_start"], "context_end": first["source_char_end"]}]
        view = build_reading_view(source["full_text"], source["source_char_start"], starts_at_line_start=True)
        boundary = view["blocks"][0]["source_char_end"]
        reading["paragraph_groups"] = [{"start": source["source_char_start"], "end": boundary}, {"start": boundary, "end": source["source_char_end"]}]
        reading["paragraph_notes"] = [{**group, "context_start": group["start"], "context_end": group["end"], "reason": "حد تجريبي: عنوان مستقل ثم خبر الإخلاص وتعليق الراوي المتصل به"} for group in reading["paragraph_groups"]]
        diacritics = read(folder/"diacritics.json")
        diacritics["reviewer"] = manifest["curator"]
        for word in diacritics["words"]:
            if word["status"] == "unexamined":
                word.update(status="uncertain", reason="اختبار تكامل اصطناعي بلا ادعاء اعتماد تشكيل")
        write(folder/"diacritics.json", diacritics)
        second = source["segments"][1]
        criticism_at = second["source_char_start"] + second["text_original"].index("حديث غريب")
        reading["criticism"] = [{"start": criticism_at, "end": criticism_at+len("حديث غريب، لا أجزم بصحته"), "context_start": second["source_char_start"], "context_end": second["source_char_end"], "kind": "hadith", "reason": "حكم منقول مصطنع بنفي الجزم؛ لا يصحح التطبيق الحديث"}]
        gold = [{"question": "كيف يرد الحديث الغريب مع التحفظ؟", "start": criticism_at, "end": criticism_at+len("حديث غريب، لا أجزم بصحته")}]
        for name, value in (("pilot",pilot),("reading",reading),("footnotes",ledger),("gold",gold)):
            write(folder/(name+".json"), value)
        bundle = {name:read(folder/(name+".json")) for name in ("source","pilot","reading","footnotes","gold","diacritics")}
        audit = {"authoring_sha256": authoring_digest(bundle), "reviewer": "مدقق مستقل للعينة الاصطناعية", "all_segments_checked": True, "all_footnotes_checked": True, "findings": [], "checks": {key:True for key in AUDIT_CHECKS}}
        write(folder/"audit.json", audit)
    write(directory/"manifest.json", manifest)
    return directory


def mark_synthetic_qa(directory, proof):
    """Attest only the deliberately synthetic fixture; not real browser QA."""
    from backend.curation import QA_CHECKS
    path = directory/"synthetic-screen.png"
    # A one-pixel PNG is a fixture only; real QA uses browser screenshots.
    import base64
    path.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="))
    write(directory/"qa.json", {"revision":proof["revision"], "gold_frozen_sha256":proof["gold_frozen_sha256"], "reviewer":"اختبار بوابات نشر اصطناعي", "checks":{key:True for key in QA_CHECKS}, "screenshots":[str(path)]})


def test_two_source_ordered_batches_preserve_prior_review_and_stable_scope(ranged_book):
    from backend.curation import preview_batch, publish_batch, export_batch, list_batches, batch_biography_ids
    from backend.pilot import presentation, search_scope, topic_candidates
    from scripts.audit_curation import audit
    p = ranged_book
    first = author_synthetic_batch(p, 1, 2)
    manifest = read(first/"manifest.json")
    ids = [item["id"] for item in manifest["biographies"]]
    assert ids != [1,2]
    proof = preview_batch(first, db_path=p["database"], source_path=p["source"])
    mark_synthetic_qa(first, proof)
    result = publish_batch(first, db_path=p["database"], source_path=p["source"])
    assert result["status"] == "published"
    assert audit(p["database"], result["backup"], ids, p["source"])["passed"]
    # Preserve human decisions when adding the next, disjoint batch.
    with closing(connect(p["database"])) as db:
        evidence = db.execute("SELECT id FROM pilot_evidence WHERE biography_id=?", (ids[0],)).fetchone()[0]
        with db:
            db.execute("UPDATE pilot_evidence SET human_status='reviewed',human_reviewer='إنسان اصطناعي' WHERE id=?", (evidence,))
            db.execute("UPDATE pilot_coverage SET human_checked=1 WHERE biography_id=?", (ids[0],))
        old = tuple(db.execute("SELECT * FROM pilot_evidence WHERE id=?", (evidence,)).fetchone())
    next_batch = author_synthetic_batch(p, 3, 4)
    next_ids = [item["id"] for item in read(next_batch/"manifest.json")["biographies"]]
    preview = preview_batch(next_batch, db_path=p["database"], source_path=p["source"])
    mark_synthetic_qa(next_batch, preview)
    published = publish_batch(next_batch, db_path=p["database"], source_path=p["source"])
    assert audit(p["database"], published["backup"], next_ids, p["source"])["passed"]
    with closing(connect(p["database"])) as db:
        assert tuple(db.execute("SELECT * FROM pilot_evidence WHERE id=?", (evidence,)).fetchone()) == old
        assert batch_biography_ids(db, manifest["batch_id"]) == ids
        assert search_scope(p["database"], {"batch_id":manifest["batch_id"]})[0] == ids
        assert all(row["biography_id"] in ids for row in topic_candidates(p["database"], "", {"batch_id":manifest["batch_id"], "topic_ids":[db.execute("SELECT topic_id FROM pilot_evidence WHERE id=?", (evidence,)).fetchone()[0]]}))
    shown = presentation(p["database"], ids[0])
    assert "(293)" in shown["plain_text"]
    assert "حديث غريب، لا أجزم بصحته" in shown["plain_text"]
    assert shown["footnote_notice"].startswith("اختبار مصطنع")
    assert publish_batch(first, db_path=p["database"], source_path=p["source"])["status"] == "unchanged"
    overlap = export_batch(2, 3, db_path=p["database"], source_path=p["source"], work_dir=p["work"])
    assert [entry["status"] for entry in overlap["manifest"]["biographies"]] == ["skipped","skipped"]


def test_stale_audit_and_failed_qa_cannot_publish(ranged_book):
    from backend.curation import validate_batch, preview_batch, publish_batch
    p = ranged_book
    batch = author_synthetic_batch(p, 5, 5)
    assert validate_batch(batch, db_path=p["database"], source_path=p["source"])["valid"]
    pilot_path = batch/"biography-5"/"pilot.json"
    original = read(pilot_path)
    changed = deepcopy(original); changed["segments"][0]["note"] += " تغيير لاحق"
    write(pilot_path, changed)
    with pytest.raises(ValueError, match="تدقيق"):
        validate_batch(batch, db_path=p["database"], source_path=p["source"])
    write(pilot_path, original)
    proof = preview_batch(batch, db_path=p["database"], source_path=p["source"])
    mark_synthetic_qa(batch, proof)
    qa = read(batch/"qa.json"); qa["checks"]["copy_raw"] = False; write(batch/"qa.json", qa)
    with pytest.raises(ValueError, match="QA"):
        publish_batch(batch, db_path=p["database"], source_path=p["source"])
    with closing(connect(p["database"])) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='curation_batches'").fetchone() is None


def test_http_catalog_filters_and_human_decision_cannot_cross_batch(ranged_book, monkeypatch):
    from backend.curation import preview_batch, publish_batch
    from backend.app import create_app
    from fastapi.testclient import TestClient
    p = ranged_book; monkeypatch.setenv("SIYAR_SEMANTIC_DISABLED", "1")
    batch = author_synthetic_batch(p, 6, 7)
    proof = preview_batch(batch, db_path=p["database"], source_path=p["source"])
    mark_synthetic_qa(batch, proof); publish_batch(batch, db_path=p["database"], source_path=p["source"])
    bid = read(batch/"manifest.json")["batch_id"]
    with TestClient(create_app(p["database"], p["source"])) as client:
        catalog = client.get("/api/curation/batches").json()
        assert next(item for item in catalog["items"] if item["id"] == bid)["start_ordinal"] == 6
        response = client.get("/api/pilot/reviews", params={"batch_id":bid,"page_size":100}).json()
        assert response["items"]
        item = response["items"][0]
        bad = client.post(f"/api/pilot/reviews/{item['id']}", json={"batch_id":"unknown", "decision":"reviewed", "reviewer":"إنسان اختباري"})
        assert bad.status_code in (404,409,422)
        for route in ("/api/pilot/reviews", "/api/pilot/coverage"):
            assert client.get(route, params={"batch_id":"unknown"}).status_code in (404,422)
