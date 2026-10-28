"""Boundary and Arabic offset regression tests against explicit synthetic data."""
import hashlib
import re

import pytest

from conftest import ROOT, SYNTHETIC


def test_dollar_titles_are_biographies_and_other_sections_remain_separate():
    from backend.parser import parse_source

    parsed = parse_source(SYNTHETIC)
    assert len(parsed.biographies) == 3
    assert [s.start for s in parsed.biographies] == [m.start() for m in re.finditer(r"^### \${1,2} ", SYNTHETIC, re.M)]
    for biography in parsed.biographies:
        original = SYNTHETIC[biography.start:biography.end]
        assert original.startswith("### $")
        assert "### |PARATEXT|" not in original
        assert original.count("### $") == 1
    # The entire source is retained as adjacent sections, including metadata/partext.
    sections = sorted(parsed.sections, key=lambda item: item.start)
    assert sections[0].start == 0
    assert sections[-1].end == len(SYNTHETIC)
    assert all(left.end == right.start for left, right in zip(sections, sections[1:]))
    assert "".join(SYNTHETIC[s.start:s.end] for s in sections) == SYNTHETIC


def test_markers_are_occurrences_not_unique_page_names_and_keep_exact_positions():
    from backend.parser import parse_source

    pages = parse_source(SYNTHETIC).pages
    assert len(pages) == 4
    assert [p.raw for p in pages].count("PageV01P001") == 2
    for page in pages:
        assert SYNTHETIC[page.start:page.end] == page.raw
        assert page.line == SYNTHETIC[:page.start].count("\n") + 1
    assert SYNTHETIC[pages[0].start - 1] == " "  # inline marker is recognized


def test_normalization_preserves_diacritic_mapping_and_does_not_merge_ta_marbuta():
    from backend.normalization import normalize_arabic, normalize_with_map

    original = "قَبْلُ الإِخْلَاصُ وبعده، على هُدًى."
    normalized, mapping = normalize_with_map(original)
    needle = normalize_arabic("الاخلاص")
    index = normalized.index(needle)
    start, end = mapping[index][0], mapping[index + len(needle) - 1][1]
    assert normalize_arabic(original[start:end]) == needle
    assert "إ" in original[start:end]
    assert len(mapping) == len(normalized)
    assert normalize_arabic("فاطمة") != normalize_arabic("فاطمه")
    assert normalize_arabic("الإخلاص") == normalize_arabic("الاخلاص")


def test_no_fake_biographies_from_section_only_file():
    from backend.parser import parse_source

    parsed = parse_source("######OpenITI#\n#META#Header#End#\n### | قسم مصطنع\n# لا ترجمة هنا.\nPageV00P000\n")
    assert parsed.biographies == []


def test_empty_pipe_heading_does_not_swallow_next_biography_heading():
    from backend.parser import parse_source

    text = "######OpenITI#\n#META#Header#End#\n### |\n### $ 1 - علم مصطنع\n# متن مصطنع.\n"
    parsed = parse_source(text)
    assert len(parsed.biographies) == 1
    assert parsed.biographies[0].start == text.index("### $ 1")
    assert any(section.start == text.index("### |") and section.end == text.index("### $ 1") for section in parsed.sections)


def test_numeric_only_heading_is_retained_without_extending_into_its_body():
    from backend.parser import parse_source

    text = "######OpenITI#\n#META#Header#End#\n### $ 89\n# - اسم مصطنع في سطر تالف للاختبار.\n### $ 90 - شخص مصطنع\n# نهاية.\n"
    parsed = parse_source(text)
    assert len(parsed.biographies) == 2
    heading = parsed.biographies[0].heading
    assert heading.raw == "### $ 89" or heading.raw == "### $ 89\n"
    assert "اسم مصطنع" not in heading.title


def test_real_source_boundaries_and_literal_coverage_if_available():
    from backend.parser import parse_source

    path = ROOT / "data" / "source.mARkdown"
    if not path.exists():
        pytest.skip("النص الأصلي غير موجود؛ الاختبارات الأخرى تستخدم عينة مصطنعة فقط")
    raw = path.read_bytes()
    original = raw.decode("utf-8")
    parsed = parse_source(original)
    expected = list(re.finditer(r"^###\s+\${1,2}(?!\$)", original, re.M))
    assert len(parsed.biographies) == len(expected) > 0
    assert [s.start for s in parsed.biographies] == [m.start() for m in expected]
    sections = sorted(parsed.sections, key=lambda item: item.start)
    assert sections[0].start == 0 and sections[-1].end == len(original)
    assert all(a.end == b.start for a, b in zip(sections, sections[1:]))
    reconstructed = "".join(original[s.start:s.end] for s in sections)
    assert hashlib.sha256(reconstructed.encode("utf-8")).digest() == hashlib.sha256(raw).digest()
    assert len(parsed.pages) == len(re.findall(r"PageV\d+P\d+", original))
    for index in [0, len(parsed.biographies) // 2, len(parsed.biographies) - 1]:
        biography = parsed.biographies[index]
        assert original[biography.start:biography.end].startswith("### $")
