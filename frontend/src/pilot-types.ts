import type { ReadingView } from './api';

export interface SourceRange { start: number; end: number }
export interface CurationBatch {
  id: string;
  label: string;
  source_id: number;
  start_ordinal: number;
  end_ordinal: number;
  legacy: boolean;
  published: boolean;
  biographies: { id: number; source_ordinal: number; heading: string }[];
}
export interface SourceCriticism extends SourceRange {
  context_start: number;
  context_end: number;
  kind: 'hadith' | 'isnad' | 'narrator' | 'report';
  reason: string;
}
export interface AddedDiacritic {
  /** Zero-width boundary in the original Unicode code points. */
  at: number;
  base_start: number;
  marks: string;
}
export interface DiacriticsLayer {
  revision?: string;
  status: 'ready' | 'partial' | 'stale' | 'invalid';
  insertions: AddedDiacritic[];
  coverage: { total_words: number; checked_words: number; complete_words: number; uncertain_words: number; not_applicable: number };
  uncertainties: (SourceRange & { reason: string })[];
  notice?: string;
}
export interface PilotAnnotations {
  headings: { at: number; label: string }[];
  quotes: SourceRange[];
  poetry: SourceRange[];
  lists: (SourceRange & { ordered: boolean; items: SourceRange[] })[];
  bold: SourceRange[];
  keywords: SourceRange[];
  names: SourceRange[];
  footnotes: (SourceRange & { raw: string; context: string })[];
  footnote_notice?: string;
}
export interface PilotPresentation {
  available: boolean;
  status: 'ready' | 'not_available' | 'stale';
  revision?: string;
  presentation_revision?: string;
  paragraph_groups?: SourceRange[];
  criticism?: SourceCriticism[];
  source_sha256?: string;
  biography_id: number;
  batch_id?: string;
  batch_label?: string;
  footnote_notice?: string;
  annotations: PilotAnnotations;
  warning?: string;
  reading_view?: ReadingView;
  plain_text?: string;
  diacritics?: DiacriticsLayer;
  vocalized_plain_text?: string;
}
export interface TopicAudit {
  id: number;
  topic_id: number;
  topic_name: string;
  biography_id: number;
  segment_id: number;
  evidence_text: string;
  source_char_start: number;
  source_char_end: number;
  context_text: string;
  context_char_start: number;
  context_char_end: number;
  relation_type: string;
  subject_scope: string;
  stance: string;
  rationale: string;
  agent_decision: 'supported' | 'uncertain' | 'exclude';
  human_status: 'pending' | 'reviewed' | 'rejected';
  original_hit_id?: number;
  reviewer?: string;
  reading_view?: ReadingView;
  batch_id?: string;
}
export interface PilotTopics {
  pilot: boolean;
  items: {
    topic_id: number;
    name: string;
    dictionary_status: string;
    evidence_count: number;
    human_reviewed_count: number;
    evidence: TopicAudit[];
  }[];
  coverage: { total: number; agent_checked: number; human_checked: number; uncertain: number; no_clear_topic: number };
}
