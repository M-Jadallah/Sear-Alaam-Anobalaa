"""Conservative OpenITI parser. Offsets index the unmodified Unicode source."""
from dataclasses import dataclass
import bisect
import re

HEADING = re.compile(r"^###[ \t]*(?P<marker>\${1,2}|\|+)(?P<title>[^\r\n]*)", re.M)
PAGE = re.compile(r"PageV\d+P\d+")

@dataclass(frozen=True)
class Heading:
    start: int
    end: int
    line: int
    raw: str
    title: str
    kind: str

@dataclass(frozen=True)
class PageMarker:
    start: int
    end: int
    line: int
    raw: str

@dataclass(frozen=True)
class Section:
    start: int
    end: int
    heading: Heading | None
    kind: str
    ordinal: int

@dataclass
class ParsedSource:
    headings: list[Heading]
    pages: list[PageMarker]
    sections: list[Section]
    biographies: list[Section]


def parse_source(text: str) -> ParsedSource:
    if not text.lstrip("\ufeff\r\n ").startswith("######OpenITI#"):
        raise ValueError("الملف ليس نص OpenITI: ترويسة المصدر مفقودة.")
    if "#META#Header#End#" not in text:
        raise ValueError("نهاية ترويسة OpenITI مفقودة؛ أوقف الاستيراد للتحقق.")
    newlines = [m.start() for m in re.finditer("\n", text)]
    headings = [Heading(m.start(), m.end(), bisect.bisect_left(newlines, m.start()) + 1,
                        m.group(), m.group("title").strip(),
                        "biography" if m.group("marker").startswith("$") else "section")
                for m in HEADING.finditer(text)]
    pages = [PageMarker(m.start(), m.end(), bisect.bisect_left(newlines, m.start()) + 1, m.group())
             for m in PAGE.finditer(text)]
    sections = []
    if not headings:
        sections.append(Section(0, len(text), None, "front_matter", 1))
    elif headings[0].start:
        sections.append(Section(0, headings[0].start, None, "front_matter", 1))
    for index, heading in enumerate(headings):
        end = headings[index + 1].start if index + 1 < len(headings) else len(text)
        sections.append(Section(heading.start, end, heading, heading.kind, len(sections) + 1))
    return ParsedSource(headings, pages, sections, [s for s in sections if s.kind == "biography"])


def paragraph_ranges(text: str, start: int, end: int):
    boundaries = [m.start() + start for m in re.finditer(r"(?m)^# (?!META)", text[start:end])]
    for index, offset in enumerate(boundaries):
        stop = boundaries[index + 1] if index + 1 < len(boundaries) else end
        yield offset, stop
