"""Transactional local importer; no network services and no generated quotations."""
from pathlib import Path
import argparse
import bisect
import hashlib
import json
import re
import time

from .db import DEFAULT_DB, DEFAULT_SOURCE, DEFAULT_DICTIONARY, connect, initialize
from .normalization import normalize_arabic, normalize_with_map
from .parser import parse_source, paragraph_ranges

SOURCE_URL = "https://github.com/OpenITI/0750AH/blob/master/data/0748Dhahabi/0748Dhahabi.SiyarAclamNubala/0748Dhahabi.SiyarAclamNubala.Shamela0010906-ara1.mARkdown"
PARSER_VERSION = "openiti-conservative-1"
PAGE_POLICY = "OpenITI markers close preceding text; raw markers only, printed pagination unverified; PageV00P000 unknown."


def canonical_from_heading(title):
    cleaned = re.sub(r"\bms\d+\b", "", title)
    cleaned = re.sub(r"^[\s*]*\d+[\s*]*(?:[-–—][\s*]*)?", "", cleaned)
    return cleaned.strip(" *\t\r\n")


def aliases_from_name(name):
    aliases = {name}
    for match in re.finditer(r"[\u0621-\u064a]+ بن [\u0621-\u064a]+(?: بن [\u0621-\u064a]+){0,3}", name):
        aliases.add(match.group())
    return [alias for alias in aliases if len(normalize_arabic(alias).split()) >= 2]


def _trie_add(trie, phrase, person_id):
    node = trie
    for token in phrase.split():
        node = node.setdefault(token, {})
    node.setdefault("_ids", set()).add(person_id)
    node["_alias"] = phrase


def _mention_matches(trie, normalized):
    tokens = list(re.finditer(r"\S+", normalized))
    for index, token in enumerate(tokens):
        node = trie
        for next_index in range(index, min(index + 18, len(tokens))):
            next_token = tokens[next_index]
            node = node.get(next_token.group())
            if node is None:
                break
            if "_ids" in node:
                yield token.start(), next_token.end(), node["_ids"], node["_alias"]


def import_book(source_path=DEFAULT_SOURCE, db_path=DEFAULT_DB, dictionary_path=DEFAULT_DICTIONARY):
    source_path = Path(source_path)
    if not source_path.is_file():
        raise FileNotFoundError("المصدر مفقود. اختر data/source.mARkdown الأصلي من OpenITI ثم أعد الاستيراد.")
    if source_path.stat().st_size > 200 * 1024 * 1024:
        raise ValueError("يتجاوز الملف حد الاستيراد الآمن (200 ميغابايت).")
    raw = source_path.read_bytes()
    if b"\x00" in raw:
        raise ValueError("لا يقبل الاستيراد ملفات ثنائية أو محارف NUL.")
    text = raw.decode("utf-8")
    parsed = parse_source(text)
    sha = hashlib.sha256(raw).hexdigest()
    initialize(db_path, dictionary_path)
    started = time.monotonic()
    with connect(db_path) as db:
        prior = db.execute("SELECT id FROM sources WHERE sha256=?", (sha,)).fetchone()
        if prior:
            return {"source_id": prior["id"], "sha256": sha, "unchanged": True,
                    "biographies": db.execute("SELECT COUNT(*) FROM biographies WHERE source_id=?", (prior["id"],)).fetchone()[0]}
        metadata = {m.group(1).strip(): m.group(2).strip() for m in re.finditer(r"^#META#\s*(.*?)\s*::\s*(.*)$", text, re.M)}
        synthetic = "synthetic" in source_path.name.lower() or "مصطنع" in text[:3000]
        known_book = "SiyarAclamNubala" in metadata.get("000.BookURI", "")
        source_id = db.execute("INSERT INTO sources(title,source_url,file_name,sha256,full_text,byte_count,char_count,metadata,edition_note,license_note,page_policy,parser_version,is_synthetic) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("عينة مصطنعة للاختبار — ليست من الكتاب" if synthetic else metadata.get("020.BookTITLE", "مصدر OpenITI مستورد"),
             "synthetic:test-fixture" if synthetic else SOURCE_URL if known_book else "local:" + source_path.name, source_path.name, sha, text, len(raw), len(text),
             json.dumps(metadata, ensure_ascii=False), metadata.get("041.EdNUMBER"),
             "لم يعتمد إذن إعادة نشر الطبعة؛ راجع حقوق النص والطبعة قبل أي نشر عام.", PAGE_POLICY, PARSER_VERSION, int(synthetic))).lastrowid
        db.executemany("INSERT INTO page_markers(source_id,raw_marker,source_char_offset,source_char_end,source_line,is_placeholder) VALUES (?,?,?,?,?,?)",
                       [(source_id, p.raw, p.start, p.end, p.line, int(p.raw == "PageV00P000")) for p in parsed.pages])
        page_offsets = [p.start for p in parsed.pages]
        def page_at(offset):
            index = bisect.bisect_left(page_offsets, offset)
            if index >= len(parsed.pages) or parsed.pages[index].raw == "PageV00P000":
                return None
            return parsed.pages[index].raw
        trie, section_rows, biography_count = {}, [], 0
        for section in parsed.sections:
            full = text[section.start:section.end]
            title = section.heading.title if section.heading else "بيانات المصدر الأصلية"
            section_id = db.execute("INSERT INTO source_sections(source_id,source_ordinal,heading,section_kind,full_text,source_char_start,source_char_end) VALUES (?,?,?,?,?,?,?)",
                                    (source_id, section.ordinal, title, section.kind, full, section.start, section.end)).lastrowid
            biography_id, person_id = None, None
            if section.kind == "biography":
                biography_count += 1
                name = canonical_from_heading(title)
                if name and any(char.isalpha() for char in name):
                    person_id = db.execute("INSERT INTO people(source_id,canonical_name,normalized_name) VALUES (?,?,?)", (source_id, name, normalize_arabic(name))).lastrowid
                biography_id = db.execute("INSERT INTO biographies(source_id,section_id,subject_person_id,source_ordinal,heading,normalized_heading,full_text,normalized_search_text,source_char_start,source_char_end,first_page_marker,last_page_marker) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                        (source_id, section_id, person_id, biography_count, title, normalize_arabic(title), full, normalize_arabic(full), section.start, section.end,
                         page_at(section.start), page_at(max(section.start,section.end - 1)))).lastrowid
                db.execute("INSERT INTO biographies_fts(biography_id,normalized_heading,normalized_search_text) SELECT id,normalized_heading,normalized_search_text FROM biographies WHERE id=?", (biography_id,))
                if person_id:
                    for alias in aliases_from_name(name):
                        normalized = normalize_arabic(alias)
                        db.execute("INSERT OR IGNORE INTO person_aliases(person_id,alias,normalized_alias,evidence_biography_id) VALUES (?,?,?,?)", (person_id, alias, normalized, biography_id))
                        _trie_add(trie, normalized, person_id)
            section_rows.append((section, section_id, biography_id, person_id))
        topics = []
        for topic in db.execute("SELECT id,name,search_terms_csv FROM topics WHERE parent_id IS NOT NULL"):
            terms = sorted({normalize_arabic(term) for term in (topic["search_terms_csv"] or "").split(",") if term.strip()}, key=len, reverse=True)
            # A literal fire is too ambiguous to propose an eschatological topic on its own.
            terms = [term for term in terms if term != "النار"]
            if terms:
                topics.append((topic["id"], re.compile(r"(?<!\w)(?:" + "|".join(re.escape(term) for term in terms) + r")(?!\w)")))
        segment_count = hit_count = mention_count = 0
        for section, section_id, biography_id, subject_id in section_rows:
            if section.kind == "front_matter":
                continue
            for ordinal, (start, end) in enumerate(paragraph_ranges(text, section.start, section.end), 1):
                original = text[start:end]
                normalized, positions = normalize_with_map(original)
                segment_id = db.execute("INSERT INTO segments(source_id,section_id,biography_id,segment_ordinal,text_original,normalized_search_text,source_char_start,source_char_end,page_marker) VALUES (?,?,?,?,?,?,?,?,?)",
                    (source_id, section_id, biography_id, ordinal, original, normalized, start, end, page_at(start))).lastrowid
                db.execute("INSERT INTO segments_fts(segment_id,normalized_search_text) VALUES (?,?)", (segment_id, normalized))
                segment_count += 1
                for topic_id, pattern in topics:
                    match = pattern.search(normalized)
                    if match:
                        a, b = start + positions[match.start()][0], start + positions[match.end()-1][1]
                        literal = text[a:b]
                        db.execute("INSERT INTO topic_hits(topic_id,segment_id,evidence_text,source_char_start,source_char_end,matched_phrase,method,confidence) VALUES (?,?,?,?,?,?,?,?)",
                            (topic_id, segment_id, literal, a, b, match.group(), "keyword_candidate", 0.35))
                        hit_count += 1
                seen = set()
                for a, b, candidates, alias in _mention_matches(trie, normalized):
                    candidates = candidates - {subject_id}
                    if not candidates or (a,b) in seen:
                        continue
                    seen.add((a,b))
                    mention_start, mention_end = start + positions[a][0], start + positions[b-1][1]
                    literal = text[mention_start:mention_end]
                    mention_id = db.execute("INSERT INTO person_mentions(source_id,biography_id,section_id,segment_id,mention_literal,evidence_text,source_char_start,source_char_end,matched_alias) VALUES (?,?,?,?,?,?,?,?,?)",
                            (source_id, biography_id, section_id, segment_id, literal, literal, mention_start, mention_end, alias)).lastrowid
                    db.executemany("INSERT INTO mention_candidates(mention_id,person_id) VALUES (?,?)", [(mention_id, pid) for pid in candidates])
                    mention_count += 1
        return {"source_id": source_id, "sha256": sha, "unchanged": False, "biographies": biography_count,
                "sections": len(parsed.sections), "segments": segment_count, "topic_hits": hit_count, "mentions": mention_count,
                "page_markers": len(parsed.pages), "source_chars": len(text), "covered_chars": sum(s.end-s.start for s in parsed.sections),
                "seconds": round(time.monotonic()-started, 2)}


def main():
    parser = argparse.ArgumentParser(description="استيراد OpenITI محليًا مع حفظ الأصل ونطاقاته")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    try:
        print(json.dumps(import_book(args.source, args.db), ensure_ascii=False, indent=2))
    except (FileNotFoundError, ValueError, UnicodeError) as error:
        parser.exit(1, str(error) + "\n")

if __name__ == "__main__":
    main()
