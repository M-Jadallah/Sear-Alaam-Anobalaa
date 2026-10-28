"""Production auth is exercised against the explicitly synthetic disposable book."""
from contextlib import closing
import sqlite3

from fastapi.testclient import TestClient
import pytest

ORIGIN = "https://sear-alaam-anobalaa.jadallah.work"
PASSWORD = "synthetic-test-password-only"


@pytest.fixture
def production(book, monkeypatch):
    from backend.app import create_app
    monkeypatch.setenv("SIYAR_MODE", "production")
    monkeypatch.setenv("SIYAR_ADMIN_USERNAME", "test-admin")
    monkeypatch.setenv("SIYAR_ADMIN_PASSWORD", PASSWORD)
    monkeypatch.setenv("SIYAR_AUTH_DB", str(book["database"].parent / "auth.sqlite3"))
    with TestClient(create_app(book["database"], book["source"]), base_url=ORIGIN, headers={"Origin": ORIGIN}) as client:
        yield client


def login(client):
    response = client.post("/api/auth/login", json={"username": "test-admin", "password": PASSWORD})
    assert response.status_code == 200
    assert all(value in response.headers["set-cookie"].lower() for value in ["secure", "httponly", "samesite=strict"])
    return {"X-CSRF-Token": response.json()["csrf"]}


def test_public_search_and_private_review_endpoints(production):
    assert production.get("/api/search", params={"q": "الإخلاص"}).status_code == 200
    assert production.post("/api/search/interpret", json={"q": "الإخلاص"}).status_code == 200
    for path in ["/api/reviews", "/api/review-log", "/api/pilot/reviews", "/api/pilot/coverage"]:
        assert production.get(path).status_code == 401
    assert production.post("/api/reviews/topic-hits/1", json={"review_status": "reviewed"}).status_code == 401
    assert production.get("/api/auth/me").json()["authenticated"] is False


def test_csrf_logout_and_server_assigned_reviewer(production, book):
    headers = login(production)
    payload = {"review_status": "reviewed", "reviewer": "untrusted-other-name"}
    assert production.post("/api/reviews/topic-hits/1", json=payload).status_code == 403
    assert production.post("/api/reviews/topic-hits/1", headers={**headers, "Origin": "https://attacker.invalid"}, json=payload).status_code == 403
    assert production.post("/api/reviews/topic-hits/1", headers=headers, json=payload).status_code == 200
    with closing(sqlite3.connect(book["database"])) as db:
        assert db.execute("SELECT reviewer FROM review_log ORDER BY id DESC LIMIT 1").fetchone()[0] == "test-admin"
    assert production.get("/api/auth/me").headers["cache-control"] == "no-store"
    assert production.post("/api/auth/logout", headers=headers).status_code == 200
    assert production.get("/api/reviews").status_code == 401


def test_bad_password_attempts_are_persisted_and_limited(production):
    for _ in range(5):
        assert production.post("/api/auth/login", json={"username": "test-admin", "password": "wrong"}).status_code == 401
    response = production.post("/api/auth/login", json={"username": "test-admin", "password": PASSWORD})
    assert response.status_code == 429
    assert response.headers["retry-after"] == "300"


def test_host_spoof_and_expired_session_are_rejected(production, book):
    assert production.get("/api/live", headers={"Host": "attacker.invalid"}).status_code == 400
    login(production)
    with closing(sqlite3.connect(book["database"].parent / "auth.sqlite3")) as db, db:
        db.execute("UPDATE sessions SET expires=0")
    assert production.get("/api/reviews").status_code == 401


def test_password_rotation_revokes_sessions(production, book, monkeypatch):
    from backend.auth import Auth, AuthSettings
    login(production)
    monkeypatch.setenv("SIYAR_ADMIN_PASSWORD", "rotated-synthetic-password-only")
    Auth(AuthSettings.from_environment(book["database"]))
    assert production.get("/api/reviews").status_code == 401
