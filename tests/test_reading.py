"""The clean reader is a mapped presentation, never a rewrite of its source."""
from contextlib import closing
from pathlib import Path
import re
import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from backend.parser import parse_source
from backend.reading import RENDERER_VERSION, build_reading_view, enrich_reading_views, reading_plain_with_map, reading_router

ROOT = Path(__file__).resolve().parents[1]


def assert_source_partition(view, text, source_start=0):
    spans = []
    for block in view["blocks"]:
        assert source_start <= block["source_char_start"] <= block["source_char_end"] <= source_start + len(text)
        for run in block["runs"]:
            a,b = run["source_char_start"] - source_start, run["source_char_end"] - source_start
            if run["type"] == "virtual_space":
                assert a == b
                assert run["text"] == " "
                continue
            assert text[a:b] == run["text"]
            spans.append((a,b,run["text"]))
    for hidden in view["hidden"]:
        a,b = hidden["source_char_start"] - source_start, hidden["source_char_end"] - source_start
        assert text[a:b] == hidden["text"]
        reason = hidden["reason"]
        if reason == "heading_prefix":
            assert re.fullmatch(r"###[ \t]*(?:\${1,2}|\|+)[ \t]*", hidden["text"])
        elif reason == "paragraph_prefix":
            assert re.fullmatch(r"#[ \t]+", hidden["text"])
        elif reason == "continuation_prefix":
            assert re.fullmatch(r"~~[ \t]*", hidden["text"])
        elif reason in ("line_break", "continuation_line_break"):
            assert hidden["text"] in ("\n", "\r\n")
        elif reason == "blank_line":
            assert not hidden["text"].strip(" \t\r\n")
        elif reason == "metadata_header":
            assert hidden["text"].lstrip("\ufeff").startswith(("######OpenITI#", "#META#"))
        else:
            pytest.fail("An unapproved technical span was hidden: " + reason)
        spans.append((a,b,hidden["text"]))
    cursor = 0
    for a,b,literal in sorted(spans):
        assert a == cursor
        assert b > a
        cursor = b
    assert cursor == len(text)
    assert "".join(s[2] for s in sorted(spans)) == text
    assert view["coverage"] == {"source_chars":len(text),"accounted_chars":len(text),"complete":True}


def test_structural_tags_are_hidden_with_exact_crlf_unicode_mapping():
    text = "### $$ 12 - اسمٌ (2) *\r\n# 🧪 قَالَ: إِخْلَاصٌ\r\n~~ومتنٌ PageV01P001 ms123 </span> (3).\r\n"
    view = build_reading_view(text, 150, starts_at_line_start=True)
    assert view["renderer_version"] == RENDERER_VERSION
    assert [b["type"] for b in view["blocks"]] == ["heading", "paragraph"]
    assert view["blocks"][0]["level"] == 2
    assert "12 - اسمٌ (2) *" in view["plain_text"]
    assert "🧪 قَالَ: إِخْلَاصٌ ومتنٌ" in view["plain_text"]
    assert " (3)." in view["plain_text"]
    assert all(tag not in view["plain_text"] for tag in ("###", "~~", "PageV01P001", "ms123", "</span>"))
    assert_source_partition(view, text, 150)
    clean,positions = reading_plain_with_map(text,150,starts_at_line_start=True)
    assert clean == view["plain_text"]
    assert len(clean) == len(positions)
    for char,(a,b) in zip(clean,positions):
        if a == b:
            assert char.isspace()
        else:
            assert text[a-150:b-150] == char
    highlight_start = 150 + text.index("إِخْلَاصٌ")
    highlight_end = highlight_start + len("إِخْلَاصٌ")
    highlighted = "".join(char for char,(a,b) in zip(clean,positions) if a<b and highlight_start<=a<b<=highlight_end)
    assert highlighted == "إِخْلَاصٌ"


def test_inline_unknown_markup_numbers_stars_and_words_are_preserved():
    text = "# نص # حرفي ~~ داخلي <span> و</SPAN> وms123abc وams456 وms789_ وms١٢. * (77) ١٢٣.\n~~نص آخر."
    view = build_reading_view(text)
    assert "# حرفي ~~ داخلي <span> و</SPAN> وms123abc وams456 وms789_ وms١٢. * (77) ١٢٣." in view["plain_text"]
    assert_source_partition(view,text)


def test_excerpt_prefix_safety_requires_true_source_line_boundary():
    for prefix in ("# نص", "~~نص", "### $ 1 - اسم"):
        unsafe = build_reading_view(prefix,99)
        assert unsafe["plain_text"] == prefix
        assert not unsafe["hidden"]
        safe = build_reading_view(prefix,99,starts_at_line_start=True)
        assert safe["plain_text"] != prefix
        assert_source_partition(unsafe,prefix,99)
        assert_source_partition(safe,prefix,99)
    # Other Unicode separators are ordinary source text, not OpenITI line starts.
    text="نص\u2028# هذا ليس سطر OpenITI جديدًا"
    assert build_reading_view(text)["plain_text"] == text


def test_metadata_header_is_explicit_and_unexpected_lines_survive():
    text = "######OpenITI#\r\n#META# 020.BookTITLE :: عينة مصطنعة\r\nسطر غير متوقع يبقى حرفيًا\r\n#META#Header#End#\r\n### | عنوان\r\n# متن\r\n"
    view=build_reading_view(text)
    assert "#META#" not in view["plain_text"]
    assert "سطر غير متوقع يبقى حرفيًا" in view["plain_text"]
    assert "عنوان" in view["plain_text"]
    assert_source_partition(view,text)
    malformed="######OpenITI#\n#META# سجل بلا نهاية ترويسة"
    assert "######OpenITI#" in build_reading_view(malformed)["plain_text"]
    body="# نص\n#META# ليس ترويسة هنا"
    assert "#META# ليس ترويسة هنا" in build_reading_view(body)["plain_text"]


def test_page_runs_retain_raw_links_and_unknown_placeholder():
    text="# قبل PageV01P010 بعد\n~~متمم PageV00P000\nPageV02P100\n# التالي"
    view=build_reading_view(text)
    pages=[r for b in view["blocks"] for r in b["runs"] if r["type"]=="page"]
    assert [p["raw_marker"] for p in pages]==["PageV01P010","PageV00P000","PageV02P100"]
    assert [p["is_unknown"] for p in pages]==[False,True,False]
    assert all(p["reference_status"]=="raw_unverified" for p in pages)
    assert_source_partition(view,text)


def test_empty_text_and_only_blank_metadata_views_are_covered():
    for text in ("", "\r\n \t\n", "######OpenITI#\n#META#Header#End#\n"):
        view=build_reading_view(text)
        assert not view["plain_text"]
        assert_source_partition(view,text)
    with pytest.raises(ValueError):
        build_reading_view("text",-1)


def test_reading_router_and_excerpt_line_boundary(book,db):
    app=FastAPI()
    app.include_router(reading_router(book["database"]))
    with TestClient(app) as client:
        bio=db.execute("SELECT * FROM biographies ORDER BY id LIMIT 1").fetchone()
        response=client.get(f"/api/biographies/{bio['id']}/reading-view")
        assert response.status_code==200
        view=response.json()
        assert view["biography_id"]==bio["id"]
        assert view["source_id"]==bio["source_id"]
        assert_source_partition(view,bio["full_text"],bio["source_char_start"])
        section=db.execute("SELECT * FROM source_sections WHERE section_kind='front_matter'").fetchone()
        view=client.get(f"/api/sections/{section['id']}/reading-view").json()
        assert "#META#" not in view["plain_text"]
        assert_source_partition(view,section["full_text"],section["source_char_start"])
        # Start immediately after the first hash so this looks like a paragraph
        # prefix inside a heading; only source context can disambiguate it.
        start=book["text"].index("### $ 1")+2
        end=start+20
        view=client.get(f"/api/sources/{bio['source_id']}/reading-view",params={"start":start,"end":end}).json()
        assert view["plain_text"]==book["text"][start:end]
        assert_source_partition(view,book["text"][start:end],start)
        assert client.get("/api/biographies/999999/reading-view").status_code==404
        assert client.get("/api/sections/999999/reading-view").status_code==404
        assert client.get("/api/sources/999999/reading-view").status_code==404
        assert client.get(f"/api/sources/{bio['source_id']}/reading-view?start=100&end=1").status_code==422
        assert client.get(f"/api/sources/{bio['source_id']}/reading-view?start=-1").status_code==422
    # Neither endpoint transforms the immutable original.
    assert db.execute("SELECT full_text FROM sources WHERE id=?",(bio["source_id"],)).fetchone()[0]==book["text"]


def test_batched_evidence_and_context_views_keep_distinct_absolute_ranges(book,db):
    hit=db.execute("SELECT h.*,s.source_id,s.text_original,s.source_char_start context_char_start FROM topic_hits h JOIN segments s ON s.id=h.segment_id ORDER BY h.id LIMIT 1").fetchone()
    result={"items":[dict(hit)],"total":1}
    returned=enrich_reading_views(db,result)
    assert returned is result
    row=result["items"][0]
    assert_source_partition(row["reading_view"],hit["evidence_text"],hit["source_char_start"])
    assert_source_partition(row["context_reading_view"],hit["text_original"],hit["context_char_start"])
    assert "# " not in row["context_reading_view"]["plain_text"]
    fallback=dict(hit)
    fallback.pop("context_char_start")
    enrich_reading_views(db,{"items":[fallback]})
    assert fallback["context_reading_view"]["source_char_start"]==hit["context_char_start"]


def test_all_real_source_sections_have_lossless_mapped_reading_views():
    path=ROOT/"data"/"source.mARkdown"
    if not path.is_file():
        pytest.skip("النص الأصلي غير موجود؛ اختبار التغطية الحقيقية متعذر")
    source=path.read_bytes().decode("utf-8")
    parsed=parse_source(source)
    covered=page_runs=0
    for section in parsed.sections:
        text=source[section.start:section.end]
        view=build_reading_view(text,section.start,starts_at_line_start=True)
        assert_source_partition(view,text,section.start)
        covered+=view["coverage"]["accounted_chars"]
        page_runs+=sum(r["type"]=="page" for b in view["blocks"] for r in b["runs"])
        for block in view["blocks"]:
            for run in block["runs"]:
                if run["type"]=="annotation":
                    assert re.fullmatch(r"ms[0-9]+|</span>",run["text"])
    assert covered==len(source)
    assert page_runs==len(parsed.pages)
