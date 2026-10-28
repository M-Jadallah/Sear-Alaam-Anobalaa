import { useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ArrowLeft, BookOpen, Search, UsersRound } from "lucide-react";
import { number, params, useApi, useAllTopics } from "./api";
import type { List, Row, Stats } from "./api";
import { SmartSearch } from "./smart-search";
import { AuditEvidence } from './pilot-reader';
import type { TopicAudit } from './pilot-types';
import {
  BiographyRows,
  Empty,
  Evidence,
  LoadState,
  Notice,
  PageTitle,
  Pagination,
  SearchForm,
  Status,
} from "./components";

export function SearchPage() {
  const [query, setQuery] = useSearchParams();
  const description = query.get("kind") === "description";
  return (
    <>
      <div
        className="search-mode-tabs tabs"
        role="group"
        aria-label="نوع البحث"
      >
        <button
          aria-pressed={!description}
          className={!description ? "selected" : ""}
          onClick={() => setQuery(query.get("q") ? { q: query.get("q")! } : {})}
        >
          بحث بالكلمات
        </button>
        <button
          aria-pressed={description}
          className={description ? "selected" : ""}
          onClick={() => setQuery({ kind: "description" })}
        >
          بحث بالوصف
        </button>
      </div>
      {description ? (
        <SmartSearch initial={query.get("q") ?? ""} />
      ) : (
        <KeywordSearchPage />
      )}
    </>
  );
}
function KeywordSearchPage() {
  const [query, setQuery] = useSearchParams();
  const q = query.get("q") ?? "";
  const scope = query.get("scope") ?? "text";
  const page = Number(query.get("page") ?? 1);
  const mode = query.get("mode") ?? "words";
  const result = useApi<List>(
    q ? `/api/search?${params({ q, scope, page, mode })}` : null,
  );
  const update = (values: Record<string, string>) =>
    setQuery({ ...Object.fromEntries(query), ...values });
  return (
    <>
      <PageTitle title="البحث في الكتاب">
        ابحث في الفهرس المحلي؛ التطبيع للبحث فقط، والنص المعروض محفوظ حرفيًا.
      </PageTitle>
      <SearchForm key={`${q}-${scope}`} initial={q} scope={scope} />
      {q ? (
        <>
          <div className="section-heading">
            <h2>
              نتائج «{q}»{" "}
              <span className="muted">
                {result.data && `(${number(result.data.total)})`}
              </span>
            </h2>
            <label className="inline-label">
              طريقة البحث
              <select
                value={mode}
                onChange={(e) => update({ mode: e.target.value, page: "1" })}
              >
                <option value="words">جميع الكلمات</option>
                <option value="phrase">عبارة متصلة</option>
              </select>
            </label>
          </div>
          <LoadState {...result} />
          {result.data?.items.map((row) =>
            scope === "people" ? (
              <Link
                className="directory-row"
                key={row.id}
                to={`/people/${row.id}`}
              >
                <UsersRound />
                <span>{row.canonical_name ?? row.heading ?? row.name}</span>
                <Status value={row.identity_review_status} />
                <ArrowLeft size={18} />
              </Link>
            ) : scope === "topics" ? (
              <Link
                className="directory-row"
                key={row.id}
                to={`/topics/${row.id}`}
              >
                <BookOpen />
                <span>{row.name ?? row.heading}</span>
                <ArrowLeft size={18} />
              </Link>
            ) : (
              <Evidence key={`${row.id}-${row.segment_id}`} item={row} />
            ),
          )}
          {result.data && !result.data.total && <Empty />}
          <Pagination
            data={result.data}
            page={page}
            setPage={(n) => update({ page: String(n) })}
          />
        </>
      ) : (
        <div className="search-guide">
          <Search size={34} />
          <h2>ابدأ بكلمة أو عبارة من النص</h2>
          <p>
            يمكن البحث عن «الإخلاص» و«الاخلاص». اختر نطاقًا أضيق للبحث في
            العناوين أو الأعلام أو الموضوعات.
          </p>
          <p className="muted small">
            لا تُعد نتيجة البحث اعتمادًا علميًا للموضوع أو الهوية.
          </p>
        </div>
      )}
    </>
  );
}
export function PeoplePage() {
  const [query, setQuery] = useSearchParams();
  const q = query.get("q") ?? "";
  const page = Number(query.get("page") ?? 1);
  const view = query.get("view") ?? "people";
  const data = useApi<List>(
    `/api/${view === "biographies" ? "biographies" : "people"}?${params({ q, page })}`,
  );
  const [term, setTerm] = useState(q);
  const update = (values: Record<string, string>) =>
    setQuery({ ...Object.fromEntries(query), ...values });
  return (
    <>
      <PageTitle title="الأعلام والتراجم">
        هوية مستقلة لكل صاحب ترجمة؛ تشابه الاسم لا يعني اتحاد الشخص.
      </PageTitle>
      <div className="toolbar">
        <form
          className="filter-search"
          onSubmit={(e) => {
            e.preventDefault();
            update({ q: term, page: "1" });
          }}
        >
          <Search size={19} />
          <input
            aria-label="ابحث عن عَلَم"
            placeholder="الاسم أو الكنية أو اللقب…"
            value={term}
            onChange={(e) => setTerm(e.target.value)}
          />
          <button type="submit">بحث</button>
        </form>
        <div className="tabs" role="group" aria-label="نوع الفهرس">
          <button
            className={view === "people" ? "selected" : ""}
            onClick={() => update({ view: "people", page: "1" })}
          >
            الأعلام
          </button>
          <button
            className={view === "biographies" ? "selected" : ""}
            onClick={() => update({ view: "biographies", page: "1" })}
          >
            التراجم
          </button>
        </div>
      </div>
      <LoadState {...data} />
      {data.data && (
        <>
          <p className="muted small">
            {number(data.data.total)} سجلًا في الفهرس
          </p>
          <section className="panel directory">
            {view === "biographies" ? (
              <BiographyRows items={data.data.items} />
            ) : (
              data.data.items.map((person) => (
                <Link
                  className="directory-row"
                  to={`/people/${person.id}`}
                  key={person.id}
                >
                  <UsersRound size={22} />
                  <div>
                    <h3>{person.canonical_name}</h3>
                    <span className="muted small">
                      هوية {number(person.id)} · {number(person.mention_count)}{" "}
                      موضع ذكر مرشّح
                    </span>
                  </div>
                  <Status value={person.identity_review_status} />
                  <ArrowLeft size={18} />
                </Link>
              ))
            )}
          </section>
          {!data.data.total && <Empty />}
          <Pagination
            data={data.data}
            page={page}
            setPage={(n) => update({ page: String(n) })}
          />
        </>
      )}
    </>
  );
}
export function PersonPage() {
  const { id } = useParams();
  const [page, setPage] = useState(1);
  const [status, setStatus] = useState("all");
  const detail = useApi<{ person: Row; biographies: Row[]; aliases: Row[] }>(
    `/api/people/${id}`,
  );
  const mentions = useApi<List>(
    `/api/people/${id}/mentions?${params({ page, status })}`,
  );
  return (
    <>
      <Link className="text-link back" to="/people">
        الأعلام والتراجم
        <ArrowLeft size={15} />
      </Link>
      <LoadState {...detail} />
      {detail.data && (
        <>
          <PageTitle title={detail.data.person.canonical_name ?? "بطاقة عَلَم"}>
            هوية رقم {number(detail.data.person.id)} ·{" "}
            <Status value={detail.data.person.identity_review_status} />
          </PageTitle>
          <Notice>
            نسبة الترجمة إلى صاحبها مستخرجة من العنوان وتحتاج تحقيقًا. مواضع
            الذكر اللفظية لا تثبت هوية المذكور حتى مراجعتها.
          </Notice>
          <h2 className="section-heading">الترجمة الأصلية</h2>
          <section className="panel">
            <BiographyRows items={detail.data.biographies} />
          </section>
          {detail.data.aliases.length > 0 && (
            <p className="muted">
              الأسماء المفهرسة:{" "}
              {detail.data.aliases.map((a) => a.alias).join("، ")}
            </p>
          )}
          <div className="section-heading">
            <h2>ذكره في تراجم أخرى</h2>
            <StatusFilter
              value={status}
              change={(v) => {
                setStatus(v);
                setPage(1);
              }}
            />
          </div>
          <LoadState {...mentions} />
          {mentions.data?.items.map((m) => (
            <Evidence key={m.id} item={m} />
          ))}
          {mentions.data && !mentions.data.total && (
            <Empty>
              لا توجد مواضع مستخرجة بهذه الحالة. الاستخراج الآلي لا يضمن الشمول.
            </Empty>
          )}
          <Pagination data={mentions.data} page={page} setPage={setPage} />
        </>
      )}
    </>
  );
}
export function StatusFilter({
  value,
  change,
}: {
  value: string;
  change: (value: string) => void;
}) {
  return (
    <label className="inline-label">
      حالة التدقيق
      <select
        aria-label="حالة التدقيق"
        value={value}
        onChange={(e) => change(e.target.value)}
      >
        <option value="all">جميع الحالات</option>
        <option value="candidate">مرشّح غير محقق</option>
        <option value="reviewed">مُراجَع</option>
        <option value="rejected">مرفوض</option>
      </select>
    </label>
  );
}
export function TopicsPage() {
  const [query, setQuery] = useSearchParams();
  const category = query.get("category") ?? "";
  const [term, setTerm] = useState("");
  const cats = useApi<List>("/api/categories?page_size=100");
  const topics = useAllTopics(params({ category_id: category, q: term }));
  return (
    <>
      <PageTitle title="موضوعات الكتاب">
        المعجم الأساسي: 81 موضوعًا في تسعة أبواب، تضاف إليه مقترحات موثقة من قراءة النص. لكل مطابقة شاهد وحدود وحالة تدقيق.
      </PageTitle>
      <Notice>
        المعجم أداة اقتراح؛ ورود اللفظ لا يكفي لإثبات المعنى. تصنيف الموضوع يخص
        مقطع الدليل وحده.
      </Notice>
      <div className="topic-layout">
        <aside className="category-menu" aria-label="أبواب الموضوعات">
          <button
            className={!category ? "selected" : ""}
            onClick={() => setQuery({})}
          >
            جميع الأبواب
          </button>
          {cats.data?.items.map((c) => (
            <button
              key={c.id}
              className={category === String(c.id) ? "selected" : ""}
              onClick={() => setQuery({ category: String(c.id) })}
            >
              <span>{c.name}</span>
              <span className="count">{number(c.topic_count)}</span>
            </button>
          ))}
        </aside>
        <section>
          <div className="filter-search">
            <Search size={19} />
            <input
              aria-label="تصفية الموضوعات"
              placeholder="ابحث عن موضوع…"
              value={term}
              onChange={(e) => setTerm(e.target.value)}
            />
          </div>
          <LoadState
            loading={topics.loading || cats.loading}
            error={topics.error ?? cats.error}
          />
          <div className="topic-list">
            {topics.data?.items.map((t) => (
              <Link key={t.id} to={`/topics/${t.id}`} className="topic-row">
                <div>
                  <h3>{t.name}</h3>
                  <p>
                    {t.dictionary_status === 'proposed_from_text' ? 'موضوع مقترح من قراءة العينة؛ افتح شواهده وحالة مراجعته.' : <>
                    {number(t.hit_count)} شاهدًا مرشّحًا أو مراجعًا ·{" "}
                    {number(t.reviewed_count)} معتمدًا
                    </>}
                  </p>
                </div>
                <ArrowLeft size={18} />
              </Link>
            ))}
          </div>
          {topics.data && !topics.data.total && (
            <Empty>لا يوجد موضوع مطابق في المعجم.</Empty>
          )}
        </section>
      </div>
    </>
  );
}
export function TopicPage() {
  const { id } = useParams();
  const [status, setStatus] = useState("all");
  const [page, setPage] = useState(1);
  const [pilotPage, setPilotPage] = useState(1);
  const topics = useAllTopics();
  const topic = topics.data?.items.find((t) => String(t.id) === id);
  const evidence = useApi<List>(
    `/api/topics/${id}/evidence?${params({ status, page })}`,
  );
  const pilotEvidence = useApi<Omit<List, 'items'> & { items: TopicAudit[] }>(status === 'rejected' ? null : `/api/pilot/evidence?${params({ topic_id: id, status: status === 'candidate' ? 'pending' : status, page: pilotPage, page_size: 10 })}`);
  return (
    <>
      <Link className="text-link back" to="/topics">
        جميع الموضوعات
        <ArrowLeft size={15} />
      </Link>
      <PageTitle title={topic?.name ?? "شواهد الموضوع"}>
        شواهد حرفية من مقاطع محددة؛ لا يمتد الوسم إلى جميع فقرات الترجمة.
      </PageTitle>
      {topic?.search_terms_csv && (
        <p className="dictionary-terms">
          ألفاظ المعجم: {topic.search_terms_csv}
        </p>
      )}
      <div className="section-heading">
        <h2>
          مقاطع الأدلة{" "}
          {evidence.data && (
            <span className="muted">({number(evidence.data.total)})</span>
          )}
        </h2>
        <StatusFilter
          value={status}
          change={(v) => {
            setStatus(v);
            setPage(1);
            setPilotPage(1);
          }}
        />
      </div>
      <LoadState {...evidence} />
      {evidence.data?.items.map((e) => (
        <Evidence key={e.id} item={e} />
      ))}
      {evidence.data && !evidence.data.total && !pilotEvidence.data?.total && <Empty />}
      <Pagination data={evidence.data} page={page} setPage={setPage} />
      {!!pilotEvidence.data?.total && <section className="panel pilot-topics" aria-label="شواهد الموضوع من العينة">
        <h2>شواهد من الفهرس النصي للعينة</h2>
        <p className="muted small">فحص الوكيل مستقل عن اعتماد الإنسان؛ ورود الموضوع داخل ترجمة لا يجعله صفة لصاحبها.</p>
        {pilotEvidence.data.items.map(item => <AuditEvidence key={item.id} item={item} />)}
        <Pagination data={pilotEvidence.data as unknown as List} page={pilotPage} setPage={setPilotPage} />
      </section>}
    </>
  );
}
function ObjectDetails({ value }: { value: unknown }) {
  let object = value;
  if (typeof value === "string") {
    try {
      object = JSON.parse(value);
    } catch {
      return <p className="break-anywhere">{value}</p>;
    }
  }
  if (!object || typeof object !== "object")
    return <p className="muted">لم تسجل بيانات إضافية.</p>;
  return (
    <dl className="data-list">
      {Object.entries(object).map(([key, v]) => (
        <div key={key}>
          <dt>
            <bdi>{key}</bdi>
          </dt>
          <dd>{typeof v === "object" ? JSON.stringify(v) : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}
export function SourcePage({ stats }: { stats?: Stats }) {
  const [sectionPage, setSectionPage] = useState(1);
  const sections = useApi<List>(
    `/api/sections?page_size=20&page=${sectionPage}`,
  );
  const source = stats?.source;
  return (
    <>
      <PageTitle title="المصدر والمنهج">
        سجل الاستيراد، وحدود التغطية، وأصل كل إحالة.
      </PageTitle>
      <Notice>
        <strong>تنبيه المصدر</strong>
        <p>
          تتضمن نسخة OpenITI ملاحظات على الترقيم في المجلدات الثلاثة الأولى
          وصفحات مفقودة. العلامات الخام لا تُعد إحالات طباعية محققة. راجع{" "}
          <a
            href="https://github.com/OpenITI/0750AH/tree/master/data/0748Dhahabi/0748Dhahabi.SiyarAclamNubala"
            target="_blank"
            rel="noreferrer"
          >
            توثيق النسخة
          </a>
          .
        </p>
      </Notice>
      <section className="panel source-panel">
        <h2>النسخة المستوردة</h2>
        <dl className="data-list">
          <div>
            <dt>المصدر</dt>
            <dd>{source?.title ?? "لا يوجد مصدر مستورد"}</dd>
          </div>
          <div>
            <dt>الملف</dt>
            <dd>
              <bdi>{source?.file_name ?? "data/source.mARkdown"}</bdi>
            </dd>
          </div>
          <div>
            <dt>بصمة SHA-256</dt>
            <dd className="mono break-anywhere" dir="ltr">
              {source?.sha256 ?? "لم يُستورد المصدر"}
            </dd>
          </div>
          <div>
            <dt>مرجع النص</dt>
            <dd>
              <a
                href={
                  source?.source_url ??
                  "https://github.com/OpenITI/0750AH/tree/master/data/0748Dhahabi/0748Dhahabi.SiyarAclamNubala"
                }
                target="_blank"
                rel="noreferrer"
              >
                نسخة OpenITI على GitHub ↗
              </a>
            </dd>
          </div>
          <div>
            <dt>سياسة الصفحات</dt>
            <dd>
              {source?.page_policy ?? "علامات OpenITI خام، غير محققة طباعيًا."}
            </dd>
          </div>
        </dl>
      </section>
      <section className="panel source-panel">
        <h2>التغطية وحالة المراجعة</h2>
        <div className="coverage">
          {[
            ["التراجم", stats?.biographies],
            ["المقاطع", stats?.segments],
            ["المطابقات الموضوعية", stats?.topic_hits],
            ["مواضع ذكر الأعلام", stats?.mentions],
            ["وسوم ومطابقات معلّقة", stats?.pending],
            ["مُراجَع", stats?.reviewed],
            ["مقاطع بنيوية غير مراجعة", stats?.pending_segments],
            ["هويات غير محققة", stats?.pending_people],
          ].map(([label, n]) => (
            <div key={label}>
              <strong>{typeof n === "number" ? number(n) : "—"}</strong>
              <span>{label}</span>
            </div>
          ))}
        </div>
        <ObjectDetails value={source?.coverage} />
      </section>
      <section className="panel source-panel">
        <h2>بيانات النص والطبعة</h2>
        <ObjectDetails value={source?.metadata} />
      </section>
      <section className="panel source-panel">
        <h2>الأقسام خارج التراجم</h2>
        <p className="muted">
          السيرة النبوية وعناوين الأقسام والخواتيم محفوظة وقابلة للبحث؛ لا
          تُعامل تلقائيًا كتراجم أشخاص.
        </p>
        <LoadState {...sections} />
        {sections.data?.items.map((s) => (
          <Link key={s.id} to={`/sections/${s.id}`} className="category-row">
            <span>{s.heading ?? s.name ?? `قسم ${number(s.id)}`}</span>
            <ArrowLeft size={17} />
          </Link>
        ))}
        <Pagination
          data={sections.data}
          page={sectionPage}
          setPage={setSectionPage}
        />
      </section>
      <section className="panel source-panel">
        <h2>قواعد التوثيق والنشر</h2>
        <p>
          النطاقات نصف مفتوحة <bdi>[start, end)</bdi> بوحدات Unicode في الملف
          الأصلي. النص المعروض لم يطبّع؛ التطبيع يخص فهرس البحث. لا تُستخرج
          سنوات الوفاة بالتخمين، ولا تُدمج الأسماء المتشابهة تلقائيًا، ولا تُنسب
          الأقوال إلى المؤلف لمجرد ورودها.
        </p>
        <p>
          الفهرسة اللفظية لا تضمن جميع الموضوعات أو جميع مواضع ذكر الأعلام.
          البحث بالمعنى أداة استرجاع اختيارية ولا يعتمد التصنيفات أو الهويات تلقائيًا.
          تُنسب النسخة الرقمية إلى OpenITI؛ تذكر إرشادات المشروع رخصة{' '}
          <a href="https://creativecommons.org/licenses/by-nc-sa/4.0/" target="_blank" rel="noreferrer">CC BY-NC-SA 4.0</a>.
          {' '}تُراجع حقوق الطبعة والتحقيق مستقلة عن رخصة النسخة الرقمية، وتظل الإضافات التحريرية مميزة عن الأصل.
        </p>
      </section>
    </>
  );
}
