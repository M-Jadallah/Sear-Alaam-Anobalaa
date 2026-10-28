PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS schema_versions(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP);
INSERT OR IGNORE INTO schema_versions(version) VALUES (1);
CREATE TABLE IF NOT EXISTS sources (
 id INTEGER PRIMARY KEY, title TEXT NOT NULL, source_url TEXT NOT NULL, file_name TEXT NOT NULL,
 sha256 TEXT NOT NULL UNIQUE, full_text TEXT NOT NULL, byte_count INTEGER NOT NULL, char_count INTEGER NOT NULL,
 metadata TEXT NOT NULL, edition_note TEXT, license_note TEXT NOT NULL, page_policy TEXT NOT NULL,
 parser_version TEXT NOT NULL, is_synthetic INTEGER NOT NULL DEFAULT 0, imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS page_markers (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id), raw_marker TEXT NOT NULL,
 source_char_offset INTEGER NOT NULL, source_char_end INTEGER NOT NULL, source_line INTEGER NOT NULL,
 verified_print_page INTEGER NOT NULL DEFAULT 0, is_placeholder INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS people (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id), canonical_name TEXT NOT NULL,
 normalized_name TEXT NOT NULL, death_year_ah INTEGER, identity_review_status TEXT NOT NULL DEFAULT 'candidate'
);
CREATE TABLE IF NOT EXISTS source_sections (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id), source_ordinal INTEGER NOT NULL,
 heading TEXT NOT NULL, section_kind TEXT NOT NULL, full_text TEXT NOT NULL,
 source_char_start INTEGER NOT NULL, source_char_end INTEGER NOT NULL, UNIQUE(source_id,source_ordinal)
);
CREATE TABLE IF NOT EXISTS biographies (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id), section_id INTEGER NOT NULL REFERENCES source_sections(id),
 subject_person_id INTEGER REFERENCES people(id), source_ordinal INTEGER NOT NULL, heading TEXT NOT NULL,
 normalized_heading TEXT NOT NULL, full_text TEXT NOT NULL, normalized_search_text TEXT NOT NULL,
 source_char_start INTEGER NOT NULL, source_char_end INTEGER NOT NULL, first_page_marker TEXT, last_page_marker TEXT,
 extraction_status TEXT NOT NULL DEFAULT 'candidate', UNIQUE(source_id,source_ordinal)
);
CREATE TABLE IF NOT EXISTS person_aliases (
 id INTEGER PRIMARY KEY, person_id INTEGER NOT NULL REFERENCES people(id), alias TEXT NOT NULL,
 normalized_alias TEXT NOT NULL, evidence_biography_id INTEGER REFERENCES biographies(id),
 review_status TEXT NOT NULL DEFAULT 'candidate', UNIQUE(person_id,normalized_alias)
);
CREATE TABLE IF NOT EXISTS segments (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id), section_id INTEGER NOT NULL REFERENCES source_sections(id),
 biography_id INTEGER REFERENCES biographies(id), segment_ordinal INTEGER NOT NULL,
 text_original TEXT NOT NULL, normalized_search_text TEXT NOT NULL,
 source_char_start INTEGER NOT NULL, source_char_end INTEGER NOT NULL,
 segment_type TEXT NOT NULL DEFAULT 'paragraph', segmentation_status TEXT NOT NULL DEFAULT 'candidate', page_marker TEXT,
 CHECK(source_char_end>source_char_start), UNIQUE(section_id,segment_ordinal)
);
CREATE TABLE IF NOT EXISTS person_mentions (
 id INTEGER PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id), biography_id INTEGER REFERENCES biographies(id),
 section_id INTEGER NOT NULL REFERENCES source_sections(id), segment_id INTEGER NOT NULL REFERENCES segments(id),
 person_id INTEGER REFERENCES people(id), mention_literal TEXT NOT NULL, evidence_text TEXT NOT NULL,
 source_char_start INTEGER NOT NULL, source_char_end INTEGER NOT NULL, matched_alias TEXT NOT NULL,
 identity_review_status TEXT NOT NULL DEFAULT 'candidate', reviewer_note TEXT,
 CHECK(identity_review_status IN ('candidate','reviewed','rejected'))
);
CREATE TABLE IF NOT EXISTS mention_candidates (
 mention_id INTEGER NOT NULL REFERENCES person_mentions(id), person_id INTEGER NOT NULL REFERENCES people(id),
 PRIMARY KEY(mention_id,person_id)
);
CREATE TABLE IF NOT EXISTS topics (
 id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES topics(id), name TEXT NOT NULL,
 search_terms_csv TEXT, dictionary_status TEXT NOT NULL DEFAULT 'proposed', UNIQUE(parent_id,name)
);
CREATE TABLE IF NOT EXISTS topic_hits (
 id INTEGER PRIMARY KEY, topic_id INTEGER NOT NULL REFERENCES topics(id), segment_id INTEGER NOT NULL REFERENCES segments(id),
 evidence_text TEXT NOT NULL, source_char_start INTEGER NOT NULL, source_char_end INTEGER NOT NULL,
 matched_phrase TEXT, method TEXT NOT NULL CHECK(method IN ('keyword_candidate','semantic_candidate','manual')),
 review_status TEXT NOT NULL DEFAULT 'candidate' CHECK(review_status IN ('candidate','reviewed','rejected')),
 confidence REAL, reviewer_note TEXT, UNIQUE(topic_id,segment_id)
);
CREATE TABLE IF NOT EXISTS review_log (
 id INTEGER PRIMARY KEY, entity_type TEXT NOT NULL, entity_id INTEGER NOT NULL, change_summary TEXT NOT NULL,
 reviewer TEXT NOT NULL, changed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE VIRTUAL TABLE IF NOT EXISTS biographies_fts USING fts5(biography_id UNINDEXED,normalized_heading,normalized_search_text,tokenize='unicode61');
CREATE VIRTUAL TABLE IF NOT EXISTS segments_fts USING fts5(segment_id UNINDEXED,normalized_search_text,tokenize='unicode61');
CREATE INDEX IF NOT EXISTS idx_markers ON page_markers(source_id,source_char_offset);
CREATE INDEX IF NOT EXISTS idx_bio_title ON biographies(normalized_heading);
CREATE INDEX IF NOT EXISTS idx_bio_subject ON biographies(subject_person_id);
CREATE INDEX IF NOT EXISTS idx_people_name ON people(normalized_name);
CREATE INDEX IF NOT EXISTS idx_alias_name ON person_aliases(normalized_alias);
CREATE INDEX IF NOT EXISTS idx_segments_bio ON segments(biography_id,segment_ordinal);
CREATE INDEX IF NOT EXISTS idx_segments_source_range ON segments(source_id,source_char_start);
CREATE INDEX IF NOT EXISTS idx_mentions_person ON person_mentions(person_id,identity_review_status);
CREATE INDEX IF NOT EXISTS idx_mentions_segment ON person_mentions(segment_id);
CREATE INDEX IF NOT EXISTS idx_candidates_person ON mention_candidates(person_id,mention_id);
CREATE INDEX IF NOT EXISTS idx_hits_topic_review ON topic_hits(topic_id,review_status);
CREATE INDEX IF NOT EXISTS idx_hits_segment ON topic_hits(segment_id);
