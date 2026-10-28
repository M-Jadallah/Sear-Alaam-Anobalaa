"""A source-mapped OpenITI reading view; canonical text is never changed.

Only documented structural prefixes and known technical inline tags receive
special treatment. Unknown markup is ordinary, literal text, never HTML.
"""
from contextlib import closing
import re

from fastapi import APIRouter, HTTPException, Query

from .db import connect

RENDERER_VERSION = "openiti-reading-v1"
_HEADING_PREFIX = re.compile(r"^###[ \t]*(?P<marker>\${1,2}(?!\$)|\|+)[ \t]*")
_PARAGRAPH_PREFIX = re.compile(r"^#[ \t]+")
_CONTINUATION_PREFIX = re.compile(r"^~~[ \t]*")
_INLINE = re.compile(r"PageV[0-9]+P[0-9]+|(?<!\w)ms[0-9]+(?!\w)|</span>")
_META_END = re.compile(r"(?m)^#META#Header#End#[ \t]*(?:\r?\n|$)")


def _plain_and_map(view, *, with_map=True):
    chunks, positions = [], []
    for block in view["blocks"]:
        visible = [run for run in block["runs"] if run["type"] in ("text", "virtual_space")]
        if not any(run["text"] for run in visible):
            continue
        if chunks:
            chunks.append("\n\n")
            if with_map:
                positions.extend([(block["source_char_start"], block["source_char_start"])] * 2)
        for run in visible:
            chunks.append(run["text"])
            start = run["source_char_start"]
            if not with_map:
                continue
            if run["type"] == "virtual_space":
                positions.extend([(start, start)] * len(run["text"]))
            else:
                positions.extend((start + index, start + index + 1) for index in range(len(run["text"])))
    return "".join(chunks), positions


def build_reading_view(text: str, source_start: int = 0, *, starts_at_line_start: bool | None = None):
    """Return a presentation AST whose ranges use absolute Unicode code points.

    An excerpt with a nonzero start is *not* assumed to start at a real source
    line boundary. Callers with source context may explicitly set that flag.
    Structural hidden spans plus literal runs partition the input exactly.
    virtual_space runs insert display separators at zero-width source positions.
    """
    if source_start < 0:
        raise ValueError("source_start must be nonnegative")
    if starts_at_line_start is None:
        starts_at_line_start = source_start == 0
    blocks, hidden = [], []
    current, pending, previous_kind = None, None, None

    def hide(start, end, reason):
        if end > start:
            hidden.append({"type": "technical", "reason": reason, "text": text[start:end],
                           "source_char_start": source_start + start, "source_char_end": source_start + end})

    def new_block(start, block_type="paragraph", level=None):
        block = {"id": "block-" + str(len(blocks) + 1), "type": block_type,
                 "source_char_start": source_start + start, "source_char_end": source_start + start, "runs": []}
        if level is not None:
            block["level"] = level
        blocks.append(block)
        return block

    def add_text(block, start, end):
        if end > start:
            block["runs"].append({"type": "text", "text": text[start:end],
                                  "source_char_start": source_start + start, "source_char_end": source_start + end})

    def add_space(block, at):
        previous = next((r["text"] for r in reversed(block["runs"]) if r["type"] in ("text", "virtual_space") and r["text"]), "")
        if previous and not previous[-1].isspace():
            block["runs"].append({"type": "virtual_space", "text": " ",
                                  "source_char_start": source_start + at, "source_char_end": source_start + at})

    def add_inline(block, start, end):
        cursor = start
        for match in _INLINE.finditer(text, start, end):
            add_text(block, cursor, match.start())
            raw = match.group()
            run = {"type": "page" if raw.startswith("PageV") else "annotation", "text": raw,
                   "source_char_start": source_start + match.start(), "source_char_end": source_start + match.end()}
            if run["type"] == "page":
                run.update(raw_marker=raw, is_unknown=raw == "PageV00P000", reference_status="raw_unverified")
            else:
                run["reason"] = "pagination_annotation" if raw.startswith("ms") else "exact_span_closer"
            block["runs"].append(run)
            cursor = match.end()
        add_text(block, cursor, end)

    # Hide metadata only when the actual source header is present and complete.
    # A #META# lookalike in running prose or an isolated excerpt stays literal.
    metadata_end = 0
    if source_start == 0 and text.lstrip("\ufeff").startswith("######OpenITI#"):
        meta_end = _META_END.search(text)
        if meta_end:
            metadata_end = meta_end.end()
    line_offset = 0
    for line_index, line_match in enumerate(re.finditer(r"[^\n]*(?:\n|$)", text)):
        line = line_match.group()
        if not line:
            continue
        line_start, line_end = line_offset, line_offset + len(line)
        content_end = line_end
        if line.endswith("\r\n"):
            content_end -= 2
        elif line.endswith("\n"):
            content_end -= 1
        content = text[line_start:content_end]
        if line_start < metadata_end and (content.startswith("#META#") or (line_start == 0 and content.lstrip("\ufeff") == "######OpenITI#")):
            if pending:
                if previous_kind == "raw" and current:
                    add_text(current, *pending)
                else:
                    hide(*pending, "line_break")
                pending = None
            hide(line_start, line_end, "metadata_header")
            current = None
            previous_kind = None
            line_offset = line_end
            continue
        anchored = bool(starts_at_line_start) if line_index == 0 else True
        heading = _HEADING_PREFIX.match(content) if anchored else None
        paragraph = _PARAGRAPH_PREFIX.match(content) if anchored else None
        continuation = _CONTINUATION_PREFIX.match(content) if anchored else None
        page_only = re.fullmatch(r"[ \t]*PageV[0-9]+P[0-9]+[ \t]*", content) is not None
        blank = not content.strip(" \t")
        kind = "heading" if heading else "paragraph" if paragraph else "continuation" if continuation else "page" if page_only else "raw"
        if blank:
            if pending:
                if previous_kind == "raw" and current:
                    add_text(current, *pending)
                else:
                    hide(*pending, "line_break")
                pending = None
            hide(line_start, line_end, "blank_line")
            current = None
            previous_kind = None
            line_offset = line_end
            continue
        if pending:
            if kind in ("continuation", "page"):
                hide(*pending, "continuation_line_break")
                if current:
                    add_space(current, line_start)
            elif kind in ("heading", "paragraph"):
                hide(*pending, "line_break")
            elif current:
                add_text(current, *pending)
            else:
                hide(*pending, "line_break")
            pending = None
        if kind in ("heading", "paragraph") or current is None:
            marker = heading.group("marker") if heading else None
            level = min(6, len(marker)) if marker and marker.startswith("|") else 2 if marker == "$$" else 1
            current = new_block(line_start, "heading" if heading else "paragraph", level if heading else None)
        prefix = heading or paragraph or continuation
        body_start = line_start
        if prefix:
            body_start += prefix.end()
            hide(line_start, body_start, "heading_prefix" if heading else "paragraph_prefix" if paragraph else "continuation_prefix")
        add_inline(current, body_start, content_end)
        current["source_char_end"] = source_start + line_end
        if content_end < line_end:
            pending = (content_end, line_end)
        previous_kind = kind
        line_offset = line_end
    if pending:
        if previous_kind == "raw" and current:
            add_text(current, *pending)
        else:
            hide(*pending, "line_break")
    spans = [(item["source_char_start"], item["source_char_end"]) for item in hidden]
    spans.extend((run["source_char_start"], run["source_char_end"]) for block in blocks for run in block["runs"] if run["type"] != "virtual_space")
    cursor = source_start
    complete = True
    for start, end in sorted(spans):
        if start != cursor or end <= start:
            complete = False
            break
        cursor = end
    complete = complete and cursor == source_start + len(text)
    result = {"renderer_version": RENDERER_VERSION, "source_char_start": source_start,
              "source_char_end": source_start + len(text), "blocks": blocks, "hidden": hidden,
              "coverage": {"source_chars": len(text), "accounted_chars": sum(end - start for start, end in spans), "complete": complete}}
    result["plain_text"] = _plain_and_map(result, with_map=False)[0]
    return result


def reading_plain_with_map(text: str, source_start: int = 0, *, starts_at_line_start: bool | None = None):
    return _plain_and_map(build_reading_view(text, source_start, starts_at_line_start=starts_at_line_start))


def enrich_reading_views(db, result):
    """Add clean witness/context ASTs to a paginated result without changing it.

    Source and optional segment context are fetched once per result, not once per
    row. Supplying context_char_start alongside text_original avoids the segment
    fallback query. All source text is read-only.
    """
    rows = result.get("items", [])
    source_ids = sorted({row["source_id"] for row in rows if row.get("source_id") is not None})
    sources = {}
    if source_ids:
        placeholders = ",".join("?" for _ in source_ids)
        sources = {row["id"]: row["full_text"] for row in db.execute("SELECT id,full_text FROM sources WHERE id IN (" + placeholders + ")", source_ids)}
    missing_context = sorted({row["segment_id"] for row in rows if row.get("segment_id") is not None and row.get("context_char_start") is None})
    segments = {}
    if missing_context:
        placeholders = ",".join("?" for _ in missing_context)
        segments = {row["id"]: row for row in db.execute("SELECT id,source_char_start,text_original FROM segments WHERE id IN (" + placeholders + ")", missing_context)}
    for row in rows:
        original = row.get("evidence_text")
        if original is None:
            original = row.get("text_original")
        if original is None:
            original = row.get("mention_literal")
        start = row.get("source_char_start")
        if original is None or start is None:
            continue
        source = sources.get(row.get("source_id"))
        anchored = start == 0 or bool(source and start <= len(source) and source[start - 1] == "\n")
        row["reading_view"] = build_reading_view(original, start, starts_at_line_start=anchored)
        context = row.get("text_original")
        context_start = row.get("context_char_start")
        fallback = segments.get(row.get("segment_id"))
        if context_start is None and fallback:
            context_start = fallback["source_char_start"]
            context = fallback["text_original"]
        if context is not None and context_start is not None and (context != original or context_start != start):
            anchored = context_start == 0 or bool(source and context_start <= len(source) and source[context_start - 1] == "\n")
            row["context_reading_view"] = build_reading_view(context, context_start, starts_at_line_start=anchored)
    return result


def reading_router(db_path):
    router = APIRouter()

    def document_view(kind, document_id):
        table = "biographies" if kind == "biography" else "source_sections"
        with closing(connect(db_path)) as db:
            row = db.execute("SELECT id,source_id,heading,full_text,source_char_start,source_char_end FROM " + table + " WHERE id=?", (document_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "العنصر غير موجود")
            result = build_reading_view(row["full_text"], row["source_char_start"], starts_at_line_start=True)
            result.update(source_id=row["source_id"], heading=row["heading"], biography_id=document_id if kind == "biography" else None,
                          section_id=document_id if kind == "section" else None)
            return result

    @router.get("/api/biographies/{biography_id}/reading-view")
    def biography_reading_view(biography_id: int):
        return document_view("biography", biography_id)

    @router.get("/api/sections/{section_id}/reading-view")
    def section_reading_view(section_id: int):
        return document_view("section", section_id)

    @router.get("/api/sources/{source_id}/reading-view")
    def source_reading_view(source_id: int, start: int = Query(0, ge=0), end: int | None = Query(None, ge=0)):
        with closing(connect(db_path)) as db:
            row = db.execute("SELECT id,title,full_text,char_count FROM sources WHERE id=?", (source_id,)).fetchone()
            if row is None:
                raise HTTPException(404, "المصدر غير موجود")
            stop = min(end if end is not None else start + 20000, row["char_count"])
            if start > stop or stop - start > 100000:
                raise HTTPException(422, "نطاق القراءة غير صالح أو يتجاوز 100000 محرف")
            text = row["full_text"]
            anchored = start == 0 or text[start - 1] == "\n"
            result = build_reading_view(text[start:stop], start, starts_at_line_start=anchored)
            result.update(source_id=source_id, heading=row["title"], biography_id=None, section_id=None)
            return result

    return router
