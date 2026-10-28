"""Model-free, source-wide proof that windowing omits no displayed content.

Without an index directory, a whitespace tokenizer audits the splitting rules.
With an index directory, actual stored windows are checked without loading a
tokenizer or models. Neither mode evaluates vectors or semantic relevance.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import defaultdict
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.reading import RENDERER_VERSION, reading_plain_with_map
from backend.semantic import DEFAULT_DB, INDEX_VERSION, canonical_connection, token_windows


class WordTokenizer:
    def __call__(self, text, **kwargs):
        offsets = [(match.start(), match.end()) for match in re.finditer(r"\S+", text)]
        return {"offset_mapping": offsets, "input_ids": list(range(len(offsets)))}


def audit(database, index_directory=None):
    started = time.perf_counter()
    actual_windows = None
    report = {"scope": "all non-front-matter source sections", "tokenizer": "synthetic whitespace tokenizer; no model inference" if index_directory is None else "stored model-token counts; no tokenizer or model loaded",
              "index_version": INDEX_VERSION, "renderer_version": RENDERER_VERSION,
              "sections": 0, "nonempty_sections": 0, "windows": 0,
              "display_nonspace_codepoints": 0, "covered_nonspace_codepoints": 0, "uncovered": [],
              "invalid_windows": [], "vectors_validated": False}
    if index_directory is not None:
        index_directory = Path(index_directory).resolve()
        actual_windows = defaultdict(list)
        with canonical_connection(index_directory / "windows.sqlite3") as db:
            report["windows_complete_metadata"] = bool(db.execute("SELECT value FROM metadata WHERE key='windows_complete' AND value='1'").fetchone())
            for row in db.execute("SELECT id,source_id,source_sha256,section_id,source_char_start,source_char_end,token_count FROM windows ORDER BY section_id,source_char_start"):
                actual_windows[row["section_id"]].append(dict(row))
        report["index_directory"] = str(index_directory)
    with canonical_connection(database) as db:
        report["sources"] = [dict(row) for row in db.execute("SELECT id,sha256,char_count FROM sources ORDER BY id")]
        source_hashes = {row["id"]: row["sha256"] for row in report["sources"]}
        for section in db.execute("SELECT * FROM source_sections WHERE section_kind!='front_matter' ORDER BY source_id,source_ordinal"):
            report["sections"] += 1
            clean, positions = reading_plain_with_map(section["full_text"], section["source_char_start"], starts_at_line_start=True)
            windows = list(token_windows(clean, positions, WordTokenizer())) if actual_windows is None else actual_windows.pop(section["id"], [])
            report["windows"] += len(windows)
            if any(not char.isspace() for char in clean):
                report["nonempty_sections"] += 1
            intervals = []
            for window in sorted(windows, key=lambda item: item["source_char_start"]):
                start, end = window["source_char_start"], window["source_char_end"]
                valid = section["source_char_start"] <= start < end <= section["source_char_end"] and 0 < window["token_count"] <= 320
                if actual_windows is not None:
                    valid = valid and window["source_id"] == section["source_id"] and window["source_sha256"] == source_hashes.get(section["source_id"])
                if not valid:
                    report["invalid_windows"].append({"section_id": section["id"], **window})
                    continue
                if intervals and start <= intervals[-1][1]:
                    intervals[-1][1] = max(intervals[-1][1], end)
                else:
                    intervals.append([start, end])
            starts = [interval[0] for interval in intervals]
            lost = []
            for char, (start, end) in zip(clean, positions):
                if char.isspace() or start == end:
                    continue
                report["display_nonspace_codepoints"] += 1
                interval = bisect_right(starts, start) - 1
                if interval >= 0 and end <= intervals[interval][1]:
                    report["covered_nonspace_codepoints"] += 1
                elif len(lost) < 10:
                    lost.append({"char": char, "start": start, "end": end})
            if lost:
                report["uncovered"].append({"section_id": section["id"], "examples": lost})
    if actual_windows:
        report["unexpected_section_ids"] = sorted(actual_windows)
        report["invalid_windows"].extend(window for windows in actual_windows.values() for window in windows)
    report["complete"] = report["display_nonspace_codepoints"] == report["covered_nonspace_codepoints"] and not report["invalid_windows"] and report.get("windows_complete_metadata", True)
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return report


def audit_paragraphs(database, index_directory):
    """Audit actual paragraph windows against every canonical source segment."""
    started = time.perf_counter()
    index_directory = Path(index_directory).resolve()
    report = {"scope": "all canonical paragraph segments", "tokenizer": "stored model-token counts; no tokenizer or model loaded",
              "index_directory": str(index_directory), "renderer_version": RENDERER_VERSION,
              "canonical_segments": 0, "nonempty_segments": 0, "segments_with_windows": 0, "windows": 0,
              "display_nonspace_codepoints": 0, "covered_nonspace_codepoints": 0,
              "uncovered": [], "invalid_windows": [], "vectors_validated": False}
    by_segment = defaultdict(list)
    with canonical_connection(database) as source, canonical_connection(index_directory / "windows.sqlite3") as index:
        report["sources"] = [dict(row) for row in source.execute("SELECT id,sha256,char_count FROM sources ORDER BY id")]
        source_hashes = {row["id"]: row["sha256"] for row in report["sources"]}
        segments_by_section = defaultdict(list)
        for row in source.execute("SELECT id,source_id,section_id,biography_id,source_char_start,source_char_end FROM segments ORDER BY section_id,source_char_start"):
            segments_by_section[row["section_id"]].append(dict(row))
        starts_by_section = {section: [segment["source_char_start"] for segment in segments] for section, segments in segments_by_section.items()}
        report["windows_complete_metadata"] = bool(index.execute("SELECT value FROM metadata WHERE key='windows_complete' AND value='1'").fetchone())
        for row in index.execute("SELECT id,source_id,source_sha256,section_id,biography_id,source_char_start,source_char_end,token_count FROM windows ORDER BY id"):
            window = dict(row)
            report["windows"] += 1
            segments = segments_by_section.get(row["section_id"], [])
            position = bisect_right(starts_by_section.get(row["section_id"], []), row["source_char_start"]) - 1
            segment = segments[position] if position >= 0 else None
            valid = segment is not None and segment["source_char_start"] <= row["source_char_start"] < row["source_char_end"] <= segment["source_char_end"]
            valid = valid and 0 < row["token_count"] <= 320 and row["source_sha256"] == source_hashes.get(row["source_id"])
            valid = valid and segment["source_id"] == row["source_id"] and segment["biography_id"] == row["biography_id"]
            if valid:
                by_segment[segment["id"]].append(window)
            else:
                report["invalid_windows"].append(window)
        for segment in source.execute("SELECT * FROM segments ORDER BY id"):
            report["canonical_segments"] += 1
            clean, positions = reading_plain_with_map(segment["text_original"], segment["source_char_start"], starts_at_line_start=True)
            windows = by_segment.get(segment["id"], [])
            if any(not char.isspace() for char in clean):
                report["nonempty_segments"] += 1
                if windows:
                    report["segments_with_windows"] += 1
            intervals = []
            for window in sorted(windows, key=lambda item: item["source_char_start"]):
                start, end = window["source_char_start"], window["source_char_end"]
                if intervals and start <= intervals[-1][1]:
                    intervals[-1][1] = max(intervals[-1][1], end)
                else:
                    intervals.append([start, end])
            starts = [interval[0] for interval in intervals]
            lost = []
            for char, (start, end) in zip(clean, positions):
                if char.isspace() or start == end:
                    continue
                report["display_nonspace_codepoints"] += 1
                interval = bisect_right(starts, start) - 1
                if interval >= 0 and end <= intervals[interval][1]:
                    report["covered_nonspace_codepoints"] += 1
                elif len(lost) < 10:
                    lost.append({"char": char, "start": start, "end": end})
            if lost:
                report["uncovered"].append({"segment_id": segment["id"], "examples": lost})
    report["complete"] = (report["display_nonspace_codepoints"] == report["covered_nonspace_codepoints"]
                          and report["nonempty_segments"] == report["segments_with_windows"]
                          and not report["invalid_windows"] and report["windows_complete_metadata"])
    report["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--index-directory", type=Path, help="Read actual stored windows.sqlite3 instead of using a synthetic tokenizer")
    parser.add_argument("--paragraphs", action="store_true", help="Audit supplemental windows against canonical segments; requires --index-directory")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.paragraphs and args.index_directory is None:
        parser.error("--paragraphs requires --index-directory")
    report = audit_paragraphs(args.db, args.index_directory) if args.paragraphs else audit(args.db, args.index_directory)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
