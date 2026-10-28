CREATE TABLE IF NOT EXISTS curation_batches (
 id TEXT PRIMARY KEY, source_id INTEGER NOT NULL REFERENCES sources(id), source_sha256 TEXT NOT NULL,
 start_ordinal INTEGER NOT NULL, end_ordinal INTEGER NOT NULL, revision TEXT NOT NULL,
 label TEXT NOT NULL, legacy INTEGER NOT NULL DEFAULT 0, manifest_json TEXT NOT NULL,
 published_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS curation_batch_members (
 batch_id TEXT NOT NULL REFERENCES curation_batches(id), biography_id INTEGER NOT NULL UNIQUE REFERENCES biographies(id),
 source_ordinal INTEGER NOT NULL, PRIMARY KEY(batch_id,biography_id)
);
