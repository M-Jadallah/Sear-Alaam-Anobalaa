import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Search,
  AlertTriangle,
  ChevronLeft,
  ChevronRight,
  Copy,
  Check,
} from "lucide-react";
import type { Row, List } from "./api";
import { number, params } from "./api";
import { FormattedText, citationFor } from "./formatted-text";
import { scopeLabels, stanceLabels } from './pilot-reader';

export function Notice({
  children,
  danger = false,
}: {
  children: React.ReactNode;
  danger?: boolean;
}) {
  return (
    <div
      className={`notice ${danger ? "danger" : ""}`}
      role={danger ? "alert" : "note"}
    >
      <AlertTriangle size={23} />
      <div>{children}</div>
    </div>
  );
}
export function PageTitle({
  title,
  children,
}: {
  title: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="page-title">
      <h1>{title}</h1>
      {children && <p>{children}</p>}
    </header>
  );
}
export function LoadState({
  loading,
  error,
}: {
  loading: boolean;
  error?: string;
}) {
  if (error)
    return (
      <Notice danger>
        {error} تأكد من تشغيل الخادم المحلي ثم أعد تحميل الصفحة.
      </Notice>
    );
  return loading ? (
    <div className="loading" role="status">
      <span />
      جارٍ تحميل النصوص والفهرس…
    </div>
  ) : null;
}
export function Empty({
  children = "لا توجد نتائج في الفهرس المتاح. هذا لا يثبت غيابها عن الكتاب.",
}: {
  children?: React.ReactNode;
}) {
  return (
    <div className="empty">
      <Search size={30} />
      <p>{children}</p>
    </div>
  );
}
export function Status({ value = "candidate" }: { value?: string }) {
  const labels: Record<string, string> = {
    reviewed: "مُراجَع",
    rejected: "مرفوض",
    candidate: "مرشّح غير محقق",
    keyword_candidate: "مطابقة لفظية مرشّحة",
    semantic_candidate: "مطابقة دلالية مرشّحة",
    topic_candidate: "شاهد من الفهرس الموضوعي — مرشّح",
    manual: "إضافة يدوية",
    unreviewed: "هوية غير محققة",
    paragraph: "فقرة غير محققة",
    extraction_candidate: "استخراج مرشّح",
  };
  return (
    <span
      className={`status ${value === "reviewed" ? "approved" : value === "rejected" ? "rejected" : ""}`}
    >
      {labels[value] ?? "استخراج مرشّح"}
    </span>
  );
}
export function SearchForm({
  initial = "",
  scope = "text",
  compact = false,
}: {
  initial?: string;
  scope?: string;
  compact?: boolean;
}) {
  const [query, setQuery] = useState(initial);
  const [area, setArea] = useState(scope);
  const navigate = useNavigate();
  return (
    <form
      className={`search-form ${compact ? "compact" : ""}`}
      onSubmit={(e) => {
        e.preventDefault();
        navigate(`/search?${params({ q: query.trim(), scope: area })}`);
      }}
    >
      <div className="search-line">
        <div className="search-input">
          <Search size={22} />
          <input
            aria-label="عبارة البحث"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="أدخل كلمة، أو اسم عَلَم، أو موضوعًا…"
            maxLength={250}
          />
        </div>
        <button className="primary" type="submit">
          <Search size={20} />
          بحث
        </button>
      </div>
      <label className="scope">
        نطاق البحث
        <select
          value={area}
          onChange={(e) => setArea(e.target.value)}
          aria-label="نطاق البحث"
        >
          <option value="text">النص الأصلي كاملًا</option>
          <option value="titles">عناوين التراجم</option>
          <option value="people">أسماء الأعلام</option>
          <option value="topics">الموضوعات</option>
        </select>
      </label>
      <Link className="text-link describe-link" to="/search?kind=description">
        صف ما تبحث عنه
        <ArrowLeft size={16} />
      </Link>
    </form>
  );
}
export function Pagination({
  data,
  page,
  setPage,
  disabled = false,
}: {
  data?: List;
  page: number;
  setPage: (n: number) => void;
  disabled?: boolean;
}) {
  if (!data || data.total <= data.page_size) return null;
  const pages = Math.ceil(data.total / data.page_size);
  return (
    <nav className="pagination" aria-label="صفحات النتائج">
      <button
        disabled={disabled || page <= 1}
        onClick={() => setPage(page - 1)}
      >
        <ChevronRight size={17} />
        السابق
      </button>
      <span>
        {number(page)} / {number(pages)}
      </span>
      <button
        disabled={disabled || page >= pages}
        onClick={() => setPage(page + 1)}
      >
        التالي
        <ChevronLeft size={17} />
      </button>
    </nav>
  );
}
export function BiographyRows({ items }: { items: Row[] }) {
  return (
    <div className="rows">
      {items.map((item) => (
        <Link className="bio-row" to={`/reader/${item.id}`} key={item.id}>
          <div>
            <h3>{item.heading}</h3>
            <span className="muted small">
              ترجمة {number(item.source_ordinal)}
              {item.first_page_marker && (
                <>
                  {" "}
                  · <bdi>{item.first_page_marker}</bdi>
                </>
              )}
            </span>
          </div>
          <Status value={item.extraction_status} />
          <ChevronLeft className="row-arrow" size={19} />
        </Link>
      ))}
    </div>
  );
}
export function readerLink(item: Row) {
  const audited = item.reader_url?.match(/^\/(reader|sections)\/([1-9]\d*)\?start=(\d+)&end=(\d+)$/);
  if (audited) {
    const start = Number(audited[3]), end = Number(audited[4]);
    const expectedKind = item.biography_id ? 'reader' : 'sections';
    const expectedId = item.biography_id ?? item.section_id ?? item.id;
    if (audited[1] === expectedKind && Number(audited[2]) === expectedId && Number.isSafeInteger(start) && Number.isSafeInteger(end) && end > start) return item.reader_url!;
  }
  return `${item.biography_id ? `/reader/${item.biography_id}` : `/sections/${item.section_id ?? item.id}`}?${params({ start: item.source_char_start, end: item.source_char_end })}`;
}
export function Evidence({
  item,
  children,
}: {
  item: Row;
  children?: React.ReactNode;
}) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const [raw, setRaw] = useState(false);
  const text =
    item.evidence_text ?? item.text_original ?? item.mention_literal ?? "";
  const qualifiedContext = item.topic_audit_id !== undefined && !!(item.context_reading_view || item.context_text);
  return (
    <article className="evidence">
      <div className="evidence-heading">
        <Link to={readerLink(item)}>
          {item.heading ??
            item.canonical_name ??
            item.topic_name ??
            item.name ??
            "موضع في النص"}
        </Link>
        <Status
          value={
            item.review_status ??
            item.identity_review_status ??
            item.segmentation_status ??
            item.match_type
          }
        />
      </div>
      {item.method && (
        <div className="muted small">
          {item.method === "keyword_candidate"
            ? "اقتراح معجمي؛ دلالة النص تحتاج مراجعة."
            : item.method === "semantic_candidate"
              ? "اقتراح دلالي يحتاج مراجعة."
              : item.method === "topic_candidate"
                ? "شاهد موضوعي مفحوص نصيًا؛ راجع حالة اعتماده البشري."
              : "توسيم يدوي"}{" "}
          {item.matched_phrase && <>اللفظ: «{item.matched_phrase}»</>}
        </div>
      )}
      {(item.explanation ?? item.reason) && (
        <p className="result-reason">{item.explanation ?? item.reason}</p>
      )}
      {!!item.retrieval_methods?.length && <div className="pilot-audit-meta" aria-label="طريقة العثور على الشاهد">
        {item.retrieval_methods.map(method => <span key={method} className="status">{method === 'lexical' ? 'استرجاع لفظي' : method === 'semantic' ? 'استرجاع دلالي' : method === 'topic_candidate' ? 'من الفهرس الموضوعي' : method}</span>)}
        {item.topic_audit_id && <span>{item.topic_review_status === 'reviewed' ? 'الوسم معتمد بشريًا' : 'فحص الوكيل النصي — بانتظار اعتمادك'}</span>}
      </div>}
      {item.topic_audit_id !== undefined && (item.subject_scope || ['contested', 'criticism', 'accusation', 'negated'].includes(item.stance ?? '')) && <div className="pilot-audit-meta" aria-label="دلالة الشاهد الموضوعي">
        {item.subject_scope && <span>{scopeLabels[item.subject_scope] ?? item.subject_scope}</span>}
        {['contested', 'criticism', 'accusation', 'negated'].includes(item.stance ?? '') && <span className="status">{stanceLabels[item.stance!]}</span>}
      </div>}
      {item.reading_view && !raw ? (
        <div className="quote formatted-quote">
          <FormattedText view={item.reading_view} />
        </div>
      ) : (
        <pre className="quote">{text}</pre>
      )}
      {item.reading_view && (
        <button
          className="text-link evidence-raw-toggle"
          onClick={() => setRaw((v) => !v)}
        >
          {raw ? "عرض الشاهد المنسّق" : "عرض الشاهد الخام"}
        </button>
      )}
      {qualifiedContext && <details className="context-details qualified-topic-context">
        <summary>السياق الموضوعي المؤهل</summary>
        {item.context_reading_view ? <div className="quote formatted-quote"><FormattedText view={item.context_reading_view} /></div> : <pre className="quote">{item.context_text}</pre>}
      </details>}
      {!qualifiedContext && item.text_original && item.text_original !== text && (
        <details className="context-details">
          <summary>عرض الفقرة الحاضنة كما في المصدر</summary>
          {item.context_reading_view ? (
            <div className="quote formatted-quote">
              <FormattedText view={item.context_reading_view} />
            </div>
          ) : (
            <pre className="quote">{item.text_original}</pre>
          )}
        </details>
      )}
      {!!item.candidates?.length && (
        <p className="small muted">
          هويات مرشّحة لهذا اللفظ:{" "}
          {item.candidates.map((c) => (
            <Link key={c.id} to={`/people/${c.id}`}>
              {c.canonical_name} (هوية {number(c.id)})؛{" "}
            </Link>
          ))}
        </p>
      )}
      <div className="evidence-footer">
        <div className="reference">
          <span>مصدر {number(item.source_id)}</span>
          <span>
            نطاق الشاهد:{" "}
            <bdi>
              [{item.source_char_start}, {item.source_char_end})
            </bdi>
          </span>
          {item.page_marker_crossed ? (
            <span>
              <bdi>{item.first_page_marker ?? "غير محددة"}</bdi> إلى{" "}
              <bdi>{item.last_page_marker ?? "غير محددة"}</bdi> · يعبر نطاق
              صفحة، غير محقق طباعيًا
            </span>
          ) : item.page_marker ? (
            <span>
              <bdi>{item.page_marker}</bdi> · إحالة غير محققة طباعيًا
            </span>
          ) : (
            <span>الصفحة غير محققة</span>
          )}
        </div>
        <div className="actions">
          <Link className="text-link" to={readerLink(item)}>
            اقرأ السياق
            <ArrowLeft size={16} />
          </Link>
          {item.reading_view && (
            <button
              className="icon-button"
              aria-label="نسخ الشاهد المنسق مع المرجع"
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(
                    `${item.reading_view?.plain_text}\n\n${citationFor(item.source_id, item.source_char_start, item.source_char_end, item.page_marker)}`,
                  );
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1800);
                } catch {
                  setCopyError(true);
                }
              }}
            >
              <Copy size={17} />
            </button>
          )}
          {text && (
            <button
              className="icon-button"
              aria-label="نسخ الشاهد"
              title={copyError ? "تعذر النسخ في هذا المتصفح" : "نسخ الشاهد"}
              onClick={async () => {
                try {
                  await navigator.clipboard.writeText(text);
                  setCopied(true);
                  setTimeout(() => setCopied(false), 1800);
                } catch {
                  setCopyError(true);
                }
              }}
            >
              {copied ? <Check size={17} /> : <Copy size={17} />}
            </button>
          )}
        </div>
      </div>
      {children}
    </article>
  );
}
