import { Fragment, useEffect, useMemo, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import type { ReadingBlock, ReadingRun, ReadingView } from './api';
import { number } from './api';
import { FormattedText } from './formatted-text';
import { clusterText, diacriticsByBase, sourceClusters, usableDiacritics } from './diacritics-text';
import type { AddedDiacritic, PilotAnnotations, PilotPresentation, PilotTopics, SourceCriticism, SourceRange, TopicAudit } from './pilot-types';

const overlaps = (a: SourceRange, start: number, end: number) => a.start < end && a.end > start;
const covers = (ranges: SourceRange[], start: number, end: number) => ranges.some(a => a.start <= start && a.end >= end);

function PageRun({ run }: { run: ReadingRun }) {
  const [open, setOpen] = useState(false);
  return <span className="page-anchor" data-source-start={run.source_char_start} data-source-end={run.source_char_end}>
    <button type="button" className="page-reference-button" aria-label={`إحالة ${run.text}`} aria-expanded={open} onClick={() => setOpen(!open)} onKeyDown={e => { if (e.key === 'Escape') setOpen(false); }}><bdi>{run.is_unknown ? 'صفحة غير محددة' : run.text}</bdi></button>
    {open && <span className="page-popover" role="note"><bdi>{run.raw_marker ?? run.text}</bdi><br />{run.is_unknown ? 'علامة صفرية؛ لا تعيّن صفحة.' : 'علامة OpenITI خام، غير محققة طباعيًا.'}</span>}
  </span>;
}

const NO_CRITICISM: SourceCriticism[] = [];
interface InlineProps { run: ReadingRun; from: number; to: number; annotations: PilotAnnotations; criticism: SourceCriticism[]; inlineQuotes: boolean; start?: number; end?: number }
function Inline({ run, from, to, annotations, criticism, inlineQuotes, start, end }: { run: ReadingRun; from: number; to: number; annotations: PilotAnnotations; criticism: SourceCriticism[]; inlineQuotes: boolean; start?: number; end?: number }) {
  if (run.type === 'annotation') return null;
  if (run.type === 'virtual_space') return run.source_char_start >= from && run.source_char_start < to ? <>{run.text}</> : null;
  if (run.source_char_end <= from || run.source_char_start >= to) return null;
  if (run.type === 'page') return run.source_char_start >= from && run.source_char_start < to ? <PageRun run={run} /> : null;
  const a = Math.max(from, run.source_char_start), b = Math.min(to, run.source_char_end);
  const cuts = new Set([a, b]);
  for (const r of [...annotations.names, ...annotations.keywords, ...annotations.bold, ...annotations.footnotes, ...criticism, ...(inlineQuotes ? annotations.quotes : [])]) {
    if (overlaps(r, a, b)) { cuts.add(Math.max(a, r.start)); cuts.add(Math.min(b, r.end)); }
  }
  if (start !== undefined && end !== undefined && start < b && end > a) { cuts.add(Math.max(a, start)); cuts.add(Math.min(b, end)); }
  const positions = [...cuts].sort((x, y) => x - y), chars = Array.from(run.text);
  return <>{positions.slice(0, -1).map((left, i) => {
    const right = positions[i + 1];
    const note = annotations.footnotes.find(r => r.start <= left && r.end >= right);
    if (note) return left === note.start ? <span key={left} id={`pilot-note-position-${note.start}`} className="pilot-note-anchor" tabIndex={-1} data-source-start={left} data-source-end={right} aria-label="موضع إشارة حاشية محفوظة في الملحق" /> : null;
    const selected = start !== undefined && end !== undefined && start < right && end > left;
    const strong = covers(annotations.bold, left, right);
    const critique = criticism.find(range => range.start <= left && range.end >= right);
    const className = [covers(annotations.names, left, right) ? 'pilot-person-name' : '', covers(annotations.keywords, left, right) ? 'pilot-keyword' : '', inlineQuotes && covers(annotations.quotes, left, right) ? 'pilot-inline-quote' : ''].filter(Boolean).join(' ');
    const text = chars.slice(left - run.source_char_start, right - run.source_char_start).join('');
    let node: ReactNode = strong ? <strong className="pilot-bold">{text}</strong> : text;
    if (critique) node = <strong className="pilot-criticism" data-criticism-start={critique.start} data-criticism-end={critique.end} data-criticism-kind={critique.kind}>{node}</strong>;
    if (selected) node = <mark data-source-start={left} data-source-end={right}>{node}</mark>;
    return <span key={left} className={className || undefined} data-source-start={left} data-source-end={right}>{node}</span>;
  })}</>;
}

/** Source clusters are indivisible in the editorial vocalization view. Marks
 * remain in the same text node as their base, including at a highlight edge. */
function VocalizedInline(props: InlineProps & { additions: Map<number, AddedDiacritic> }) {
  const { run, from, to, annotations, criticism, inlineQuotes, start, end, additions } = props;
  if (run.type !== 'text') return <Inline {...props} />;
  interface Chunk { start: number; end: number; text: string; className: string; strong: boolean; selected: boolean; critique?: SourceCriticism; note?: SourceRange }
  const chunks: Chunk[] = [];
  for (const cluster of sourceClusters(run.text, run.source_char_start)) {
    if (cluster.start < from || cluster.start >= to) continue;
    const note = annotations.footnotes.find(range => range.start <= cluster.start && range.end > cluster.start);
    const critique = criticism.find(range => range.start <= cluster.start && range.end > cluster.start);
    const chunk: Chunk = {
      start: cluster.start, end: cluster.end, text: clusterText(cluster, additions), note, critique,
      strong: covers(annotations.bold, cluster.start, cluster.start + 1),
      selected: start !== undefined && end !== undefined && start < cluster.end && end > cluster.start,
      className: [covers(annotations.names, cluster.start, cluster.start + 1) ? 'pilot-person-name' : '', covers(annotations.keywords, cluster.start, cluster.start + 1) ? 'pilot-keyword' : '', inlineQuotes && covers(annotations.quotes, cluster.start, cluster.start + 1) ? 'pilot-inline-quote' : ''].filter(Boolean).join(' '),
    };
    const previous = chunks.at(-1);
    if (!note && previous && !previous.note && previous.end === chunk.start && previous.className === chunk.className && previous.strong === chunk.strong && previous.selected === chunk.selected && previous.critique === chunk.critique) {
      previous.text += chunk.text; previous.end = chunk.end;
    } else chunks.push(chunk);
  }
  return <>{chunks.map(chunk => {
    if (chunk.note) return chunk.start === chunk.note.start ? <span key={chunk.start} id={`pilot-note-position-${chunk.note.start}`} className="pilot-note-anchor" tabIndex={-1} data-source-start={chunk.start} data-source-end={chunk.note.end} aria-label="موضع إشارة حاشية محفوظة في الملحق" /> : null;
    let node: ReactNode = chunk.strong ? <strong className="pilot-bold">{chunk.text}</strong> : chunk.text;
    if (chunk.critique) node = <strong className="pilot-criticism" data-criticism-start={chunk.critique.start} data-criticism-end={chunk.critique.end} data-criticism-kind={chunk.critique.kind}>{node}</strong>;
    if (chunk.selected) node = <mark data-source-start={chunk.start} data-source-end={chunk.end}>{node}</mark>;
    return <span key={chunk.start} className={chunk.className || undefined} data-source-start={chunk.start} data-source-end={chunk.end}>{node}</span>;
  })}</>;
}

interface Piece { block: ReadingBlock; start: number; end: number; list?: number; item?: number; quote?: number; poetry?: number }
function makePieces(view: ReadingView, annotations: PilotAnnotations, grouped: boolean): Piece[] {
  return view.blocks.flatMap(block => {
    // A technical-only range can remain in the coverage partition without
    // creating an empty paragraph (for example a trailing list newline).
    if (grouped && !block.runs.some(run => run.type === 'page' || run.type === 'text' && run.text.trim())) return [];
    const a = block.source_char_start, b = block.source_char_end;
    const points = new Set([a, b]);
    // Source-mapped groups are paragraph boundaries in v2. A quote that begins
    // within a connected report decorates its text without cutting that report.
    const ranges = [...(grouped ? [] : annotations.quotes), ...annotations.poetry, ...annotations.lists];
    for (const range of ranges) for (const point of [range.start, range.end]) if (point > a && point < b) points.add(point);
    // Punctuation and conjunctions between authored list items belong to the
    // preceding item. Cutting only at item starts preserves them in one list.
    for (const list of annotations.lists) for (const item of list.items) if (item.start > a && item.start < b) points.add(item.start);
    for (const heading of annotations.headings) if (heading.at > a && heading.at < b) points.add(heading.at);
    const cuts = [...points].sort((x, y) => x - y);
    return cuts.slice(0, -1).map((start, i) => {
      const end = cuts[i + 1], piece: Piece = { block, start, end };
      annotations.lists.some((list, index) => {
        if (list.start > start || list.end < end) return false;
        let item = -1;
        list.items.forEach((range, itemIndex) => { if (range.start <= start) item = itemIndex; });
        if (item >= 0) { piece.list = index; piece.item = item; return true; } return false;
      });
      piece.quote = annotations.quotes.findIndex(range => range.start <= start && range.end >= end);
      piece.poetry = annotations.poetry.findIndex(range => range.start <= start && range.end >= end);
      return piece;
    });
  });
}

export function PilotReader({ presentation, view, start, end, showAddedDiacritics = true, onAddedDiacriticsChange }: { presentation: PilotPresentation; view: ReadingView; start?: number; end?: number; showAddedDiacritics?: boolean; onAddedDiacriticsChange?: (value: boolean) => void }) {
  const [emphasis, setEmphasis] = useState(true);
  const [criticismVisible, setCriticismVisible] = useState(true);
  const [notesOpen, setNotesOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const a = presentation.annotations;
  const grouped = Boolean(presentation.presentation_revision && presentation.paragraph_groups?.length);
  const criticism = grouped ? presentation.criticism ?? NO_CRITICISM : NO_CRITICISM;
  const vocalizationAvailable = usableDiacritics(presentation.diacritics);
  const additions = useMemo(() => diacriticsByBase(vocalizationAvailable && showAddedDiacritics ? presentation.diacritics!.insertions : []), [presentation.diacritics, vocalizationAvailable, showAddedDiacritics]);
  const noteSelected = start !== undefined && end !== undefined && a.footnotes.some(note => overlaps(note, start, end));
  useEffect(() => {
    if (!noteSelected) return;
    setNotesOpen(true);
  }, [noteSelected, start, end]);
  useEffect(() => {
    if (!noteSelected || !notesOpen) return;
    let cancelled = false;
    const scroll = () => void document.fonts.ready.then(() => requestAnimationFrame(() => {
      if (!cancelled) (root.current?.querySelector('.pilot-body mark') ?? root.current?.querySelector('.pilot-footnotes mark'))?.scrollIntoView({ block: 'center', behavior: 'instant' });
    }));
    scroll();
    window.addEventListener('resize', scroll);
    return () => { cancelled = true; window.removeEventListener('resize', scroll); };
  }, [notesOpen, noteSelected, start, end]);
  const pieces = useMemo(() => makePieces(view, a, grouped), [view, a, grouped]);
  const headingNodes = (piece: Piece) => a.headings.filter(heading => heading.at === piece.start).map((heading, i) => <div key={`heading-${piece.start}-${i}`} className="pilot-helper-heading" data-editorial="true"><span>عنوان تنظيمي</span><h3>{heading.label}</h3></div>);
  const paragraph = (piece: Piece, index: number) => {
    const children = piece.block.runs.map((run, i) => vocalizationAvailable
      ? <VocalizedInline key={`${i}-${run.source_char_start}`} run={run} from={piece.start} to={piece.end} annotations={a} criticism={criticism} inlineQuotes={grouped} start={start} end={end} additions={additions} />
      : <Inline key={`${i}-${run.source_char_start}`} run={run} from={piece.start} to={piece.end} annotations={a} criticism={criticism} inlineQuotes={grouped} start={start} end={end} />);
    const props = { 'data-source-start': piece.start, 'data-source-end': piece.end };
    return <Fragment key={`${piece.start}-${index}`}>{headingNodes(piece)}{piece.block.type === 'heading' ? <h2 className={`reading-heading level-${piece.block.level ?? 1}`} {...props}>{children}</h2> : <p {...props}>{children}</p>}</Fragment>;
  };
  const body: ReactNode[] = [];
  for (let i = 0; i < pieces.length;) {
    const first = pieces[i];
    if (first.list !== undefined) {
      const list = a.lists[first.list], items: ReactNode[] = [];
      while (i < pieces.length && pieces[i].list === first.list) {
        const itemIndex = pieces[i].item, itemParts: ReactNode[] = [];
        while (i < pieces.length && pieces[i].list === first.list && pieces[i].item === itemIndex) { itemParts.push(paragraph(pieces[i], i)); i++; }
        items.push(<li key={itemIndex}>{itemParts}</li>);
      }
      body.push(list.ordered ? <ol key={`list-${first.start}`} className="pilot-list">{items}</ol> : <ul key={`list-${first.start}`} className="pilot-list">{items}</ul>);
    } else if ((first.poetry ?? -1) >= 0 || (first.quote ?? -1) >= 0) {
      const poetry = (first.poetry ?? -1) >= 0, index = poetry ? first.poetry : first.quote, parts: ReactNode[] = [];
      while (i < pieces.length && pieces[i].list === undefined && (poetry ? pieces[i].poetry : pieces[i].quote) === index) { parts.push(paragraph(pieces[i], i)); i++; }
      body.push(poetry ? <div key={`poetry-${first.start}`} className="pilot-poetry">{parts}</div> : <blockquote key={`quote-${first.start}`} className="pilot-source-quote">{parts}</blockquote>);
    } else { body.push(paragraph(first, i)); i++; }
  }
  const revealNote = (note: SourceRange) => {
    const target = document.getElementById(`pilot-note-position-${note.start}`);
    target?.focus({ preventScroll: true });
    target?.scrollIntoView({ block: 'center', behavior: 'instant' });
  };
  return <div ref={root} className={`pilot-reader ${grouped ? 'pilot-reading-v2' : ''} ${emphasis ? '' : 'pilot-without-emphasis'} ${criticismVisible ? '' : 'pilot-without-criticism'} ${vocalizationAvailable && showAddedDiacritics ? 'pilot-vocalized' : ''}`} data-testid="pilot-reader">
    <div className="pilot-legend">
      <span><span className="pilot-person-name">الخمري: أسماء أشخاص</span> · <span className="pilot-keyword">البترولي: كلمات محورية</span> · <strong>العريض: إبراز انتقائي</strong></span>
      <label><input type="checkbox" checked={emphasis} onChange={event => setEmphasis(event.target.checked)} />تفعيل الإبراز</label>
      {grouped && <><span className="pilot-criticism-key">الإبراز المظلل: ألفاظ تقويم ونقد واردة في المصدر</span><label><input type="checkbox" checked={criticismVisible} onChange={event => setCriticismVisible(event.target.checked)} />إبراز عبارات التحقيق</label></>}
      {vocalizationAvailable && <label><input type="checkbox" checked={showAddedDiacritics} onChange={event => onAddedDiacriticsChange?.(event.target.checked)} />إظهار الحركات المضافة</label>}
      <small>الألوان للتنظيم؛ لا تثبت الهوية أو التصنيف. العناوين التنظيمية مضافة للعرض.</small>
    </div>
    {vocalizationAvailable && <div className="pilot-diacritics-notice" role="note" aria-label="التشكيل التحريري">
      <p>{presentation.diacritics?.notice ?? 'تشكيل تحريري أضافه الوكيل؛ ليس من ضبط المصدر ولا اعتمادًا بشريًا.'}</p>
      <p>مواضع تحتاج مراجعة: {number(presentation.diacritics?.coverage.uncertain_words ?? 0)} · الكلمات المفحوصة: {number(presentation.diacritics?.coverage.checked_words ?? 0)} من {number(presentation.diacritics?.coverage.total_words ?? 0)}.</p>
      {Boolean(presentation.diacritics?.uncertainties.length) && <details><summary>مواضع التشكيل الملتبسة</summary><ul>{presentation.diacritics?.uncertainties.map((item, i) => <li key={`${item.start}-${i}`}><Link to={`/reader/${presentation.biography_id}?start=${item.start}&end=${item.end}`}>موضع في الأصل <bdi>[{item.start}, {item.end})</bdi></Link> — {item.reason}</li>)}</ul></details>}
    </div>}
    {presentation.diacritics && !vocalizationAvailable && <p className="pilot-diacritics-notice" role="status">تعذر التحقق من طبقة التشكيل المضاف؛ عُرض النص بضبط المصدر دون إضافات.</p>}
    <div className="formatted-text pilot-body" data-testid="formatted-text">{body}</div>
    <details className="pilot-footnotes" open={notesOpen} onToggle={event => setNotesOpen(event.currentTarget.open)}>
      <summary>إشارات الحواشي المحفوظة ({number(a.footnotes.length)})</summary>
      <p className="muted">{presentation.footnote_notice ?? a.footnote_notice ?? 'لم يُعثر على متون الحواشي المقابلة في هذه العينة؛ المحفوظ هنا هو الإشارات الأصلية وسياقها.'}</p>
      <ol>{a.footnotes.map(note => <li key={note.start} data-source-start={note.start} data-source-end={note.end}>
        <span className="pilot-footnote-raw">{start !== undefined && end !== undefined && overlaps(note, start, end) ? <>{Array.from(note.raw).slice(0, Math.max(0, start - note.start)).join('')}<mark data-source-start={Math.max(start, note.start)} data-source-end={Math.min(end, note.end)}>{Array.from(note.raw).slice(Math.max(0, start - note.start), Math.min(note.end - note.start, end - note.start)).join('')}</mark>{Array.from(note.raw).slice(Math.min(note.end - note.start, end - note.start)).join('')}</> : note.raw}</span>
        <span className="pilot-footnote-context">{note.context}</span>
        <button type="button" className="text-link" onClick={() => revealNote(note)}>موضع الإشارة في المتن</button>
      </li>)}</ol>
    </details>
  </div>;
}

export const relationLabels: Record<string, string> = { event: 'موقف', saying: 'قول', description: 'وصف', discussion: 'مناقشة', passing: 'ذكر عابر' };
export const scopeLabels: Record<string, string> = { biography_subject: 'يتصل بصاحب الترجمة', mentioned_person: 'يتصل بشخص مذكور', general_context: 'سياق عام داخل الترجمة' };
export const stanceLabels: Record<string, string> = { reported: 'خبر منقول', negated: 'عبارة منفية', accusation: 'اتهام منقول', contested: 'قول مختلف فيه', criticism: 'نقد رواية' };
export function AuditEvidence({ item }: { item: TopicAudit }) {
  return <div className="pilot-topic-evidence">
    <div className="pilot-audit-meta"><span className={`status ${item.human_status === 'reviewed' ? 'approved' : ''}`}>{item.human_status === 'reviewed' ? (item.agent_decision === 'exclude' ? 'استبعاد معتمد بشريًا' : 'معتمد بشريًا') : item.human_status === 'rejected' ? 'توصية الوكيل مرفوضة بشريًا' : 'فحص نصي بواسطة وكيل — بانتظار اعتمادك'}</span><span>{relationLabels[item.relation_type] ?? item.relation_type} · {scopeLabels[item.subject_scope] ?? item.subject_scope} · {stanceLabels[item.stance] ?? item.stance}</span></div>
    <blockquote dir="rtl">{item.reading_view ? <FormattedText view={item.reading_view} /> : item.evidence_text}</blockquote>
    <p className="small">سبب الربط: {item.rationale}</p>
    {item.context_text && item.context_text !== item.evidence_text && <details className="context-details"><summary>السياق المتصل للشاهد ونقده إن ورد</summary><pre className="original-text">{item.context_text}</pre></details>}
    <Link className="text-link" to={`/reader/${item.biography_id}?start=${item.context_char_start ?? item.source_char_start}&end=${item.context_char_end ?? item.source_char_end}`}>اقرأ الشاهد في سياقه</Link>
  </div>;
}
export function PilotTopicsPanel({ data, biographyId }: { data: PilotTopics; biographyId: number }) {
  if (!data.pilot) return null;
  const topics = data.items.map(topic => ({ ...topic, evidence: topic.evidence.filter(item => item.agent_decision !== 'exclude' && item.human_status !== 'rejected' && (item.agent_decision === 'supported' || item.human_status === 'reviewed')) })).filter(topic => topic.evidence.length > 0);
  return <section className="panel pilot-topics" aria-label="موضوعات الترجمة وشواهدها">
    <div className="panel-title"><h2>موضوعات وردت داخل الترجمة</h2><Link className="text-link" to={`/pilot/reviews?biography_id=${biographyId}`}>مراجعة مقترحات العينة</Link></div>
    <p className="muted small">صلة الموضوع بمقطع لا تعني أنه وصف لصاحب الترجمة. المقترحات النصية مستقلة عن الاعتماد البشري.</p>
    <p className="small">فحص الوكلاء: {number(data.coverage.agent_checked)} من {number(data.coverage.total)} مقطعًا · مراجعة بشرية: {number(data.coverage.human_checked)} · دلالة غير محسومة: {number(data.coverage.uncertain)} · لا موضوع واضح: {number(data.coverage.no_clear_topic)}</p>
    {topics.map(topic => <details key={topic.topic_id} className="pilot-topic-row"><summary>{topic.name} <span className="muted small">{number(topic.evidence.length)} شواهد · {number(topic.evidence.filter(item => item.human_status === 'reviewed').length)} معتمدًا بشريًا</span></summary>{topic.dictionary_status?.includes('proposed') && <p className="small muted">موضوع مقترح يحتاج مراجعة.</p>}{topic.evidence.map(item => <AuditEvidence key={item.id} item={item} />)}</details>)}
    {!topics.length && <p>لم تُنشر مقترحات موضوعية مدعومة لهذه الترجمة بعد.</p>}
  </section>;
}
