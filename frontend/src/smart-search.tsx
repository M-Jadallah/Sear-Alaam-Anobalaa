import { useEffect, useRef, useState } from "react";
import {
  ArrowLeft,
  Check,
  RefreshCw,
  Search,
  SlidersHorizontal,
  Sparkles,
  X,
} from "lucide-react";
import { number, postApi, useApi, useAllTopics } from "./api";
import type { List, Row } from "./api";
import { Empty, Evidence, Notice, PageTitle, Pagination } from "./components";
import { useCurationBatches } from './curation-batches';

interface Interpretation {
  id: string;
  label: string;
  meaning: string;
  query_text?: string;
  include_terms: string[];
  expansion_terms?: string[];
  exclude_terms: string[];
  topic_ids: number[];
  topic_mode?: 'boost' | 'filter';
  reviewed_only?: boolean;
  pilot_only?: boolean;
  batch_id?: string;
  uncertainty?: string | string[];
}
interface InterpretationResponse {
  query: string;
  interpretations: Interpretation[];
  note?: string;
  method: string;
  uncertainty_flags?: string[];
}
interface SmartResult extends List {
  mode: "hybrid" | "lexical_only";
  warning?: string;
  reason?: string;
  elapsed_ms?: number;
  cache_hit?: boolean;
  interpretation?: Interpretation;
  ranking?: {
    lexical_candidates: number;
    semantic_candidates: number;
    topic_candidates?: number;
    reranked: number;
  };
}
interface SemanticStatus {
  status?: string;
  state?: string;
  ready?: boolean;
  message?: string;
  note?: string;
  warning?: string;
  reason?: string;
  paragraph_status?: {
    status?: string;
    ready?: boolean;
    reason?: string;
  };
  [key: string]: unknown;
}
const terms = (text: string) => [
  ...new Set(
    text
      .split(/[,،\n]+/)
      .map((t) => t.trim())
      .filter(Boolean),
  ),
];

export function SmartSearch({ initial = "" }: { initial?: string }) {
  const [q, setQ] = useState(initial);
  const [interpretation, setInterpretation] =
    useState<InterpretationResponse>();
  const [selected, setSelected] = useState<Interpretation>();
  const [result, setResult] = useState<SmartResult>();
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState<"interpret" | "search" | null>(null);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const [expansionText, setExpansionText] = useState("");
  const [includeText, setIncludeText] = useState("");
  const [excludeText, setExcludeText] = useState("");
  const request = useRef<AbortController | null>(null);
  const resultsRef = useRef<HTMLDivElement>(null);
  const status = useApi<SemanticStatus>("/api/semantic/status", revision);
  const topics = useAllTopics();
  const batches = useCurationBatches();
  useEffect(() => () => request.current?.abort(), []);
  const ready =
    status.data?.ready === true ||
    status.data?.status === "ready" ||
    status.data?.state === "ready";
  useEffect(() => {
    if (
      status.data?.status !== 'building' &&
      status.data?.paragraph_status?.status !== 'building'
    ) return;
    const timer = setTimeout(() => setRevision(value => value + 1), 15000);
    return () => clearTimeout(timer);
  }, [status.data]);
  const changeQuery = (value: string) => {
    request.current?.abort();
    setQ(value);
    setInterpretation(undefined);
    setSelected(undefined);
    setResult(undefined);
    setError("");
    setBusy(null);
  };
  const edit = (values: Partial<Interpretation>) => {
    request.current?.abort();
    setBusy(null);
    setSelected((current) => (current ? { ...current, ...values } : current));
    setResult(undefined);
    setPage(1);
  };
  const analyze = async () => {
    if (!q.trim()) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy("interpret");
    setError("");
    setResult(undefined);
    setSelected(undefined);
    setInterpretation(undefined);
    try {
      const response = await postApi<InterpretationResponse>(
        "/api/search/interpret",
        { q: q.trim() },
        controller.signal,
      );
      if (controller.signal.aborted || request.current !== controller) return;
      setInterpretation(response);
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      if (request.current === controller) setBusy(null);
    }
  };
  const search = async (nextPage = 1) => {
    if (!selected || busy) return;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setBusy("search");
    setError("");
    try {
      const response = await postApi<SmartResult>(
        "/api/search/smart",
        {
          q: q.trim(),
          plan: {
            id: selected.id,
            meaning: selected.meaning,
            expansion_terms: terms(expansionText),
            include_terms: terms(includeText),
            exclude_terms: terms(excludeText),
            topic_ids: selected.topic_ids,
            topic_mode: selected.topic_mode ?? 'boost',
            reviewed_only: selected.reviewed_only ?? false,
            pilot_only: selected.pilot_only ?? false,
            ...(selected.batch_id ? { batch_id: selected.batch_id } : {}),
          },
          page: nextPage,
          page_size: 10,
        },
        controller.signal,
      );
      if (controller.signal.aborted || request.current !== controller) return;
      setResult(response);
      setPage(nextPage);
      setTimeout(
        () =>
          resultsRef.current?.scrollIntoView({
            block: "start",
            behavior: "instant",
          }),
        0,
      );
    } catch (e) {
      if ((e as Error).name !== "AbortError") setError((e as Error).message);
    } finally {
      if (request.current === controller) setBusy(null);
    }
  };
  const uncertainties = selected?.uncertainty
    ? Array.isArray(selected.uncertainty)
      ? selected.uncertainty
      : [selected.uncertainty]
    : [];
  return (
    <>
      <PageTitle title="صف ما تريد البحث عنه">
        اختر المعنى الأقرب إلى قصدك أولًا، ثم استكشف مقاطعه في النص الأصلي.
      </PageTitle>
      <div className="semantic-health">
        <span>
          <i className={ready ? "" : "inactive"} />
          {ready ? "الفهرس الدلالي المحلي جاهز" : "وضع البحث المحلي"}
        </span>
        <button
          className="icon-button"
          aria-label="تحديث حالة الفهرس"
          onClick={() => setRevision((n) => n + 1)}
        >
          <RefreshCw size={16} />
        </button>
      </div>
      {ready && status.data?.paragraph_status?.status === 'building' && (
        <Notice>
          البحث في السياق متاح الآن، ويجري إعداد تغطية إضافية للشواهد القصيرة.
        </Notice>
      )}
      {status.data && !ready && (
        <Notice>
          <strong>البحث الدلالي غير جاهز</strong>
          <p>
            {status.data.message ??
              status.data.warning ??
              status.data.reason ??
              status.data.note ??
              "يظل تحليل الوصف والتوسيع اللفظي متاحين؛ تهيئة النماذج والفهرس موضحة في README."}
          </p>
        </Notice>
      )}
      {status.error && (
        <Notice>
          تعذر قراءة حالة الفهرس. يمكنك محاولة البحث؛ ستظهر طريقة التنفيذ
          الفعلية مع النتائج.
        </Notice>
      )}
      <form
        className="description-form"
        onSubmit={(e) => {
          e.preventDefault();
          void analyze();
        }}
      >
        <label htmlFor="description-query">وصف ما تبحث عنه</label>
        <textarea
          id="description-query"
          aria-label="صف ما تبحث عنه"
          value={q}
          onChange={(e) => changeQuery(e.target.value)}
          maxLength={1000}
          placeholder="مثلًا: أريد مواقف امتنع فيها عالم عن مال أو منصب حفاظًا على دينه…"
          rows={4}
        />
        <div className="description-actions">
          <span className="muted small">
            {number(Array.from(q).length)} / {number(1000)} · لا تظهر المقاطع
            قبل اختيار المعنى.
          </span>
          <button
            className="primary"
            type="submit"
            disabled={!!busy || !q.trim()}
          >
            <Sparkles size={18} />
            اقترح المعاني
          </button>
        </div>
      </form>
      {busy === "interpret" && (
        <div className="loading" role="status">
          <span />
          جارٍ اقتراح المعاني من الوصف…
        </div>
      )}
      {error && <Notice danger>{error}</Notice>}
      {interpretation && (
        <section className="interpretations" aria-label="المعاني المقترحة">
          <div className="section-heading">
            <h2>أي معنى تقصد؟</h2>
            <span className="muted small">اقتراحات مبدئية قابلة للتعديل</span>
          </div>
          {interpretation.note && (
            <p className="muted small">{interpretation.note}</p>
          )}
          {!!interpretation.uncertainty_flags?.length && (
            <Notice>
              {interpretation.uncertainty_flags.map((flag, i) => (
                <p key={i}>{flag}</p>
              ))}
            </Notice>
          )}
          <div className="interpretation-list">
            {interpretation.interpretations.map((item, index) => (
              <article
                className={`interpretation-card ${selected?.id === item.id ? "chosen" : ""}`}
                key={item.id}
              >
                <div className="interpretation-heading">
                  <span className="interpretation-number">
                    {number(index + 1)}
                  </span>
                  <h3>{item.label}</h3>
                  {selected?.id === item.id && <Check size={20} />}
                </div>
                <p>{item.meaning}</p>
                {item.topic_ids.length > 0 && (
                  <div className="concept-chips">
                    {item.topic_ids.map((id) => (
                      <span key={id}>
                        {topics.data?.items.find((t) => t.id === id)?.name ??
                          `موضوع ${number(id)}`}
                      </span>
                    ))}
                  </div>
                )}
                <button
                  type="button"
                  className={`button ${selected?.id === item.id ? "primary" : "subtle"}`}
                  aria-pressed={selected?.id === item.id}
                  onClick={() => {
                    request.current?.abort();
                    setBusy(null);
                    setSelected({
                      ...item,
                      include_terms: [...item.include_terms],
                      exclude_terms: [...item.exclude_terms],
                      topic_ids: [...item.topic_ids],
                    });
                    setExpansionText((item.expansion_terms ?? []).join("، "));
                    setIncludeText(item.include_terms.join("، "));
                    setExcludeText(item.exclude_terms.join("، "));
                    setResult(undefined);
                    setError("");
                  }}
                >
                  اختيار هذا المعنى
                  <ArrowLeft size={17} />
                </button>
              </article>
            ))}
          </div>
        </section>
      )}
      {selected && (
        <form
          className="meaning-editor panel"
          onSubmit={(e) => {
            e.preventDefault();
            void search();
          }}
        >
          <h2>
            <SlidersHorizontal size={22} />
            اضبط المعنى قبل البحث
          </h2>
          <div className="form-grid">
            <label className="full-field">
              الوصف المعتمد
              <textarea
                aria-label="الوصف المعتمد"
                value={selected.meaning}
                onChange={(e) => edit({ meaning: e.target.value })}
                maxLength={1000}
              />
            </label>
            <label className="full-field">
              ألفاظ مساعدة للتوسيع
              <input
                aria-label="ألفاظ مساعدة للتوسيع"
                value={expansionText}
                onChange={(e) => {
                  setExpansionText(e.target.value);
                  edit({ expansion_terms: terms(e.target.value) });
                }}
                placeholder="مرادفات أو ألفاظ قريبة من قصدك، تفصلها فاصلة"
              />
            </label>
            <label>
              كلمات مطلوبة
              <input
                aria-label="كلمات مطلوبة"
                value={includeText}
                onChange={(e) => {
                  setIncludeText(e.target.value);
                  edit({ include_terms: terms(e.target.value) });
                }}
                placeholder="افصل بين العبارات بفاصلة"
              />
            </label>
            <label>
              كلمات مستبعدة
              <input
                aria-label="كلمات مستبعدة"
                value={excludeText}
                onChange={(e) => {
                  setExcludeText(e.target.value);
                  edit({ exclude_terms: terms(e.target.value) });
                }}
                placeholder="كلمات لا تريد ورودها في المقطع"
              />
            </label>
            <label className="full-field">
              المفاهيم المساعدة
              <select
                aria-label="أضف مفهومًا"
                value=""
                onChange={(e) => {
                  const id = Number(e.target.value);
                  if (id && !selected.topic_ids.includes(id))
                    edit({ topic_ids: [...selected.topic_ids, id] });
                }}
              >
                <option value="">إضافة مفهوم من المعجم (اختياري)</option>
                {topics.data?.items.map((t: Row) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              استخدام الموضوعات المختارة
              <select aria-label="استخدام الموضوعات المختارة" value={selected.topic_mode ?? 'boost'} onChange={event => edit({ topic_mode: event.target.value as 'boost' | 'filter' })}>
                <option value="boost">ترجيح النتائج بالموضوعات</option>
                <option value="filter">حصر النتائج في الموضوعات المختارة</option>
              </select>
            </label>
            <div className="pilot-search-options">
              <label><input type="checkbox" checked={selected.pilot_only ?? false} onChange={event => edit({ pilot_only: event.target.checked, ...(event.target.checked ? { batch_id: undefined } : {}) })} />البحث في التراجم الخمس التجريبية فقط</label>
              <label><input type="checkbox" checked={selected.reviewed_only ?? false} onChange={event => edit({ reviewed_only: event.target.checked })} />شواهد الموضوعات المعتمدة بشريًا فقط</label>
            </div>
            {batches.items.length > 0 && <label>حصر البحث في دفعة تحريرية<select aria-label="دفعة البحث" value={selected.batch_id ?? ''} onChange={event => edit({ batch_id: event.target.value || undefined, ...(event.target.value ? { pilot_only: false } : {}) })}><option value="">دون حصر بدفعة تحريرية</option>{batches.items.map(batch => <option key={batch.id} value={batch.id}>{batch.label}{batch.legacy ? ' · العينة الأولى' : ''}</option>)}</select></label>}
          </div>
          <div className="concept-chips editable">
            {selected.topic_ids.map((id) => (
              <button
                type="button"
                key={id}
                aria-label={`إزالة مفهوم ${topics.data?.items.find((t) => t.id === id)?.name ?? id}`}
                onClick={() =>
                  edit({
                    topic_ids: selected.topic_ids.filter((t) => t !== id),
                  })
                }
              >
                {topics.data?.items.find((t) => t.id === id)?.name ??
                  `موضوع ${number(id)}`}
                <X size={13} />
              </button>
            ))}
          </div>
          {uncertainties.length > 0 && (
            <Notice>
              {uncertainties.map((text, i) => (
                <p key={i}>{text}</p>
              ))}
            </Notice>
          )}
          <p className="muted small">
            الموضوعات ترجّح الاسترجاع افتراضيًا؛ يمكنك حصر النتائج فيها صراحة. المقترحات المفحوصة بواسطة وكيل تظهر بحالتها ولا تثبت التصنيف. الكلمات المطلوبة
            والمستبعدة شروط لفظية؛ راجع السياق للتأكد من الأفعال والنفي وهوية
            المذكور.
          </p>
          <button
            className="primary"
            disabled={!!busy || !selected.meaning.trim()}
            type="submit"
          >
            <Search size={18} />
            ابحث بهذا المعنى
          </button>
        </form>
      )}
      {busy === "search" && (
        <div className="loading" role="status">
          <span />
          <div>جارٍ البحث وتقييم المقاطع المحلية…<small className="first-load-note">قد يستغرق أول طلب وقتًا إضافيًا لتحميل النماذج المحلية.</small></div>
        </div>
      )}
      {result && (
        <div className="smart-results" ref={resultsRef}>
          <div className="section-heading">
            <h2>المقاطع المرشّحة للمعنى المختار</h2>
            <span className="status">
              {result.mode === "hybrid" ? "بحث لفظي ودلالي" : "نتائج لفظية فقط"}
            </span>
          </div>
          {result.warning && <Notice>{result.warning}</Notice>}
          <p className="muted small">
            {number(result.total)} نتيجة مرشّحة متاحة للعرض، ضمن أفضل المرشحين؛
            لا يمثل العدد جميع مواضع المعنى في الكتاب.
          </p>
          {result.items.map((row) => (
            <Evidence
              key={`${row.source_id}-${row.id}-${row.source_char_start}`}
              item={row}
            />
          ))}
          {!result.total && (
            <Empty>
              لم تظهر مقاطع مناسبة في المرشحين الحاليين. جرّب تعديل الوصف أو
              تخفيف الشروط؛ هذا لا يثبت غياب المعنى عن الكتاب.
            </Empty>
          )}
          <Pagination
            data={result}
            page={page}
            disabled={!!busy}
            setPage={(n) => {
              void search(n);
            }}
          />
          <details className="context-details">
            <summary>كيف تُرتّب هذه النتائج؟</summary>
            <p>
              {result.mode === "hybrid"
                ? "يجمع البحث المطابقات اللفظية وتقارب المعنى، ثم يعيد ترتيب المقاطع وفق صلتها بالوصف المختار."
                : "استُخدم البحث اللفظي المحلي فقط؛ لم يُنفذ بحث دلالي."}{" "}
              درجة الصلة للترتيب ولا تمثل نسبة صحة علمية. لا يعتمد البحث الوسوم
              أو الهويات تلقائيًا.
            </p>
          </details>
        </div>
      )}
    </>
  );
}
