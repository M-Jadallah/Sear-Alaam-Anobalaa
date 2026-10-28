import { useEffect, useState } from "react";
import { Link, NavLink, Route, Routes, useLocation } from "react-router-dom";
import {
  BookOpen,
  House,
  Search,
  UsersRound,
  LibraryBig,
  ClipboardCheck,
  FileText,
  Monitor,
  Menu,
  X,
  ArrowLeft,
} from "lucide-react";
import { useApi, number } from "./api";
import type { Stats, List } from "./api";
import {
  BiographyRows,
  LoadState,
  Notice,
  PageTitle,
  SearchForm,
} from "./components";
import {
  SearchPage,
  PeoplePage,
  PersonPage,
  TopicsPage,
  TopicPage,
  SourcePage,
} from "./pages";
import { ReaderPage } from "./reader";
import { ReviewsPage } from "./reviews";
import { PilotReviewsPage } from './pilot-reviews';
import { AdminAccess, AdminRoute, AuthProvider, LoginPage, useAuth } from './auth';

const navigation = [
  ["/", "الرئيسية", House],
  ["/search", "البحث في الكتاب", Search],
  ["/people", "الأعلام والتراجم", UsersRound],
  ["/topics", "الموضوعات", LibraryBig],
  ["/reviews", "مراجعة الفهرسة", ClipboardCheck],
  ["/source", "المصدر والمنهج", FileText],
] as const;
function Home({ stats }: { stats?: Stats }) {
  const biographies = useApi<List>("/api/biographies?page_size=3");
  const categories = useApi<List>("/api/categories?page_size=3");
  return (
    <>
      <div className="home-intro">
        <PageTitle title="ابحث في سير أعلام النبلاء">
          النص الأصلي، وشواهده، وإحالاته في مكان واحد.
        </PageTitle>
        <SearchForm />
      </div>
      <div className="stats-strip">
        {[
          ["عناوين التراجم", stats?.biographies],
          ["موضوعًا مقترحًا", stats?.topics],
          ["أبواب موضوعية", stats?.categories],
          ["نتائج معتمدة", stats?.reviewed],
        ].map(([label, count]) => (
          <div key={label}>
            <span>{label}</span>
            <strong>{typeof count === "number" ? number(count) : "—"}</strong>
          </div>
        ))}
      </div>
      <div className="home-columns">
        <section className="panel">
          <div className="panel-title">
            <h2>
              <UsersRound size={23} />
              تصفّح التراجم
            </h2>
            <Link to="/people" className="text-link">
              الفهرس الكامل
              <ArrowLeft size={16} />
            </Link>
          </div>
          <LoadState {...biographies} />
          {biographies.data && <BiographyRows items={biographies.data.items} />}
        </section>
        <section className="panel">
          <div className="panel-title">
            <h2>
              <BookOpen size={23} />
              موضوعات الكتاب
            </h2>
          </div>
          <LoadState {...categories} />
          {categories.data?.items.slice(0, 3).map((c) => (
            <Link
              key={c.id}
              className="category-row"
              to={`/topics?category=${c.id}`}
            >
              <span>{c.name}</span>
              <ArrowLeft size={18} />
            </Link>
          ))}
          <Link className="button full subtle" to="/topics">
            عرض جميع الموضوعات
            <ArrowLeft size={17} />
          </Link>
        </section>
      </div>
      <Notice>
        <strong>الفهرسة الآلية تحتاج مراجعة</strong>
        <p>
          المطابقات اللفظية اقتراحات معجمية. راجع الشاهد وسياقه قبل اعتماد
          دلالته أو هوية الشخص المذكور.
        </p>
      </Notice>
    </>
  );
}
export default function App() {
  return <AuthProvider><AppShell /></AuthProvider>;
}
function AppShell() {
  const { session } = useAuth();
  const location = useLocation();
  const [menu, setMenu] = useState(false);
  const [revision, setRevision] = useState(0);
  const stats = useApi<Stats>("/api/stats", revision);
  useEffect(() => {
    const update = () => setRevision((n) => n + 1);
    window.addEventListener("siyar-review", update);
    return () => window.removeEventListener("siyar-review", update);
  }, []);
  const title =
    (location.pathname.startsWith('/pilot/') ? 'مراجعة العينة' : undefined) ?? navigation.find(
      ([path]) => path !== "/" && location.pathname.startsWith(path),
    )?.[1] ??
    (location.pathname.startsWith("/reader") ||
    location.pathname.startsWith("/sections")
      ? "قارئ النص الأصلي"
      : "الرئيسية");
  return (
    <div className="app-shell">
      <a href="#main" className="skip-link">
        انتقل إلى المحتوى
      </a>
      <aside className={`sidebar ${menu ? "open" : ""}`}>
        <Link to="/" className="brand" onClick={() => setMenu(false)}>
          <BookOpen size={54} strokeWidth={1.25} />
          <span>
            موسوعة
            <br />
            سير أعلام النبلاء
          </span>
        </Link>
        <button
          className="mobile-close icon-button"
          aria-label="إغلاق القائمة"
          onClick={() => setMenu(false)}
        >
          <X />
        </button>
        <nav aria-label="التنقل الرئيسي">
          {navigation.filter(([path]) => path !== '/reviews' || session?.authenticated).map(([path, label, Icon]) => (
            <NavLink
              key={path}
              to={path}
              end={path === "/"}
              onClick={() => setMenu(false)}
            >
              <Icon size={21} />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <AdminAccess />
          <p>
            <BookOpen size={20} />
            النص الأصلي أولًا
          </p>
          <span>
            <i />
            نسخة محلية · بلا خدمات مدفوعة
          </span>
        </div>
      </aside>
      {menu && (
        <button
          className="scrim"
          aria-label="إغلاق القائمة"
          onClick={() => setMenu(false)}
        />
      )}
      <div className="workspace">
        <div className="topbar">
          <div>
            <button
              className="mobile-menu icon-button"
              aria-label="فتح القائمة"
              onClick={() => setMenu(true)}
            >
              <Menu />
            </button>
            <House size={18} />
            <Link to="/">الرئيسية</Link>
            {title !== "الرئيسية" && (
              <>
                <span className="muted">/</span>
                <span>{title}</span>
              </>
            )}
          </div>
          <span className="local">
            <i />
            نسخة محلية
            <Monitor size={18} />
          </span>
        </div>
        <main id="main" tabIndex={-1}>
          <LoadState loading={false} error={stats.error} />
          {stats.data?.source_missing && (
            <Notice danger>
              <strong>
                {stats.data.source_file_exists
                  ? "النص موجود ولم يُستورد بعد."
                  : "النص المرجعي غير موجود."}
              </strong>
              <p>
                اختر ملف OpenITI وضعه في <bdi>data/source.mARkdown</bdi>، ثم
                نفّذ أمر الاستيراد الموضّح في README. لا تُعرض بيانات مصطنعة من
                الكتاب.
              </p>
            </Notice>
          )}
          {(stats.data?.source?.is_synthetic === 1 ||
            stats.data?.source?.is_synthetic === true) && (
            <Notice>
              <strong>عينة مصطنعة للاختبار — ليست من الكتاب</strong>
              <p>
                هذه البيئة تعرض نصوص اختبار فقط. لا تُنسب تراجمها أو شواهدها إلى
                سير أعلام النبلاء.
              </p>
            </Notice>
          )}
          <Routes>
            <Route path="/" element={<Home stats={stats.data} />} />
            <Route path="/search" element={<SearchPage />} />
            <Route path="/people" element={<PeoplePage />} />
            <Route path="/people/:id" element={<PersonPage />} />
            <Route path="/topics" element={<TopicsPage />} />
            <Route path="/topics/:id" element={<TopicPage />} />
            <Route path="/reader/:id" element={<ReaderPage />} />
            <Route path="/sections/:id" element={<ReaderPage section />} />
            <Route path="/login" element={<LoginPage />} />
            <Route path="/reviews" element={<AdminRoute><ReviewsPage /></AdminRoute>} />
            <Route path="/pilot/reviews" element={<AdminRoute><PilotReviewsPage /></AdminRoute>} />
            <Route path="/source" element={<SourcePage stats={stats.data} />} />
            <Route
              path="*"
              element={
                <>
                  <PageTitle title="الصفحة غير موجودة" />
                  <Link className="button primary" to="/">
                    العودة للرئيسية
                  </Link>
                </>
              }
            />
          </Routes>
          <footer className="site-footer">
            <span>موسوعة سير أعلام النبلاء</span>
            <Link to="/source">المصدر وحدود الفهرسة</Link>
            <span>كل شاهد يعود إلى موضعه في الأصل.</span>
          </footer>
        </main>
      </div>
    </div>
  );
}
