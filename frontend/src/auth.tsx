import { createContext, useContext, useEffect, useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { Link, Navigate, useLocation, useSearchParams } from 'react-router-dom';

type Session = { mode: 'local' | 'production'; authenticated: boolean; username?: string; csrf?: string };
type AuthState = { session?: Session; error: string; refresh: () => Promise<void> };
const AuthContext = createContext<AuthState>({ error: '', refresh: async () => {} });
export const useAuth = () => useContext(AuthContext);

export async function authHeaders(): Promise<Record<string, string>> {
  const response = await fetch('/api/auth/me', { cache: 'no-store' });
  if (!response.ok) throw new Error('تعذر التحقق من جلسة الدخول.');
  const session: Session = await response.json();
  return session.csrf ? { 'X-CSRF-Token': session.csrf } : {};
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session>();
  const [error, setError] = useState('');
  const refresh = async () => {
    try {
      const response = await fetch('/api/auth/me', { cache: 'no-store' });
      if (!response.ok) throw new Error('تعذر التحقق من جلسة الدخول.');
      setSession(await response.json()); setError('');
    } catch (failure) { setError((failure as Error).message); }
  };
  useEffect(() => { void refresh(); }, []);
  return <AuthContext.Provider value={{ session, error, refresh }}>{children}</AuthContext.Provider>;
}

export function AdminRoute({ children }: { children: ReactNode }) {
  const { session, error } = useAuth();
  const location = useLocation();
  if (error) return <p role="alert">{error}</p>;
  if (!session) return <p role="status">جارٍ التحقق من الدخول…</p>;
  return session.authenticated ? <>{children}</> : <Navigate replace to={`/login?next=${encodeURIComponent(location.pathname)}`} />;
}

export function LoginPage() {
  const { session, refresh } = useAuth();
  const [query] = useSearchParams();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const next = query.get('next');
  const destination = next && (next === '/pilot/reviews' || /^\/reader\/[1-9][0-9]*$/.test(next)) ? next : '/reviews';
  if (session?.authenticated) return <Navigate replace to={destination} />;
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError('');
    const form = new FormData(event.currentTarget);
    try {
      const response = await fetch('/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username: form.get('username'), password: form.get('password') }) });
      const result = await response.json();
      if (!response.ok) throw new Error(result.detail || 'تعذر تسجيل الدخول.');
      await refresh();
    } catch (failure) { setError((failure as Error).message); }
    finally { setBusy(false); }
  }
  return <section className="auth-panel">
    <h1>دخول المدير</h1>
    <p>سجّل الدخول لمراجعة الشواهد وتوثيق التعديلات باسمك.</p>
    <form onSubmit={submit} className="auth-form">
      <label htmlFor="admin-username">اسم المستخدم</label>
      <input id="admin-username" name="username" autoComplete="username" required maxLength={100} dir="auto" />
      <label htmlFor="admin-password">كلمة المرور</label>
      <input id="admin-password" name="password" type="password" autoComplete="current-password" required maxLength={1024} />
      {error && <p role="alert" className="auth-error">{error}</p>}
      <button className="button primary" disabled={busy} type="submit">{busy ? 'جارٍ الدخول…' : 'تسجيل الدخول'}</button>
    </form>
    <Link to="/">العودة للقراءة والبحث</Link>
  </section>;
}

export function AdminAccess() {
  const { session, refresh } = useAuth();
  const [error, setError] = useState('');
  if (!session || session.mode === 'local') return null;
  if (!session.authenticated) return <Link className="button" to="/login">دخول المدير</Link>;
  async function logout() {
    try {
      const response = await fetch('/api/auth/logout', { method: 'POST', headers: await authHeaders() });
      if (!response.ok) throw new Error('تعذر تسجيل الخروج.');
      await refresh();
    } catch (failure) { setError((failure as Error).message); }
  }
  return <div><span>{session.username}</span> <button className="button" onClick={() => void logout()}>تسجيل الخروج</button>{error && <p role="alert">{error}</p>}</div>;
}
