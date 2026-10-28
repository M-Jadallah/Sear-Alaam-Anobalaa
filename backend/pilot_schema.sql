CREATE TABLE IF NOT EXISTS pilot_documents (
 biography_id INTEGER PRIMARY KEY REFERENCES biographies(id), source_id INTEGER NOT NULL REFERENCES sources(id),
 source_sha256 TEXT NOT NULL, revision TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pilot_coverage (
 segment_id INTEGER PRIMARY KEY REFERENCES segments(id), biography_id INTEGER NOT NULL REFERENCES biographies(id),
 assessment TEXT NOT NULL, note TEXT NOT NULL, reviewer TEXT NOT NULL, human_checked INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pilot_evidence (
 id INTEGER PRIMARY KEY, stable_key TEXT NOT NULL UNIQUE, biography_id INTEGER NOT NULL REFERENCES biographies(id),
 segment_id INTEGER NOT NULL REFERENCES segments(id), source_id INTEGER NOT NULL REFERENCES sources(id),
 source_sha256 TEXT NOT NULL, topic_id INTEGER NOT NULL REFERENCES topics(id), original_hit_id INTEGER REFERENCES topic_hits(id),
 source_char_start INTEGER NOT NULL, source_char_end INTEGER NOT NULL, context_char_start INTEGER NOT NULL, context_char_end INTEGER NOT NULL,
 rationale TEXT NOT NULL, relation_type TEXT NOT NULL, subject_scope TEXT NOT NULL, stance TEXT NOT NULL,
 agent_decision TEXT NOT NULL, human_status TEXT NOT NULL DEFAULT 'pending', reviewer TEXT NOT NULL,
 human_reviewer TEXT, human_note TEXT, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pilot_events (
 id INTEGER PRIMARY KEY, evidence_id INTEGER REFERENCES pilot_evidence(id), actor_kind TEXT NOT NULL,
 reviewer TEXT NOT NULL, before_json TEXT, after_json TEXT NOT NULL, note TEXT NOT NULL,
 created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS pilot_topic_aliases (
 topic_id INTEGER NOT NULL REFERENCES topics(id), alias TEXT NOT NULL, PRIMARY KEY(topic_id,alias)
);
CREATE VIRTUAL TABLE IF NOT EXISTS pilot_topics_fts USING fts5(evidence_id UNINDEXED,topic_text,context_text,tokenize='unicode61');
CREATE INDEX IF NOT EXISTS idx_pilot_evidence_topic ON pilot_evidence(topic_id,human_status,agent_decision);
CREATE INDEX IF NOT EXISTS idx_pilot_evidence_bio ON pilot_evidence(biography_id);
