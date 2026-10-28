"""Read-only acceptance check of the actual import, when it has been built.

Run the documented import command first. This test never modifies that database.
Isolated synthetic databases remain the basis of all API mutation tests.
"""
import hashlib
import sqlite3

import pytest

from conftest import ROOT


def test_real_import_has_exact_source_full_coverage_and_literal_evidence():
    source_path = ROOT / "data" / "source.mARkdown"
    database_path = ROOT / "data" / "library.sqlite3"
    if not source_path.exists() or not database_path.exists():
        pytest.skip("يلزم المصدر الحقيقي وتشغيل أمر الاستيراد لاختبار قاعدة البيانات الفعلية")
    source_bytes = source_path.read_bytes()
    source_text = source_bytes.decode("utf-8")
    with sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        source = connection.execute("SELECT * FROM sources WHERE sha256=?", (hashlib.sha256(source_bytes).hexdigest(),)).fetchone()
        assert source is not None, "قاعدة البيانات لا تحتوي النسخة الحالية من المصدر؛ أعد الاستيراد"
        assert source["full_text"] == source_text
        assert source["is_synthetic"] == 0
        sections = connection.execute("SELECT * FROM source_sections WHERE source_id=? ORDER BY source_char_start", (source["id"],)).fetchall()
        assert sections and sections[0]["source_char_start"] == 0
        assert sections[-1]["source_char_end"] == len(source_text)
        assert all(a["source_char_end"] == b["source_char_start"] for a, b in zip(sections, sections[1:]))
        for section in sections:
            assert source_text[section["source_char_start"]:section["source_char_end"]] == section["full_text"]
        biographies = connection.execute("SELECT * FROM biographies WHERE source_id=?", (source["id"],)).fetchall()
        assert biographies
        for biography in biographies:
            assert source_text[biography["source_char_start"]:biography["source_char_end"]] == biography["full_text"]
        for segment in connection.execute("SELECT * FROM segments WHERE source_id=?", (source["id"],)):
            assert source_text[segment["source_char_start"]:segment["source_char_end"]] == segment["text_original"]
        for hit in connection.execute("SELECT h.* FROM topic_hits h JOIN segments s ON s.id=h.segment_id WHERE s.source_id=?", (source["id"],)):
            assert source_text[hit["source_char_start"]:hit["source_char_end"]] == hit["evidence_text"]
        for mention in connection.execute("SELECT * FROM person_mentions WHERE source_id=?", (source["id"],)):
            assert source_text[mention["source_char_start"]:mention["source_char_end"]] == mention["mention_literal"]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
