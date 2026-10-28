import { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { number, params, postReview, useApi, useAllTopics } from './api';
import type { List, Row } from './api';
import { LoadState, Notice, PageTitle, Pagination } from './components';
import { AuditEvidence } from './pilot-reader';
import type { TopicAudit } from './pilot-types';
import { FormattedText } from './formatted-text';
import type { ReadingView } from './api';
import { useCurationBatches } from './curation-batches';
import { useAuth } from './auth';

interface AuditPage extends Omit<List, 'items'> { items: TopicAudit[] }
interface CoverageRow {
  segment_id: number; biography_id: number; text_original: string;
  source_char_start: number; source_char_end: number; assessment: string;
  note: string; reviewer: string; human_checked: number; reading_view?: ReadingView;
  batch_id?: string;
}
interface CoveragePage extends Omit<List, 'items'> { items: CoverageRow[] }
function CoverageReview({ item, batchId, saved }: { item: CoverageRow; batchId?: string; saved: () => void }) {
  const { session } = useAuth();
  const [reviewer, setReviewer] = useState(session?.mode === 'production' ? session.username ?? '' : ''), [note, setNote] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const save = async () => {
    setBusy(true); setError('');
    try { await postReview(`/api/pilot/coverage/${item.segment_id}`, { reviewer: reviewer.trim(), note, ...(batchId ? { batch_id: batchId } : {}) }); saved(); }
    catch (error) { setError((error as Error).message); }
    finally { setBusy(false); }
  };
  return <article className="panel pilot-review-card">
    <h2>مقطع {number(item.segment_id)} <span className="muted small">· ترجمة {number(item.biography_id)}</span></h2>
    <p className="small">تقييم الوكيل: {item.assessment === 'supported' ? 'صلة موضوعية مقترحة' : item.assessment === 'no_clear_topic' ? 'لا موضوع واضح' : 'دلالة غير محسومة'} · {item.human_checked ? 'فُحص بشريًا' : 'بانتظار فحصك'}</p>
    <div className="pilot-topic-evidence"><blockquote>{item.reading_view ? <FormattedText view={item.reading_view} /> : item.text_original}</blockquote></div>
    <p className="small">{item.note}</p>
    <Link className="text-link" to={`/reader/${item.biography_id}?start=${item.source_char_start}&end=${item.source_char_end}`}>قراءة السياق الكامل</Link>
    <div className="form-grid"><label>اسم المراجع البشري<input aria-label={`مراجع المقطع ${item.segment_id}`} value={reviewer} onChange={event => setReviewer(event.target.value)} maxLength={100} /></label><label>ملاحظة الفحص<input aria-label={`ملاحظة المقطع ${item.segment_id}`} value={note} onChange={event => setNote(event.target.value)} maxLength={3000} /></label></div>
    <p className="small muted">تسجيل فحص المقطع لا يعتمد وسومه الموضوعية؛ تُراجع توصيات الوسوم في تبويب مستقل.</p>
    <button className="primary" disabled={!reviewer.trim() || busy || item.human_checked === 1} onClick={() => void save()}>{item.human_checked ? 'سُجّل الفحص البشري' : 'تسجيل الفحص البشري للمقطع'}</button>
    {error && <Notice danger>{error}</Notice>}
  </article>;
}
function AuditReview({ item, topics, batchId, saved }: { item: TopicAudit; topics: Row[]; batchId?: string; saved: () => void }) {
  const [topicId, setTopicId] = useState(item.topic_id);
  const [start, setStart] = useState(String(item.source_char_start));
  const [end, setEnd] = useState(String(item.source_char_end));
  const { session } = useAuth();
  const [reviewer, setReviewer] = useState(session?.mode === 'production' ? session.username ?? '' : '');
  const [note, setNote] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [decision, setDecision] = useState('');
  const submit = async (value: 'reviewed' | 'rejected') => {
    setBusy(true); setError('');
    try {
      await postReview(`/api/pilot/reviews/${item.id}`, { decision: value, reviewer: reviewer.trim(), note, topic_id: topicId, source_char_start: Number(start), source_char_end: Number(end), ...(batchId ? { batch_id: batchId } : {}) });
      setDecision(value === 'reviewed' ? 'حُفظ الاعتماد البشري.' : 'حُفظ الرفض البشري.'); saved();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  };
  const valid = item.human_status === 'pending' && reviewer.trim().length > 0 && Number.isInteger(Number(start)) && Number.isInteger(Number(end)) && Number(end) > Number(start);
  return <article className="panel pilot-review-card">
    <h2>{item.topic_name} <span className="muted small">· ترجمة {number(item.biography_id)} · مقطع {number(item.segment_id)}</span></h2>
    <AuditEvidence item={item} />
    <p className="small">توصية الوكيل: {item.agent_decision === 'supported' ? 'صلة مدعومة نصيًا' : item.agent_decision === 'exclude' ? 'استبعاد الربط' : 'دلالة غير محسومة'}</p>
    <details className="context-details"><summary>السياق الحرفي المحيط</summary><pre className="original-text">{item.context_text}</pre></details>
    <div className="form-grid">
      <label>الموضوع<select aria-label={`موضوع المقترح ${item.id}`} value={topicId} onChange={event => setTopicId(Number(event.target.value))}>{!topics.some(topic => topic.id === item.topic_id) && <option value={item.topic_id}>{item.topic_name}</option>}{topics.map(topic => <option key={topic.id} value={topic.id}>{topic.name}</option>)}</select></label>
      <label>اسم المراجع البشري<input aria-label={`اسم المراجع ${item.id}`} value={reviewer} onChange={event => setReviewer(event.target.value)} maxLength={100} /></label>
      <label>بداية الشاهد في الأصل<input dir="ltr" inputMode="numeric" aria-label={`بداية الشاهد ${item.id}`} value={start} onChange={event => setStart(event.target.value)} /></label>
      <label>نهاية الشاهد في الأصل<input dir="ltr" inputMode="numeric" aria-label={`نهاية الشاهد ${item.id}`} value={end} onChange={event => setEnd(event.target.value)} /></label>
      <label className="full-field">سبب القرار أو التعديل<textarea aria-label={`ملاحظة المراجع ${item.id}`} value={note} onChange={event => setNote(event.target.value)} maxLength={3000} /></label>
    </div>
    <p className="small muted">الحدود بوحدات Unicode؛ يتحقق الخادم من حرفية الشاهد وحدوده قبل حفظ القرار. {item.agent_decision === 'exclude' ? 'اعتماد الاستبعاد يرفض الربط اللفظي الأصلي؛ رفض التوصية يبقي الربط دون اعتماد جديد.' : 'اعتمادك يسجل قرارًا بشريًا مستقلًا عن فحص الوكيل.'}</p>
    <div className="pilot-review-actions"><button className="primary" disabled={busy || !valid} onClick={() => void submit('reviewed')}>{item.agent_decision === 'exclude' ? 'اعتماد الاستبعاد' : 'اعتماد بشري وحفظ التعديل'}</button><button disabled={busy || !valid} onClick={() => void submit('rejected')}>رفض توصية الوكيل</button></div>
    {error && <Notice danger>{error}</Notice>}{decision && <p role="status">{decision}</p>}
  </article>;
}

export function PilotReviewsPage() {
  const [query, setQuery] = useSearchParams();
  const [page, setPage] = useState(1), [revision, setRevision] = useState(0);
  const biography = query.get('biography_id') ?? '';
  const batchId = query.get('batch_id') ?? '';
  const status = query.get('status') ?? 'pending';
  const kind = query.get('kind') === 'coverage' ? 'coverage' : 'topics';
  const batches = useCurationBatches();
  const oldBiographies = useApi<List>(!batches.loading && !batches.items.length ? '/api/biographies?page_size=5' : null);
  const selectedBatch = batches.items.find(batch => batch.id === batchId);
  const batchBiographies = (selectedBatch ? [selectedBatch] : batches.items).flatMap(batch => batch.biographies);
  const biographies: Row[] = batches.items.length ? [...new Map(batchBiographies.map(item => [item.id, item])).values()] : oldBiographies.data?.items ?? [];
  const audits = useApi<AuditPage>(kind === 'topics' ? `/api/pilot/reviews?${params({ batch_id: batchId, biography_id: biography, status, page, page_size: 10 })}` : null, revision);
  const coverage = useApi<CoveragePage>(kind === 'coverage' ? `/api/pilot/coverage?${params({ batch_id: batchId, biography_id: biography, status, page, page_size: 10 })}` : null, revision);
  const topics = useAllTopics(kind === 'topics' ? '' : null);
  useEffect(() => setPage(1), [biography, status, kind, batchId]);
  return <>
    <Link className="text-link back" to="/reviews">مراجعة الفهرسة</Link>
    <PageTitle title={batches.items.some(batch => !batch.legacy) ? 'مراجعة الدفعات التحريرية' : 'مراجعة موضوعات العينة'}>مقترحات مستندة إلى قراءة النص؛ الاعتماد البشري يُسجّل بقرار مستقل مع اسمك وسببه.</PageTitle>
    <nav className="tabs" aria-label="نوع مراجعة العينة"><Link className={kind === 'topics' ? 'selected' : ''} to={`/pilot/reviews?${params({ batch_id: batchId, biography_id: biography })}`}>توصيات الموضوعات</Link><Link className={kind === 'coverage' ? 'selected' : ''} to={`/pilot/reviews?${params({ kind: 'coverage', batch_id: batchId, biography_id: biography })}`}>تغطية فحص المقاطع</Link></nav>
    <div className="pilot-review-filters">
      {batches.items.length > 0 && <label>الدفعة التحريرية<select aria-label="دفعة المراجعة" value={batchId} onChange={event => setQuery({ kind, batch_id: event.target.value, status })}><option value="">جميع الدفعات المنشورة</option>{batches.items.map(batch => <option key={batch.id} value={batch.id}>{batch.label}{batch.legacy ? ' · العينة الأولى' : ''}</option>)}</select></label>}
      <label>الترجمة<select aria-label="ترجمة العينة" value={biography} onChange={event => setQuery({ kind, batch_id: batchId, biography_id: event.target.value, status })}><option value="">{selectedBatch ? 'جميع تراجم الدفعة' : batches.items.length ? 'جميع التراجم التحريرية' : 'التراجم الخمس'}</option>{biographies.map(item => <option key={item.id} value={item.id}>{item.source_ordinal ? `${number(item.source_ordinal)} — ` : ''}{item.heading ?? item.name}</option>)}</select></label>
      <label>حالة القرار البشري<select aria-label="حالة مراجعة العينة" value={status} onChange={event => setQuery({ kind, batch_id: batchId, biography_id: biography, status: event.target.value })}><option value="pending">بانتظار {kind === 'coverage' ? 'فحصك' : 'اعتمادك'}</option><option value="reviewed">{kind === 'coverage' ? 'مفحوص بشريًا' : 'معتمد بشريًا'}</option>{kind !== 'coverage' && <option value="rejected">مرفوض بشريًا</option>}<option value="all">جميع الحالات</option></select></label>
    </div>
    <LoadState {...(kind === 'coverage' ? coverage : audits)} />
    {kind === 'topics' && audits.data?.items.map(item => <AuditReview key={`${batchId}-${item.id}-${revision}`} item={item} batchId={batchId || item.batch_id} topics={topics.data?.items ?? []} saved={() => setRevision(value => value + 1)} />)}
    {kind === 'coverage' && coverage.data?.items.map(item => <CoverageReview key={`${batchId}-${item.segment_id}-${revision}`} item={item} batchId={batchId || item.batch_id} saved={() => setRevision(value => value + 1)} />)}
    {(kind === 'coverage' ? coverage.data : audits.data)?.total === 0 && <p>لا توجد مقترحات بهذه الشروط.</p>}
    <Pagination data={(kind === 'coverage' ? coverage.data : audits.data) as unknown as List} page={page} setPage={setPage} />
  </>;
}
