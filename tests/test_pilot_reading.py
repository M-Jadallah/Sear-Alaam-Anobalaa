"""Reading-only migrations on isolated, explicitly synthetic books."""
from contextlib import closing
from copy import deepcopy
import json

import pytest

from backend.db import connect
from backend.pilot import prepare, presentation, pilot_revision
from backend.pilot_reading import VERSION, grouped_reading_view, prepare_reading, validate_reading_document
from backend.reading import build_reading_view


@pytest.fixture
def reading_book(book):
    pilots, overlays = [], []
    with closing(connect(book["database"])) as db:
        sha = db.execute("SELECT sha256 FROM sources").fetchone()[0]
        for bio in db.execute("SELECT * FROM biographies ORDER BY id"):
            segments = []
            for segment in db.execute("SELECT * FROM segments WHERE biography_id=?", (bio["id"],)):
                old = [{"hit_id": hit[0], "decision": "uncertain", "reason": "عينة اصطناعية؛ لا اعتماد"} for hit in db.execute("SELECT id FROM topic_hits WHERE segment_id=?", (segment["id"],))]
                segments.append({"segment_id": segment["id"], "assessment": "no_clear_topic", "note": "عينة اصطناعية لفحص الحفظ", "topics": [], "existing_hits": old})
            pilots.append({"source_sha256": sha, "biography_id": bio["id"], "source_char_start": bio["source_char_start"], "source_char_end": bio["source_char_end"], "reviewer": "وكيل اختبار", "segments": segments, "presentation": {k: [] for k in ("headings", "quotes", "poetry", "lists", "names", "bold", "keywords", "footnotes")}})
            view = build_reading_view(bio["full_text"], bio["source_char_start"], starts_at_line_start=True)
            title, end = view["blocks"][0], bio["source_char_end"]
            criticism = []
            if bio["id"] == 1:
                a = bio["source_char_start"] + bio["full_text"].index("لا يثبت")
                criticism = [{"start": a, "end": a + len("لا يثبت ذلك تصنيفًا عقديًا."), "context_start": title["source_char_end"], "context_end": end, "kind": "report", "reason": "قيد صريح في العينة الاصطناعية؛ ليس حكمًا حديثيًا"}]
            overlays.append({"version": VERSION, "source_sha256": sha, "biography_id": bio["id"], "source_char_start": bio["source_char_start"], "source_char_end": end, "reviewer": "وكيل اختبار", "paragraph_groups": [{"start": bio["source_char_start"], "end": title["source_char_end"]}, {"start": title["source_char_end"], "end": end}], "criticism": criticism})
    prepare(book["database"], pilots)
    return {**book, "pilots": pilots, "overlays": overlays}


def snapshot(db):
    tables = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name!='pilot_reading_overlays'")]
    return {table: sorted((tuple(row) for row in db.execute('SELECT * FROM "' + table + '"')), key=repr) for table in tables}


def test_reading_preparation_preserves_all_existing_rows_and_human_reviews(reading_book):
    p = reading_book
    with closing(connect(p["database"])) as db:
        with db:
            db.execute("UPDATE topic_hits SET review_status='reviewed',reviewer_note='قرار بشري اصطناعي سابق' WHERE id=(SELECT MIN(id) FROM topic_hits)")
            db.execute("UPDATE pilot_coverage SET human_checked=1 WHERE segment_id=(SELECT MIN(segment_id) FROM pilot_coverage)")
            db.execute("INSERT INTO pilot_events(actor_kind,reviewer,after_json,note) VALUES ('human','إنسان اختباري','{}','فحص حفظ قرار سابق')")
        before, revision = snapshot(db), pilot_revision(db)
    prepared = prepare_reading(p["database"], p["overlays"])
    assert prepared["status"] == "ready" and __import__("pathlib").Path(prepared["backup"]).exists()
    assert prepare_reading(p["database"], p["overlays"])["status"] == "unchanged"
    with closing(connect(p["database"])) as db:
        assert snapshot(db) == before
        assert pilot_revision(db) == revision
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
    result = presentation(p["database"], 1)
    assert result["presentation_revision"] == prepared["presentation_revision"]
    assert result["reading_view"]["coverage"]["complete"]
    assert len(result["reading_view"]["blocks"]) == 2
    assert result["criticism"] == p["overlays"][0]["criticism"]
    # Updating formatting after a human decision must keep that decision too.
    documents = deepcopy(p["overlays"])
    documents[0]["criticism"][0]["reason"] += " توضيح تحريري"
    assert prepare_reading(p["database"], documents)["status"] == "ready"
    with closing(connect(p["database"])) as db:
        assert snapshot(db) == before
        assert pilot_revision(db) == revision


@pytest.mark.parametrize("fault", ["gap", "overlap", "reverse", "source", "bounds", "title", "context", "kind", "reason", "duplicate", "page_cut"])
def test_reject_invalid_authored_ranges_without_publishing(reading_book, fault):
    p = reading_book
    documents = deepcopy(p["overlays"])
    doc, groups = documents[0], documents[0]["paragraph_groups"]
    if fault == "gap": groups[1]["start"] += 1
    elif fault == "overlap": groups[1]["start"] -= 1
    elif fault == "reverse": groups.reverse()
    elif fault == "source": doc["source_sha256"] = "0" * 64
    elif fault == "bounds": doc["source_char_end"] += 1
    elif fault == "title": doc["paragraph_groups"] = [{"start": doc["source_char_start"], "end": doc["source_char_end"]}]
    elif fault == "context": doc["criticism"][0]["context_end"] = doc["criticism"][0]["start"]
    elif fault == "kind": doc["criticism"][0]["kind"] = "صححه الوكيل"
    elif fault == "reason": doc["criticism"][0]["reason"] = ""
    elif fault == "duplicate": documents.append(deepcopy(doc))
    elif fault == "page_cut":
        from backend.pilot import _literal
        with closing(connect(p["database"])) as db:
            bio = db.execute("SELECT * FROM biographies WHERE id=1").fetchone()
            cut = doc["source_char_start"] + bio["full_text"].index("PageV") + 3
        groups[1:] = [{"start": groups[1]["start"], "end": cut}, {"start": cut, "end": doc["source_char_end"]}]
    with pytest.raises(ValueError):
        prepare_reading(p["database"], documents)
    with closing(connect(p["database"])) as db:
        assert db.execute("SELECT 1 FROM sqlite_master WHERE name='pilot_reading_overlays'").fetchone() is None


def test_failure_during_publication_rolls_back_every_biography(reading_book, monkeypatch):
    from backend import pilot_reading
    p = reading_book
    prepare_reading(p["database"], p["overlays"])
    with closing(connect(p["database"])) as db:
        before = [tuple(r) for r in db.execute("SELECT * FROM pilot_reading_overlays ORDER BY biography_id")]
    changed = deepcopy(p["overlays"])
    changed[0]["reviewer"] += " تحديث"
    original, count = pilot_reading.validate_reading_document, 0
    def interrupted(db, doc):
        nonlocal count
        count += 1
        if count == len(changed) + 2:
            raise RuntimeError("انقطاع مصطنع أثناء النشر")
        return original(db, doc)
    monkeypatch.setattr(pilot_reading, "validate_reading_document", interrupted)
    with pytest.raises(RuntimeError, match="انقطاع"):
        prepare_reading(p["database"], changed)
    with closing(connect(p["database"])) as db:
        assert [tuple(r) for r in db.execute("SELECT * FROM pilot_reading_overlays ORDER BY biography_id")] == before


def test_stale_or_invalid_overlay_falls_back_without_touching_base(reading_book):
    p = reading_book
    original = presentation(p["database"], 1)
    prepare_reading(p["database"], p["overlays"])
    with closing(connect(p["database"])) as db:
        with db: db.execute("UPDATE pilot_reading_overlays SET source_sha256=? WHERE biography_id=1", ("0" * 64,))
    stale = presentation(p["database"], 1)
    assert "presentation_revision" not in stale and stale["warning"]
    assert stale["reading_view"] == original["reading_view"]
    with closing(connect(p["database"])) as db:
        bad = deepcopy(p["overlays"][0]); bad["paragraph_groups"][0]["end"] -= 1
        with db: db.execute("UPDATE pilot_reading_overlays SET source_sha256=?,payload=? WHERE biography_id=1", (bad["source_sha256"], json.dumps(bad)))
    malformed = presentation(p["database"], 1)
    assert malformed["reading_view"] == original["reading_view"] and malformed["warning"]


def test_joined_paragraph_keeps_unicode_pagination_and_original_source_mapping():
    text = "### $ عنوان صناعي\r\n# قال: قد PageV01P003\r\n# شَكَوْك 🧪.\r\n# قال: لا.\r\n"
    offset = 930
    base = build_reading_view(text, offset, starts_at_line_start=True)
    boundary = base["blocks"][0]["source_char_end"]
    groups = [{"start": offset, "end": boundary}, {"start": boundary, "end": offset + len(text)}]
    joined = grouped_reading_view(base, groups)
    assert joined["coverage"] == base["coverage"]
    assert "قال: قد شَكَوْك 🧪. قال: لا." in joined["plain_text"]
    assert any(run["type"] == "page" and run["text"] == "PageV01P003" for run in joined["blocks"][1]["runs"])
    for block in joined["blocks"]:
        for run in block["runs"]:
            a, b = run["source_char_start"], run["source_char_end"]
            if run["type"] == "virtual_space": assert a == b
            else: assert run["text"] == text[a-offset:b-offset]
    assert base["renderer_version"] != joined["renderer_version"]


def test_editorial_heading_list_and_poetry_boundaries_are_mandatory(reading_book):
    p = reading_book
    doc = p["overlays"][0]
    with closing(connect(p["database"])) as db:
        payload = json.loads(db.execute("SELECT payload FROM pilot_documents WHERE biography_id=1").fetchone()[0])
        split = doc["criticism"][0]["start"]
        with db:
            payload["presentation"]["headings"] = [{"at": split, "label": "عنوان اصطناعي"}]
            db.execute("UPDATE pilot_documents SET payload=? WHERE biography_id=1", (json.dumps(payload),))
        with pytest.raises(ValueError, match="حد عنوان"):
            validate_reading_document(db, doc)


def test_presentation_http_returns_grouping_but_original_reading_api_is_unchanged(reading_book):
    from fastapi.testclient import TestClient
    from backend.app import create_app
    p = reading_book
    with TestClient(create_app(p["database"], p["source"])) as client:
        original_view = client.get("/api/biographies/1/reading-view").json()
        original_detail = client.get("/api/biographies/1").json()
        prepare_reading(p["database"], p["overlays"])
        result = client.get("/api/biographies/1/presentation").json()
        assert len(result["reading_view"]["blocks"]) < len(original_view["blocks"])
        assert client.get("/api/biographies/1/reading-view").json() == original_view
        assert client.get("/api/biographies/1").json() == original_detail


def test_heading_relocation_changes_only_its_display_position(reading_book):
    p = reading_book
    doc = deepcopy(p["overlays"][0])
    with closing(connect(p["database"])) as db:
        old_document = json.loads(db.execute("SELECT payload FROM pilot_documents WHERE biography_id=1").fetchone()[0])
        text = db.execute("SELECT full_text,source_char_start FROM biographies WHERE id=1").fetchone()
        base = build_reading_view(text[0], text[1], starts_at_line_start=True)
        old, new = base["blocks"][2]["source_char_start"], base["blocks"][1]["source_char_start"]
        old_document["presentation"]["headings"] = [{"at": old, "label": "عنوان تنظيمي اصطناعي"}]
        with db: db.execute("UPDATE pilot_documents SET payload=? WHERE biography_id=1", (json.dumps(old_document),))
        with pytest.raises(ValueError, match="حد عنوان"):
            validate_reading_document(db, doc)
    doc["heading_overrides"] = [{"from_at": old, "at": new}]
    documents = [doc, *p["overlays"][1:]]
    prepare_reading(p["database"], documents)
    result = presentation(p["database"], 1)
    assert result["annotations"]["headings"] == [{"at": new, "label": "عنوان تنظيمي اصطناعي"}]
    with closing(connect(p["database"])) as db:
        assert json.loads(db.execute("SELECT payload FROM pilot_documents WHERE biography_id=1").fetchone()[0]) == old_document
