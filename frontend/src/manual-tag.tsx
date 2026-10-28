import { useState } from "react";
import { useAllTopics, postReview, number } from "./api";
import type { Row } from "./api";
import { LoadState } from "./components";
import { useAuth } from './auth';

export function ManualTag({
  segment,
  done,
}: {
  segment: Row;
  done: () => void;
}) {
  const topics = useAllTopics();
  const [topic, setTopic] = useState(0);
  const [start, setStart] = useState(segment.source_char_start ?? 0);
  const [end, setEnd] = useState(segment.source_char_end ?? 0);
  const { session } = useAuth();
  const isAdmin = session?.mode === 'production';
  const [reviewer, setReviewer] = useState(isAdmin ? session.username ?? '' : '');
  const [note, setNote] = useState("");
  const [status, setStatus] = useState("candidate");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const submit = async () => {
    if (!topic || !reviewer.trim()) {
      setError("اختر الموضوع وأدخل اسم المراجع.");
      return;
    }
    setSaving(true);
    setError("");
    try {
      await postReview(`/api/segments/${segment.id}/topic-hits`, {
        topic_id: topic,
        source_char_start: start,
        source_char_end: end,
        review_status: status,
        reviewer: reviewer.trim(),
        note,
      });
      done();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setSaving(false);
    }
  };
  return (
    <section className="panel source-panel manual-tag">
      <h2>ربط المقطع {number(segment.id)} بموضوع</h2>
      <p>
        حدد نطاق الشاهد داخل هذا المقطع. يُؤخذ النص من المصدر مباشرة دون تحرير
        حروفه.
      </p>
      <LoadState {...topics} />
      <pre className="quote">{segment.text_original}</pre>
      <div className="form-grid">
        <label>
          الموضوع
          <select
            aria-label="موضوع الوسم اليدوي"
            value={topic}
            onChange={(e) => setTopic(Number(e.target.value))}
          >
            <option value={0}>اختر موضوعًا</option>
            {topics.data?.items.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          اسم المراجع
          <input
            aria-label="مراجع الوسم اليدوي"
            value={reviewer}
            readOnly={isAdmin}
            onChange={(e) => setReviewer(e.target.value)}
            maxLength={100}
          />
        </label>
        <label>
          بداية الشاهد
          <input
            type="number"
            aria-label="بداية الوسم اليدوي"
            value={start}
            onChange={(e) => setStart(Number(e.target.value))}
          />
        </label>
        <label>
          نهاية الشاهد
          <input
            type="number"
            aria-label="نهاية الوسم اليدوي"
            value={end}
            onChange={(e) => setEnd(Number(e.target.value))}
          />
        </label>
        <label>
          حالة الوسم
          <select value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="candidate">حفظ كمرشّح</option>
            <option value="reviewed">اعتماد بعد مراجعة السياق</option>
          </select>
        </label>
        <label>
          ملاحظة
          <input
            value={note}
            onChange={(e) => setNote(e.target.value)}
            maxLength={2000}
          />
        </label>
      </div>
      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      <div className="review-actions">
        <button className="primary" disabled={saving} onClick={submit}>
          حفظ ربط الموضوع
        </button>
        <button className="button subtle" onClick={done}>
          إغلاق
        </button>
      </div>
    </section>
  );
}
