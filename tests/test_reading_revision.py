"""Published reading corrections against disposable invented books only."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path

import pytest

from backend import curation, reading_revision as revisions
from backend.db import connect
from backend.diacritics import derive_insertions
from backend.pilot import presentation
from backend.pilot_reading import effective_annotations
from test_curation import library, exported, author, preview_and_publish, write, table_rows


PROTECTED = ("sources", "biographies", "segments", "topics", "topic_hits", "review_log",
             "pilot_documents", "pilot_coverage", "pilot_evidence", "pilot_events", "pilot_topic_aliases",
             "curation_batches", "curation_batch_members")


@pytest.fixture
def published(library):
    batch = exported(library)
    author(batch)
    preview_and_publish(library, batch)
    return library


def correction(p):
    return Path(revisions.export_batch(6, 7, db_path=p["db"], source_path=p["source"], work_dir=p["work"])["batch"])


def authored(directory):
    _, manifest, bundles = revisions._load(directory)
    manifest.update(curator="مؤلف تصحيح مصطنع", reason="تحسين قراءة عينة اصطناعية دون تغيير الموضوعات")
    write(directory/"manifest.json", manifest)
    for identifier, bundle in bundles.items():
        target = directory/f"biography-{bundle['source']['source_ordinal']}"
        reading, diacritics, source = bundle["reading"], bundle["diacritics"], bundle["source"]
        reading["reviewer"] = diacritics["reviewer"] = manifest["curator"]
        for note in reading["paragraph_notes"]:
            note["reason"] = "خبر مستقل في نص اصطناعي؛ فحص اتصال الجملة بالسياق السابق واللاحق"
        at = source["source_char_start"] + source["full_text"].index("خبر")
        diacritics["insertions"] = derive_insertions("خبر", "خَبَرٌ", at)
        for word in diacritics["words"]:
            if word["start"] == at:
                word["status"] = "complete"
            elif word["status"] == "unexamined":
                word.update(status="uncertain", reason="اختبار بنيوي مصطنع لا يدعي الاجتهاد اللغوي")
        for key, obj in (("reading", reading), ("diacritics", diacritics)):
            write(target/(key+".json"), obj)
        _, _, fresh = revisions._load(directory)
        audit = {"authoring_sha256": revisions.authoring_digest(fresh[identifier]), "reviewer": "مدقق تصحيح مصطنع مستقل",
                 "all_words_checked": True, "all_paragraphs_checked": True, "findings": [],
                 "checks": {key: True for key in revisions.AUDIT_CHECKS}}
        write(target/"audit.json", audit)
    return manifest


def qapass(directory, proof):
    import base64
    image = directory/"synthetic-not-corpus-QA.png"
    image.write_bytes(base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aY9sAAAAASUVORK5CYII="))
    write(directory/"qa.json", {"revision": proof["revision"], "content_sha256": proof["content_sha256"],
          "reviewer": "فاحص واجهة مصطنع", "checks": {key: True for key in curation.QA_CHECKS}, "screenshots": [str(image)]})


def ready(p, directory):
    proof = revisions.preview_batch(directory, db_path=p["db"], source_path=p["source"])
    qapass(directory, proof)
    return proof


def test_export_requires_published_source_and_is_readonly_resumable(library):
    with pytest.raises(ValueError, match="منشورة"):
        correction(library)
    old = exported(library); author(old); preview_and_publish(library, old)
    before = {name: table_rows(library["db"], name) for name in PROTECTED}
    directory = correction(library)
    source = revisions._read_json(directory/"biography-6/source.json")
    assert source["reading_row"]["revision"] and source["pilot"]
    draft = directory/"biography-6/reading.json"
    draft.write_text(draft.read_text(encoding="utf-8")+"\n", encoding="utf-8")
    text = draft.read_bytes()
    assert revisions.export_batch(6, 7, db_path=library["db"], source_path=library["source"], work_dir=library["work"])["status"] == "resumed"
    assert draft.read_bytes() == text
    assert {name: table_rows(library["db"], name) for name in PROTECTED} == before


def test_publish_archives_old_rows_and_preserves_all_source_topic_and_review_state(published):
    directory = correction(published); authored(directory)
    before = {name: table_rows(published["db"], name) for name in PROTECTED}
    original_reading = table_rows(published["db"], "pilot_reading_overlays")
    originals = {i: presentation(published["db"], i) for i in (6, 7)}
    proof = ready(published, directory)
    assert {name: table_rows(published["db"], name) for name in PROTECTED} == before
    assert table_rows(published["db"], "pilot_reading_overlays") == original_reading
    result = revisions.publish_batch(directory, db_path=published["db"], source_path=published["source"])
    assert result["status"] == "published" and Path(result["backup"]).is_file()
    assert {name: table_rows(published["db"], name) for name in PROTECTED} == before
    with closing(connect(published["db"])) as db:
        rows = db.execute("SELECT * FROM reading_revision_history ORDER BY biography_id").fetchall()
        assert len(rows) == 2
        for row in rows:
            old = json.loads(row["previous_reading_json"])
            assert old["revision"] == originals[row["biography_id"]]["presentation_revision"]
            assert row["reviewer"] != row["curator"]
            assert row["reason"]
    p = presentation(published["db"], 6)
    assert p["revision"] == originals[6]["revision"]  # Topic edition is untouched.
    assert p["presentation_revision"] == proof["revision"]
    assert p["reading_view"] == originals[6]["reading_view"]
    assert "خَبَرٌ" in p["vocalized_plain_text"]
    assert revisions.publish_batch(directory, db_path=published["db"], source_path=published["source"])["status"] == "unchanged"
    assert len(table_rows(published["db"], "reading_revision_history")) == 2


@pytest.mark.parametrize("table", ["pilot_documents", "pilot_reading_overlays", "curation_diacritics"])
def test_optimistic_concurrency_detects_even_payload_only_changes(published, table):
    directory = correction(published); authored(directory)
    with closing(connect(published["db"])) as db, db:
        row = db.execute('SELECT payload FROM "'+table+'" WHERE biography_id=?', (6,)).fetchone()
        payload = json.loads(row[0]); payload["unexpected_change"] = "concurrent edit in synthetic copy"
        db.execute('UPDATE "'+table+'" SET payload=? WHERE biography_id=?', (json.dumps(payload), 6))
    with pytest.raises(ValueError, match="أثناء التأليف"):
        revisions.validate_batch(directory, db_path=published["db"], source_path=published["source"])


def test_self_old_audit_and_old_qa_block_publication(published):
    directory = correction(published); authored(directory)
    path = directory/"biography-6/audit.json"
    audit = revisions._read_json(path); audit["reviewer"] = "مؤلف تصحيح مصطنع"; write(path, audit)
    with pytest.raises(ValueError, match="مستقل"):
        revisions.validate_batch(directory, db_path=published["db"], source_path=published["source"])
    authored(directory)
    with pytest.raises(ValueError, match="معاينة"):
        revisions.publish_batch(directory, db_path=published["db"], source_path=published["source"])
    proof = ready(published, directory)
    q = revisions._read_json(directory/"qa.json"); q["content_sha256"] = "old"; write(directory/"qa.json", q)
    with pytest.raises(ValueError, match="QA"):
        revisions.publish_batch(directory, db_path=published["db"], source_path=published["source"])
    qapass(directory, proof)
    reading_path = directory/"biography-6/reading.json"
    reading = revisions._read_json(reading_path); reading["paragraph_notes"][0]["reason"] += " تعديل"; write(reading_path, reading)
    with pytest.raises(ValueError, match="مستقل"):
        revisions.validate_batch(directory, db_path=published["db"], source_path=published["source"])


def test_failure_during_second_install_rolls_back_readings_history_and_new_tables(published, monkeypatch):
    directory = correction(published); authored(directory); ready(published, directory)
    before = table_rows(published["db"], "pilot_reading_overlays")
    before_diacritics = table_rows(published["db"], "curation_diacritics")
    real = revisions._install
    def fail(db, manifest, entry, bundle, revision):
        if entry["source_ordinal"] == 7:
            raise RuntimeError("injected second-row failure")
        return real(db, manifest, entry, bundle, revision)
    monkeypatch.setattr(revisions, "_install", fail)
    with pytest.raises(RuntimeError, match="injected"):
        revisions.publish_batch(directory, db_path=published["db"], source_path=published["source"])
    assert table_rows(published["db"], "pilot_reading_overlays") == before
    assert table_rows(published["db"], "curation_diacritics") == before_diacritics
    with closing(connect(published["db"])) as db:
        assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='reading_revision_history'").fetchone()


def test_label_vocalization_cannot_replace_words_or_remove_source_marks():
    annotations = {"headings": [{"at": 10, "label": "خبر رجل"}]}
    document = {"source_char_start": 0, "source_char_end": 100, "heading_labels": [{"at": 10, "label": "خَبَرُ رَجُلٍ"}]}
    assert effective_annotations(annotations, document)["headings"][0]["label"] == "خَبَرُ رَجُلٍ"
    assert annotations["headings"][0]["label"] == "خبر رجل"
    document["heading_labels"][0]["label"] = "خبر راو"
    with pytest.raises(ValueError): effective_annotations(annotations, document)
    annotations["headings"][0]["label"] = "خَبر"
    document["heading_labels"][0]["label"] = "خُبر"
    with pytest.raises(ValueError): effective_annotations(annotations, document)


def test_new_explicit_revision_keeps_prior_revision_history(published):
    first = correction(published); authored(first); ready(published, first)
    old_result = revisions.publish_batch(first, db_path=published["db"], source_path=published["source"])
    second = correction(published)
    assert second != first
    authored(second); ready(published, second)
    result = revisions.publish_batch(second, db_path=published["db"], source_path=published["source"])
    assert result["revision"] != old_result["revision"]
    assert len(table_rows(published["db"], "reading_revision_history")) == 4
    with pytest.raises(ValueError, match="تغيرت بعد"):
        revisions.publish_batch(first, db_path=published["db"], source_path=published["source"])
