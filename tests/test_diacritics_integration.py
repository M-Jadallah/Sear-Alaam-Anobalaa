"""Disposable synthetic publication + read-time corruption probes."""
from contextlib import closing
import json

from backend import curation
from backend.db import connect
from backend.diacritics import derive_insertions
from backend.pilot import presentation
from scripts.audit_curation import audit
from test_curation import library, exported, author, write, qa


def publish_vocalized(library):
    batch = exported(library)
    manifest = author(batch)
    for entry in manifest["biographies"]:
        folder = batch/entry["directory"]
        bundle = curation._bundle(batch, entry)
        text = bundle["source"]["full_text"]
        at = entry["source_char_start"]+text.index("خبر")
        doc = bundle["diacritics"]
        doc["insertions"] = derive_insertions("خبر", "خَبَرٌ", at)
        word = next(w for w in doc["words"] if w["start"] == at)
        word.update(status="complete", reason="مبتدأ في الجملة الاصطناعية الخبرية")
        write(folder/"diacritics.json", doc)
        bundle = curation._bundle(batch, entry)
        proof = bundle["audit"]
        proof["authoring_sha256"] = curation.authoring_digest(bundle)
        write(folder/"audit.json", proof)
    preview = curation.preview_batch(batch, db_path=library["db"], source_path=library["source"])
    qa(batch, preview)
    published = curation.publish_batch(batch, db_path=library["db"], source_path=library["source"])
    return published


def test_real_api_shape_has_independent_original_runs_and_shaped_copy(library):
    published = publish_vocalized(library)
    p = presentation(library["db"], 6)
    assert p["diacritics"]["status"] == "partial"
    assert p["diacritics"]["coverage"]["complete_words"] == 1
    assert "خَبَرٌ" in p["vocalized_plain_text"]
    assert "خَبَرٌ" not in p["plain_text"]
    assert "تشكيل تحريري" in p["vocalized_plain_text"]
    runs = "".join(r["text"] for b in p["reading_view"]["blocks"] for r in b["runs"])
    assert "خَبَرٌ" not in runs
    preserved = audit(library["db"], published["backup"], [6, 7], library["source"])
    assert preserved["passed"]
    assert all(row["vocalization_valid"] for row in preserved["presentations"])


def test_stale_corrupt_and_failed_reading_never_apply_editorial_marks(library):
    published = publish_vocalized(library)
    original = presentation(library["db"], 6)
    with closing(connect(library["db"])) as db, db:
        db.execute("UPDATE curation_diacritics SET revision=? WHERE biography_id=?", ("stale-revision", 6))
    stale = presentation(library["db"], 6)
    assert stale["diacritics"]["status"] == "invalid"
    assert "vocalized_plain_text" not in stale
    assert stale["plain_text"] == original["plain_text"]
    assert not audit(library["db"], published["backup"], [6, 7], library["source"])["passed"]
    with closing(connect(library["db"])) as db, db:
        row = db.execute("SELECT payload FROM curation_diacritics WHERE biography_id=?", (6,)).fetchone()
        doc = json.loads(row[0]); doc["words"][0]["status"] = "unexamined"
        db.execute("UPDATE curation_diacritics SET revision=?,payload=? WHERE biography_id=?", (original["diacritics"]["revision"], json.dumps(doc), 6))
    corrupt = presentation(library["db"], 6)
    assert corrupt["diacritics"]["status"] == "invalid"
    assert "vocalized_plain_text" not in corrupt
    with closing(connect(library["db"])) as db, db:
        row = db.execute("SELECT payload FROM pilot_reading_overlays WHERE biography_id=?", (6,)).fetchone()
        doc = json.loads(row[0]); doc["paragraph_groups"][0]["end"] += 1
        db.execute("UPDATE pilot_reading_overlays SET payload=? WHERE biography_id=?", (json.dumps(doc), 6))
    failed_reading = presentation(library["db"], 6)
    assert "warning" in failed_reading
    assert "vocalized_plain_text" not in failed_reading
    assert not failed_reading.get("diacritics")
