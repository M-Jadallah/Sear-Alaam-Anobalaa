import { useEffect, useRef, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Minus,
  Plus,
  BookOpen,
  Copy,
  Check,
} from "lucide-react";
import { useApi, number } from "./api";
import type { List, Row, ReadingView } from "./api";
import { LoadState, Notice, PageTitle, Pagination, Status } from "./components";

import { ManualTag } from "./manual-tag";
import { FormattedText, citationFor } from "./formatted-text";
import { PilotReader, PilotTopicsPanel } from './pilot-reader';
import { usableDiacritics, vocalizedRunText } from './diacritics-text';
import type { PilotPresentation, PilotTopics } from './pilot-types';

export function ReaderPage({ section = false }: { section?: boolean }) {
  const { id } = useParams();
  const [query] = useSearchParams();
  const [fontSize, setFontSize] = useState(23);
  const [showSegments, setShowSegments] = useState(false);
  const content = useRef<HTMLDivElement>(null);
  const [mode, setMode] = useState<"formatted" | "raw">(() => {
    try {
      return localStorage.getItem("siyar-reading-mode-v1") === "raw"
        ? "raw"
        : "formatted";
    } catch {
      return "formatted";
    }
  });
  const [copied, setCopied] = useState("");
  const [showAddedDiacritics, setShowAddedDiacritics] = useState(() => {
    try { return localStorage.getItem('siyar-added-diacritics-v1') !== 'off'; }
    catch { return true; }
  });
  const [copyError, setCopyError] = useState("");
  const [segmentPage, setSegmentPage] = useState(1);
  const [manual, setManual] = useState<Row | null>(null);
  useEffect(() => {
    setSegmentPage(1);
    setShowSegments(false);
    setManual(null);
  }, [id, section]);
  const detail = useApi<Row>(
    `/api/${section ? "sections" : "biographies"}/${id}`,
  );
  const reading = useApi<ReadingView>(
    `/api/${section ? "sections" : "biographies"}/${id}/reading-view`,
  );
  const presentation = useApi<PilotPresentation>(section ? null : `/api/biographies/${id}/presentation`);
  const pilot = presentation.data?.available && presentation.data.status === 'ready' ? presentation.data : undefined;
  const pilotTopics = useApi<PilotTopics>(pilot ? `/api/biographies/${id}/topics` : null);
  const segments = useApi<List>(
    !section && showSegments
      ? `/api/biographies/${id}/segments?page_size=20&page=${segmentPage}`
      : null,
  );
  const data = detail.data;
  const headingBlock = reading.data?.blocks.find(block => block.type === 'heading');
  const addedDiacriticsActive = showAddedDiacritics && usableDiacritics(pilot?.diacritics);
  const displayHeading = mode === 'formatted' && headingBlock
    ? headingBlock.runs.filter(run => run.type === 'text' || run.type === 'virtual_space').map(run => addedDiacriticsActive ? vocalizedRunText(run, pilot!.diacritics!.insertions) : run.text).join('')
    : data?.heading;
  const raw = data?.full_text ?? data?.text_original ?? "";
  const points = Array.from(raw);
  const start = Number(query.get("start"));
  const end = Number(query.get("end"));
  const offset = data?.source_char_start ?? 0;
  const highlighted =
    query.has("start") &&
    Number.isInteger(start) &&
    end > start &&
    start >= offset &&
    end <= offset + points.length;
  useEffect(() => {
    let cancelled = false;
    if (highlighted && data)
      void document.fonts.ready.then(() => {
        if (!cancelled)
          content.current
            ?.querySelector("mark")
            ?.scrollIntoView({ block: "center", behavior: "instant" });
      });
    return () => {
      cancelled = true;
    };
  }, [data, reading.data, presentation.data, mode, highlighted, start, end, showAddedDiacritics]);
  const chooseMode = (value: "formatted" | "raw") => {
    setMode(value);
    try {
      localStorage.setItem("siyar-reading-mode-v1", value);
    } catch {
      /* Reading remains available when storage is restricted. */
    }
  };
  const copy = async (kind: "formatted" | "raw") => {
    try {
      const text =
        kind === "raw"
          ? raw
          : `${(addedDiacriticsActive ? pilot?.vocalized_plain_text : undefined) ?? pilot?.plain_text ?? reading.data?.plain_text ?? ""}\n\n${citationFor(data?.source_id, data?.source_char_start, data?.source_char_end, data?.first_page_marker)}`;
      await navigator.clipboard.writeText(text);
      setCopied(kind);
      setCopyError("");
      setTimeout(() => setCopied(""), 2000);
    } catch {
      setCopyError("تعذر النسخ في هذا المتصفح.");
    }
  };
  const chooseAddedDiacritics = (value: boolean) => {
    setShowAddedDiacritics(value);
    try { localStorage.setItem('siyar-added-diacritics-v1', value ? 'on' : 'off'); }
    catch { /* The in-memory preference still works when storage is blocked. */ }
  };
  useEffect(() => {
    if (!highlighted) window.scrollTo({ top: 0 });
  }, [id, highlighted]);
  return (
    <>
      <Link
        className="text-link back"
        to={section ? "/source" : "/people?view=biographies"}
      >
        {section ? "الأقسام الأصلية" : "فهرس التراجم"}
        <ArrowLeft size={16} />
      </Link>
      <LoadState {...detail} />
      {data && (
        <>
          <PageTitle title={displayHeading?.trim() || data.heading || "النص الأصلي"}>
            قراءة منسّقة مرتبطة بالأصل؛ يمكنك إظهار النص الخام وعلاماته في أي
            وقت.
          </PageTitle>
          {pilot && <p className="pilot-trial-label">{pilot.batch_id && !pilot.batch_id.startsWith('legacy-first-five:') ? `دفعة تحريرية${pilot.batch_label ? `: ${pilot.batch_label}` : ''}` : 'تجربة تنسيق وفهرسة للتراجم الخمس الأولى'} · الترتيب التحريري ومقترحات الموضوعات قيد مراجعتك</p>}
          {presentation.data?.warning && <Notice>{presentation.data.warning}</Notice>}
          <div className="reader-layout">
            <article className="reader-paper">
              <div className="reader-toolbar">
                <span>
                  <BookOpen size={19} />
                  {mode === "formatted" ? "قراءة منسّقة" : "النص الأصلي"}
                </span>
                <div
                  className="reader-modes"
                  role="group"
                  aria-label="وضع القراءة"
                >
                  <button
                    aria-pressed={mode === "formatted"}
                    className={mode === "formatted" ? "selected" : ""}
                    onClick={() => chooseMode("formatted")}
                  >
                    قراءة منسّقة
                  </button>
                  <button
                    aria-pressed={mode === "raw"}
                    className={mode === "raw" ? "selected" : ""}
                    onClick={() => chooseMode("raw")}
                  >
                    النص الأصلي
                  </button>
                </div>
                <div>
                  <button
                    aria-label="تصغير الخط"
                    className="icon-button"
                    disabled={fontSize <= 17}
                    onClick={() => setFontSize((n) => n - 2)}
                  >
                    <Minus size={18} />
                  </button>
                  <span>{number(fontSize)}</span>
                  <button
                    aria-label="تكبير الخط"
                    className="icon-button"
                    disabled={fontSize >= 35}
                    onClick={() => setFontSize((n) => n + 2)}
                  >
                    <Plus size={18} />
                  </button>
                </div>
              </div>
              <div
                ref={content}
                style={{ fontSize }}
                className="reader-content"
              >
                {mode === "formatted" ? (
                  <>
                    <LoadState {...reading} />
                    {reading.data && (pilot ? <PilotReader key={`${id}-${pilot.revision}-${pilot.presentation_revision ?? 'original-pilot'}`} presentation={pilot} view={pilot.reading_view ?? reading.data} start={highlighted ? start : undefined} end={highlighted ? end : undefined} showAddedDiacritics={showAddedDiacritics} onAddedDiacriticsChange={chooseAddedDiacritics} /> : (
                      <FormattedText
                        view={reading.data}
                        start={highlighted ? start : undefined}
                        end={highlighted ? end : undefined}
                      />
                    ))}
                  </>
                ) : (
                  <pre
                    data-testid="original-text"
                    className="original-text"
                    style={{ fontSize }}
                  >
                    {highlighted ? (
                      <>
                        {points.slice(0, start - offset).join("")}
                        <mark>
                          {points.slice(start - offset, end - offset).join("")}
                        </mark>
                        {points.slice(end - offset).join("")}
                      </>
                    ) : (
                      raw
                    )}
                  </pre>
                )}
              </div>
              <div className="reader-copy">
                <button
                  className="button subtle"
                  disabled={!reading.data}
                  onClick={() => void copy("formatted")}
                >
                  {copied === "formatted" ? (
                    <Check size={16} />
                  ) : (
                    <Copy size={16} />
                  )}
                  نسخ النص مع المرجع
                </button>
                <button
                  className="button subtle"
                  onClick={() => void copy("raw")}
                >
                  {copied === "raw" ? <Check size={16} /> : <Copy size={16} />}
                  نسخ الأصل الحرفي
                </button>
                {copyError && (
                  <span role="status" className="form-error">
                    {copyError}
                  </span>
                )}
              </div>
              <div className="reader-pagination">
                {data.previous_id ? (
                  <Link
                    to={`/${section ? "sections" : "reader"}/${data.previous_id}`}
                  >
                    <ArrowRight size={17} />
                    السابق
                  </Link>
                ) : (
                  <span />
                )}
                {data.next_id && (
                  <Link
                    to={`/${section ? "sections" : "reader"}/${data.next_id}`}
                  >
                    التالي
                    <ArrowLeft size={17} />
                  </Link>
                )}
              </div>
            </article>
            <aside className="reader-meta">
              <section className="panel">
                <h2>موضع النص</h2>
                <dl>
                  <dt>معرّف المصدر</dt>
                  <dd>{number(data.source_id)}</dd>
                  <dt>النطاق الأصلي</dt>
                  <dd dir="ltr" className="mono">
                    [{data.source_char_start}, {data.source_char_end})
                  </dd>
                  <dt>علامة الصفحة الخام</dt>
                  <dd>
                    <bdi>
                      {data.first_page_marker ??
                        data.page_marker ??
                        "غير متحققة"}
                    </bdi>
                  </dd>
                  <dt>حالة الاستخراج</dt>
                  <dd>
                    <Status value={data.extraction_status} />
                  </dd>
                </dl>
                {data.subject_person_id && (
                  <Link
                    className="button subtle full"
                    to={`/people/${data.subject_person_id}`}
                  >
                    بطاقة صاحب الترجمة
                    <ArrowLeft size={16} />
                  </Link>
                )}
              </section>
              <Notice>
                علامات الصفحات محفوظة من OpenITI. لا تمثل هنا توثيقًا محققًا
                لصفحات الطبعة.
              </Notice>
              {!section && (
                <button
                  className="button full subtle"
                  onClick={() => setShowSegments((v) => !v)}
                >
                  {showSegments ? "إخفاء المقاطع" : "استعراض المقاطع وحدودها"}
                </button>
              )}
            </aside>
          </div>
          {pilotTopics.data && <PilotTopicsPanel data={pilotTopics.data} biographyId={Number(id)} />}
          {showSegments && (
            <section className="panel source-panel">
              <h2>المقاطع المستخرجة</h2>
              <LoadState {...segments} />
              {segments.data?.items.map((s) => (
                <div key={s.id} className="segment-row">
                  <span>
                    مقطع {number(s.id)} ·{" "}
                    <bdi>
                      [{s.source_char_start}, {s.source_char_end})
                    </bdi>
                  </span>
                  <Status value={s.segmentation_status} />
                  <Link
                    className="text-link"
                    to={`/reader/${id}?start=${s.source_char_start}&end=${s.source_char_end}`}
                  >
                    تظليل المقطع
                  </Link>
                  <button
                    className="button subtle"
                    onClick={() => setManual(s)}
                  >
                    ربط بموضوع
                  </button>
                </div>
              ))}
              <Pagination
                data={segments.data}
                page={segmentPage}
                setPage={setSegmentPage}
              />
              {manual && (
                <ManualTag
                  key={manual.id}
                  segment={manual}
                  done={() => setManual(null)}
                />
              )}
              <Link to="/reviews?kind=segments" className="text-link">
                مراجعة حدود المقاطع
                <ArrowLeft size={16} />
              </Link>
            </section>
          )}
        </>
      )}
    </>
  );
}
