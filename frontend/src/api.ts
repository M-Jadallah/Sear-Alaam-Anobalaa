import { useEffect, useState } from "react";
import { authHeaders } from './auth';

export interface Row {
  id: number;
  name?: string;
  heading?: string;
  canonical_name?: string;
  text_original?: string;
  full_text?: string;
  source_id?: number;
  biography_id?: number;
  section_id?: number;
  segment_id?: number;
  source_char_start?: number;
  source_char_end?: number;
  page_marker?: string;
  first_page_marker?: string;
  last_page_marker?: string;
  page_marker_crossed?: boolean;
  subject_person_id?: number;
  source_ordinal?: number;
  parent_id?: number;
  topic_name?: string;
  topic_id?: number;
  topic_count?: number;
  hit_count?: number;
  reviewed_count?: number;
  mention_count?: number;
  evidence_text?: string;
  matched_phrase?: string;
  method?: string;
  review_status?: string;
  identity_review_status?: string;
  extraction_status?: string;
  segmentation_status?: string;
  segment_type?: string;
  mention_literal?: string;
  person_id?: number;
  match_type?: string;
  confidence?: number;
  previous_id?: number;
  next_id?: number;
  search_terms_csv?: string;
  alias?: string;
  change_summary?: string;
  changed_at?: string;
  entity_type?: string;
  entity_id?: number;
  reviewer?: string;
  source?: Source;
  candidates?: { id: number; canonical_name: string }[];
  reading_view?: ReadingView;
  context_reading_view?: ReadingView;
  context_char_start?: number;
  context_char_end?: number;
  context_text?: string;
  reader_url?: string;
  explanation?: string;
  reason?: string;
  retrieval_methods?: string[];
  topic_review_status?: string;
  topic_audit_id?: number;
  relation_type?: string;
  subject_scope?: string;
  stance?: string;
  dictionary_status?: string;
}
export interface ReadingRun {
  type: "text" | "virtual_space" | "page" | "annotation";
  text: string;
  source_char_start: number;
  source_char_end: number;
  raw_marker?: string;
  is_unknown?: boolean;
}
export interface ReadingBlock {
  id: string;
  type: "heading" | "paragraph";
  level?: number;
  source_char_start: number;
  source_char_end: number;
  runs: ReadingRun[];
}
export interface ReadingView {
  renderer_version: string;
  source_char_start: number;
  source_char_end: number;
  blocks: ReadingBlock[];
  plain_text: string;
  coverage?: {
    source_chars: number;
    accounted_chars: number;
    complete: boolean;
  };
}
export interface Source {
  id?: number;
  title?: string;
  sha256?: string;
  file_name?: string;
  source_url?: string;
  page_policy?: string;
  metadata?: Record<string, unknown> | string;
  coverage?: Record<string, unknown> | string;
  edition_note?: string;
  license_note?: string;
  imported_at?: string;
  [key: string]: unknown;
}
export interface List {
  items: Row[];
  total: number;
  page: number;
  page_size: number;
}
export interface Stats {
  sources: number;
  biographies: number;
  people: number;
  topics: number;
  categories: number;
  segments: number;
  topic_hits: number;
  mentions: number;
  reviewed: number;
  pending: number;
  pending_segments?: number;
  pending_people?: number;
  pending_topics?: number;
  pending_mentions?: number;
  source_missing: boolean;
  source_file_exists: boolean;
  warning?: string;
  source?: Source;
  [key: string]: unknown;
}
export function useApi<T>(url: string | null, refresh = 0) {
  const [state, setState] = useState<{
    data?: T;
    error?: string;
    loading: boolean;
  }>({ loading: !!url });
  useEffect(() => {
    if (!url) {
      setState({ loading: false });
      return;
    }
    const controller = new AbortController();
    setState({ loading: true });
    fetch(url, { signal: controller.signal })
      .then(async (r) => {
        if (!r.ok) {
          const body = await r.json().catch(() => ({}));
          throw new Error(
            typeof body.detail === "string"
              ? body.detail
              : "تعذر تحميل البيانات.",
          );
        }
        return r.json() as Promise<T>;
      })
      .then((data) => setState({ data, loading: false }))
      .catch((e: Error) => {
        if (e.name !== "AbortError")
          setState({
            error: e.message || "تعذر الاتصال بالخادم المحلي.",
            loading: false,
          });
      });
    return () => controller.abort();
  }, [url, refresh]);
  return state;
}

// The topic catalog can grow beyond the CSV's 81 entries. Fetch every page
// rather than silently dropping proposed topics from selectors.
export function useAllTopics(filters: string | null = '', refresh = 0) {
  const [state, setState] = useState<{ data?: List; error?: string; loading: boolean }>({ loading: true });
  useEffect(() => {
    if (filters === null) { setState({ loading: false }); return; }
    const controller = new AbortController();
    setState({ loading: true });
    void (async () => {
      const items: Row[] = [];
      let page = 1, total = 0;
      do {
        const query = new URLSearchParams(filters);
        query.set('page_size', '100'); query.set('page', String(page));
        const response = await fetch(`/api/topics?${query}`, { signal: controller.signal });
        if (!response.ok) throw new Error('تعذر تحميل فهرس الموضوعات.');
        const result = await response.json() as List;
        if (controller.signal.aborted) return;
        if (result.items.length === 0 && items.length < result.total) throw new Error('لم يكتمل تحميل فهرس الموضوعات؛ أعد المحاولة.');
        items.push(...result.items); total = result.total; page++;
      } while (items.length < total);
      setState({ data: { items, total, page: 1, page_size: Math.max(total, 100) }, loading: false });
    })().catch((error: Error) => { if (error.name !== 'AbortError') setState({ error: error.message, loading: false }); });
    return () => controller.abort();
  }, [filters, refresh]);
  return state;
}
export async function postReview(path: string, body: object) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...await authHeaders() },
    body: JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok)
    throw new Error(
      typeof data.detail === "string"
        ? data.detail
        : "لم يُحفظ التعديل؛ تحقق من النطاق والحقول.",
    );
  window.dispatchEvent(new Event("siyar-review"));
  return data;
}
export async function postApi<T>(
  path: string,
  body: object,
  signal?: AbortSignal,
): Promise<T> {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(path.startsWith('/api/search/') ? {} : await authHeaders()) },
    body: JSON.stringify(body),
    signal,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail =
      typeof data.detail === "string"
        ? data.detail
        : "تعذر تنفيذ الطلب؛ تحقق من طول الوصف والحقول ثم حاول مجددًا.";
    throw new Error(detail);
  }
  return data as T;
}
export const number = (value: number | undefined) =>
  new Intl.NumberFormat("ar").format(value ?? 0);
export const params = (values: Record<string, string | number | undefined>) =>
  new URLSearchParams(
    Object.entries(values)
      .filter(([, v]) => v !== undefined && v !== "")
      .map(([k, v]) => [k, String(v)]),
  ).toString();
