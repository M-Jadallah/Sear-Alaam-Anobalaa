CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS windows(
 id INTEGER PRIMARY KEY,source_id INTEGER NOT NULL,source_sha256 TEXT NOT NULL,
 section_id INTEGER NOT NULL,biography_id INTEGER,heading TEXT NOT NULL,
 source_char_start INTEGER NOT NULL,source_char_end INTEGER NOT NULL,
 clean_text TEXT NOT NULL,token_count INTEGER NOT NULL,
 CHECK(source_char_end>source_char_start)
);
CREATE INDEX IF NOT EXISTS windows_source_range ON windows(source_id,source_char_start,source_char_end);
CREATE INDEX IF NOT EXISTS windows_section ON windows(section_id);
CREATE VIRTUAL TABLE IF NOT EXISTS windows_fts USING fts5(window_id UNINDEXED,normalized_text,tokenize='unicode61');
