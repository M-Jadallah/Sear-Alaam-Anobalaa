"""Single-administrator sessions. Production credentials never enter the book DB."""
from contextvars import ContextVar
from contextlib import closing
from dataclasses import dataclass
import hashlib
import hmac
import os
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

ACTOR = ContextVar("review_actor", default=None)
COOKIE = "__Host-siyar_session"
PUBLIC_POSTS = {"/api/search/interpret", "/api/search/smart"}


def reviewer_name(fallback):
    return ACTOR.get() or fallback


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600_000).hex()


@dataclass(frozen=True)
class AuthSettings:
    production: bool
    origin: str
    database: Path

    @classmethod
    def from_environment(cls, library):
        mode = os.environ.get("SIYAR_MODE", "local")
        if mode not in ("local", "production"):
            raise ValueError("SIYAR_MODE must be local or production")
        origin = os.environ.get("SIYAR_PUBLIC_ORIGIN", "https://sear-alaam-anobalaa.jadallah.work").rstrip("/")
        parsed = urlsplit(origin)
        if parsed.scheme != "https" or not parsed.hostname or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("SIYAR_PUBLIC_ORIGIN must be an HTTPS origin")
        return cls(mode == "production", origin, Path(os.environ.get("SIYAR_AUTH_DB", Path(library).parent / "auth.sqlite3")))


class Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=1024)


class Auth:
    def __init__(self, settings):
        self.settings = settings
        if not settings.production:
            return
        username = os.environ.get("SIYAR_ADMIN_USERNAME", "").strip()
        password = os.environ.get("SIYAR_ADMIN_PASSWORD", "")
        if not username or len(username) > 100 or len(password) < 14 or len(password) > 1024:
            raise ValueError("اضبط اسم المدير وكلمة مرور من 14 إلى 1024 حرفًا في متغيرات Coolify.")
        settings.database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS administrators(username TEXT PRIMARY KEY,salt TEXT NOT NULL, password_hash TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS sessions(token_hash TEXT PRIMARY KEY,username TEXT NOT NULL,csrf TEXT NOT NULL,expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS login_attempts(peer TEXT NOT NULL,created REAL NOT NULL);
            """)
            current = db.execute("SELECT * FROM administrators WHERE username=?", (username,)).fetchone()
            if not current or not hmac.compare_digest(password_hash(password, current["salt"]), current["password_hash"]):
                salt = secrets.token_hex(16)
                db.execute("DELETE FROM administrators")
                db.execute("DELETE FROM sessions")
                db.execute("INSERT INTO administrators VALUES (?,?,?)", (username, salt, password_hash(password, salt)))

    def connect(self):
        db = sqlite3.connect(self.settings.database, timeout=15)
        db.row_factory = sqlite3.Row
        return db

    def session(self, request):
        raw = request.cookies.get(COOKIE, "")
        if not raw or len(raw) > 200:
            return None
        with closing(self.connect()) as db:
            row = db.execute("SELECT username,csrf,expires FROM sessions WHERE token_hash=? AND expires>?", (digest(raw), time.time())).fetchone()
            return dict(row) if row else None

    def install(self, app):
        settings = self.settings

        @app.middleware("http")
        async def access(request: Request, call_next):
            path = request.url.path.rstrip("/") or "/"
            write = request.method not in ("GET", "HEAD", "OPTIONS")
            if not settings.production:
                if write:
                    local = {"localhost", "127.0.0.1", "::1", "testserver"}
                    origin = request.headers.get("origin")
                    if request.url.hostname not in local or (origin and urlsplit(origin).hostname not in local) or request.headers.get("sec-fetch-site") == "cross-site":
                        return JSONResponse({"detail": "التعديلات متاحة من الواجهة المحلية فقط."}, status_code=403)
                return await call_next(request)
            if request.url.hostname != urlsplit(settings.origin).hostname:
                return JSONResponse({"detail": "المضيف غير مسموح."}, status_code=400)
            if write and (request.headers.get("origin") != settings.origin or request.headers.get("sec-fetch-site") == "cross-site"):
                return JSONResponse({"detail": "مصدر الطلب غير مسموح."}, status_code=403)
            admin_read = path in {"/api/reviews", "/api/review-log", "/api/pilot/reviews", "/api/pilot/coverage"}
            protected = admin_read or (write and path not in PUBLIC_POSTS and path != "/api/auth/login")
            current = self.session(request) if protected else None
            if protected and not current:
                return JSONResponse({"detail": "يلزم تسجيل دخول المدير."}, status_code=401)
            if protected and write and not hmac.compare_digest(request.headers.get("x-csrf-token", ""), current["csrf"]):
                return JSONResponse({"detail": "رمز حماية الطلب غير صالح؛ أعد تسجيل الدخول."}, status_code=403)
            token = ACTOR.set(current["username"] if current else None)
            try:
                response = await call_next(request)
                if path.startswith("/api/auth/") or protected:
                    response.headers["Cache-Control"] = "no-store"
                return response
            finally:
                ACTOR.reset(token)

        router = APIRouter(prefix="/api/auth")

        @router.get("/me")
        def me(request: Request):
            if not settings.production:
                return {"mode": "local", "authenticated": True, "username": "مراجع محلي", "csrf": ""}
            current = self.session(request)
            return {"mode": "production", "authenticated": bool(current), "username": current["username"] if current else None, "csrf": current["csrf"] if current else None}

        @router.post("/login")
        def login(payload: Credentials, request: Request):
            if not settings.production:
                raise HTTPException(404, "الدخول الإداري خاص بوضع الإنتاج.")
            peer = request.client.host if request.client else "unknown"
            now = time.time()
            with closing(self.connect()) as db, db:
                db.execute("DELETE FROM login_attempts WHERE created<?", (now - 300,))
                db.execute("DELETE FROM sessions WHERE expires<=?", (now,))
                if db.execute("SELECT COUNT(*) FROM login_attempts WHERE peer=?", (peer,)).fetchone()[0] >= 5:
                    raise HTTPException(429, "محاولات كثيرة؛ انتظر خمس دقائق.", headers={"Retry-After": "300"})
                db.execute("INSERT INTO login_attempts VALUES (?,?)", (peer, now))
                row = db.execute("SELECT * FROM administrators WHERE username=?", (payload.username,)).fetchone()
                salt = row["salt"] if row else "0" * 32
                valid = hmac.compare_digest(password_hash(payload.password, salt), row["password_hash"] if row else "0" * 64)
                if not row or not valid:
                    # Commit the failed attempt before returning; exceptions roll transactions back.
                    db.commit()
                    raise HTTPException(401, "بيانات الدخول غير صحيحة.")
                db.execute("DELETE FROM login_attempts WHERE peer=?", (peer,))
                raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                db.execute("INSERT INTO sessions VALUES (?,?,?,?)", (digest(raw), row["username"], csrf, now + 8 * 3600))
            response = JSONResponse({"authenticated": True, "username": row["username"], "csrf": csrf})
            response.set_cookie(COOKIE, raw, max_age=8 * 3600, secure=True, httponly=True, samesite="strict", path="/")
            return response

        @router.post("/logout")
        def logout(request: Request):
            if settings.production:
                with closing(self.connect()) as db, db:
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(request.cookies.get(COOKIE, "")),))
            response = JSONResponse({"authenticated": False})
            response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict", path="/")
            return response

        app.include_router(router)
