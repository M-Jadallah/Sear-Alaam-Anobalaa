import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Check, X, Save, History } from "lucide-react";
import { useApi, params, postReview, number, useAllTopics } from "./api";
import type { Row, List } from "./api";
import {
  Empty,
  Evidence,
  LoadState,
  Notice,
  PageTitle,
  Pagination,
  Status,
} from "./components";
import { StatusFilter } from "./pages";

function ReviewForm({
  item,
  kind,
  saved,
}: {
  item: Row;
  kind: string;
  saved: () => void;
}) {
  const [reviewer, setReviewer] = useState("");
  const [note, setNote] = useState("");
  const [topic, setTopic] = useState(item.topic_id ?? 0);
  const [person, setPerson] = useState(item.person_id ?? 0);
  const [personQuery, setPersonQuery] = useState("");
  const [start, setStart] = useState(item.source_char_start ?? 0);
  const [end, setEnd] = useState(item.source_char_end ?? 0);
  const [type, setType] = useState(item.segment_type ?? "paragraph");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const topics = useAllTopics(kind === 'topics' ? '' : null);
  const people = useApi<List>(
    kind === "mentions" && personQuery.length >= 2
      ? `/api/people?${params({ q: personQuery, page_size: 12 })}`
      : null,
  );
  const submit = async (status: string) => {
    if (!reviewer.trim()) {
      setError("أدخل اسم المراجع لتوثيق القرار.");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const body: Record<string, unknown> = {
        reviewer: reviewer.trim(),
        note,
        review_status: status,
      };
      if (kind === "topics") {
        body.topic_id = topic;
        body.source_char_start = start;
        body.source_char_end = end;
      } else if (kind === "mentions") {
        body.person_id = person || null;
      } else {
        delete body.review_status;
        body.source_char_start = start;
        body.source_char_end = end;
        body.segment_type = type;
      }
      await postReview(
        `/api/reviews/${kind === "topics" ? "topic-hits" : kind}/${item.id}`,
        body,
      );
      saved();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="review-form">
      <div className="form-grid">
        <label>
          اسم المراجع
          <input
            aria-label="اسم المراجع"
            value={reviewer}
            onChange={(e) => setReviewer(e.target.value)}
            placeholder="اسمك في سجل التدقيق"
            maxLength={100}
          />
        </label>
        {kind === "topics" && (
          <label>
            الموضوع
            <select
              aria-label="الموضوع"
              value={topic}
              onChange={(e) => setTopic(Number(e.target.value))}
            >
              {topics.data?.items.map((t) => (
                <option value={t.id} key={t.id}>
                  {t.name}
                </option>
              ))}
            </select>
          </label>
        )}
        {kind === "mentions" && (
          <label>
            البحث عن الهوية
            <input
              aria-label="البحث عن الهوية"
              value={personQuery}
              onChange={(e) => setPersonQuery(e.target.value)}
              placeholder="حرفان على الأقل…"
            />
          </label>
        )}
        {kind === "segments" && (
          <label>
            نوع المقطع
            <select value={type} onChange={(e) => setType(e.target.value)}>
              <option value="paragraph">فقرة</option>
              <option value="saying">قول</option>
              <option value="story">خبر أو موقف</option>
              <option value="unverified_excerpt">مقتطف غير محقق</option>
            </select>
          </label>
        )}
        {kind !== "mentions" && (
          <>
            <label>
              بداية الشاهد في المصدر
              <input
                type="number"
                aria-label="بداية الشاهد"
                min={0}
                value={start}
                onChange={(e) => setStart(Number(e.target.value))}
              />
            </label>
            <label>
              نهاية الشاهد في المصدر
              <input
                type="number"
                aria-label="نهاية الشاهد"
                min={start + 1}
                value={end}
                onChange={(e) => setEnd(Number(e.target.value))}
              />
            </label>
          </>
        )}
        {kind === "mentions" && (
          <label className="full-field">
            الهوية المطابقة
            <select
              aria-label="الهوية المطابقة"
              value={person}
              onChange={(e) => setPerson(Number(e.target.value))}
            >
              <option value={0}>دون هوية محسومة</option>
              {person > 0 &&
                !people.data?.items.some((p) => p.id === person) && (
                  <option value={person}>
                    الهوية الحالية رقم {number(person)}
                  </option>
                )}
              {(people.data?.items ?? item.candidates ?? []).map((p) => (
                <option value={p.id} key={p.id}>
                  {p.canonical_name} — هوية {number(p.id)}
                </option>
              ))}
            </select>
            <span className="small muted">
              تحقق من السياق قبل مطابقة الاسم. لا تُدمج هويتان بهذا الإجراء.
            </span>
          </label>
        )}
        <label className="full-field">
          ملاحظة المراجعة
          <textarea
            aria-label="ملاحظة المراجعة"
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="سبب الاعتماد أو الرفض، أو وصف تعديل الحدود…"
            maxLength={2000}
          />
        </label>
      </div>
      {error && (
        <p role="alert" className="form-error">
          {error}
        </p>
      )}
      <div className="review-actions">
        {kind === "segments" ? (
          <button
            className="primary"
            disabled={busy}
            onClick={() => submit("reviewed")}
          >
            <Save size={17} />
            حفظ حدود المقطع
          </button>
        ) : (
          <>
            <button
              className="primary"
              disabled={busy}
              onClick={() => submit("reviewed")}
            >
              <Check size={17} />
              اعتماد بعد المراجعة
            </button>
            <button
              className="button reject"
              disabled={busy}
              onClick={() => submit("rejected")}
            >
              <X size={17} />
              رفض المطابقة
            </button>
            <button
              className="button subtle"
              disabled={busy}
              onClick={() => submit("candidate")}
            >
              <Save size={17} />
              حفظ كمرشّح
            </button>
          </>
        )}
      </div>
      <p className="muted small">
        تُسجّل التعديلات باسم المراجع ووقتها. يُستخرج الشاهد من الأصل ضمن النطاق
        الجديد؛ لا يُكتب نص بديل.
      </p>
    </div>
  );
}
function HistoryEntry({ row }: { row: Row }) {
  type ChangeState = {
    review_status?: string;
    identity_review_status?: string;
    segmentation_status?: string;
  };
  let change: { before?: ChangeState; after?: ChangeState; note?: string } = {};
  try {
    change = JSON.parse(row.change_summary ?? "{}");
  } catch {
    /* Legacy summaries remain readable below. */
  }
  const status = (value?: ChangeState) =>
    value?.review_status ??
    value?.identity_review_status ??
    value?.segmentation_status;
  const names: Record<string, string> = {
    topic_hit: "وسم موضوع",
    person_mention: "مطابقة عَلَم",
    segment: "حدود مقطع",
  };
  return (
    <div className="log-entry">
      <strong>{row.reviewer}</strong>
      <bdi>{row.changed_at} UTC</bdi>
      <p>
        {names[row.entity_type ?? ""] ?? row.entity_type} ·{" "}
        {number(row.entity_id)}{" "}
        {change.before && <Status value={status(change.before)} />} ←{" "}
        {change.after && <Status value={status(change.after)} />}
      </p>
      {change.note && <p>{change.note}</p>}
      <details className="context-details">
        <summary>تفاصيل التعديل قبل القرار وبعده</summary>
        <pre className="audit-json" dir="auto">
          {row.change_summary}
        </pre>
      </details>
    </div>
  );
}

export function ReviewsPage() {
  const [query, setQuery] = useSearchParams();
  const kind = query.get("kind") ?? "topics";
  const [status, setStatus] = useState("candidate");
  const [page, setPage] = useState(1);
  const [revision, setRevision] = useState(0);
  const [opened, setOpened] = useState<number | null>(null);
  const [message, setMessage] = useState("");
  const [log, setLog] = useState(false);
  const data = useApi<List>(
    `/api/reviews?${params({ kind, status, page, page_size: 10 })}`,
    revision,
  );
  const history = useApi<List>(
    log ? "/api/review-log?page_size=20" : null,
    revision,
  );
  return (
    <>
      <PageTitle title="مراجعة الفهرسة">
        قرارات بشرية موثّقة، مع بقاء النص المرجعي محفوظًا.
      </PageTitle>
      <Link className="button subtle" to="/pilot/reviews">مراجعة موضوعات التراجم الخمس التجريبية</Link>
      <Notice>
        اقرأ السياق قبل الاعتماد. تراجع الوسوم وحدود الأدلة وهويات المذكورين
        بصورة مستقلة. الخبر أو القول لا يُعد محققًا لمجرد استخراجه آليًا.
      </Notice>
      <div className="toolbar">
        <div className="tabs" role="group" aria-label="نوع المراجعة">
          {[
            ["topics", "وسوم الموضوعات"],
            ["mentions", "مطابقات الأعلام"],
            ["segments", "حدود المقاطع"],
          ].map(([key, label]) => (
            <button
              key={key}
              className={kind === key ? "selected" : ""}
              onClick={() => {
                setQuery({ kind: key });
                setPage(1);
                setOpened(null);
              }}
            >
              {label}
            </button>
          ))}
        </div>
        <StatusFilter
          value={status}
          change={(v) => {
            setStatus(v);
            setPage(1);
          }}
        />
      </div>
      {message && (
        <p className="success" role="status">
          <Check size={18} />
          {message}
        </p>
      )}
      <LoadState {...data} />
      {data.data && (
        <p className="muted small">
          {number(data.data.total)} عنصرًا بهذه الحالة
        </p>
      )}
      {data.data?.items.map((item) => (
        <Evidence key={item.id} item={item}>
          <div className="review-toggle">
            <button
              className="button subtle"
              onClick={() => setOpened((v) => (v === item.id ? null : item.id))}
            >
              {opened === item.id ? "إغلاق المراجعة" : "مراجعة هذا الموضع"}
            </button>
            {item.biography_id && (
              <Link
                className="text-link"
                to={`/reader/${item.biography_id}?start=${item.source_char_start}&end=${item.source_char_end}`}
              >
                فتح النص الأصلي
              </Link>
            )}
          </div>
          {opened === item.id && (
            <ReviewForm
              key={`${item.id}-${kind}-${revision}`}
              item={item}
              kind={kind}
              saved={() => {
                setRevision((n) => n + 1);
                setOpened(null);
                setMessage("حُفظ القرار في سجل التدقيق.");
              }}
            />
          )}
        </Evidence>
      ))}
      {data.data && !data.data.total && (
        <Empty>لا توجد عناصر بهذه الحالة في قائمة المراجعة.</Empty>
      )}
      <Pagination data={data.data} page={page} setPage={setPage} />
      <button className="button subtle" onClick={() => setLog((v) => !v)}>
        <History size={18} />
        {log ? "إخفاء سجل التدقيق" : "عرض سجل التدقيق"}
      </button>
      {log && (
        <section className="panel source-panel">
          <h2>سجل التعديلات الأخيرة</h2>
          <LoadState {...history} />
          {history.data?.items.map((row) => (
            <HistoryEntry key={row.id} row={row} />
          ))}
          {history.data && !history.data.total && (
            <Empty>لم تُسجّل مراجعات بعد.</Empty>
          )}
        </section>
      )}
    </>
  );
}
