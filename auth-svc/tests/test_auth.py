"""auth-svc testleri: giriş, çerez doğrulama, yönlendirme güvenliği."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

import app.main as auth

client = TestClient(auth.app)
HTML = {"accept": "text/html"}


def _login(c: TestClient, user: str | None = None, pw: str | None = None, nxt="/"):
    user, pw = user or auth.USERNAME, pw or auth.PASSWORD
    return c.post("/auth/login", data={"username": user, "password": pw, "next": nxt}, follow_redirects=False)


def test_verify_without_cookie_is_401():
    assert TestClient(auth.app).get("/auth/verify").status_code == 401


def test_login_success_sets_cookie_and_verifies():
    c = TestClient(auth.app)
    r = _login(c, nxt="/events?x=1")
    assert r.status_code == 303 and r.headers["location"] == "/events?x=1"
    assert "httponly" in r.headers["set-cookie"].lower()
    assert c.get("/auth/verify").status_code == 204


def test_wrong_password_rejected():
    c = TestClient(auth.app)
    r = _login(c, pw="yanlis")
    assert r.status_code == 401 and "hatalı" in r.text
    assert c.get("/auth/verify").status_code == 401


def test_json_login():
    c = TestClient(auth.app)
    assert c.post("/auth/login", json={"username": auth.USERNAME, "password": "x"}).status_code == 401
    r = c.post("/auth/login", json={"username": auth.USERNAME, "password": auth.PASSWORD})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert c.get("/auth/verify").status_code == 204


def test_tampered_and_expired_tokens_rejected():
    tok = auth.make_token(auth.USERNAME)
    assert auth.check_token(tok)
    assert not auth.check_token(tok[:-1] + ("0" if tok[-1] != "0" else "1"))
    assert not auth.check_token(auth.make_token(auth.USERNAME, now=time.time() - auth.SESSION_TTL_S - 1))
    assert not auth.check_token(auth.make_token("baskasi"))
    assert not auth.check_token("garbage")


def test_secure_cookie_behind_https_tunnel():
    r = TestClient(auth.app).post("/auth/login", data={"username": auth.USERNAME, "password": auth.PASSWORD},
                                  headers={"x-forwarded-proto": "https"}, follow_redirects=False)
    assert "secure" in r.headers["set-cookie"].lower()


def test_open_redirect_blocked():
    for bad in ["//evil.com", "https://evil.com", "/\\evil.com", "evil", "/auth/logout"]:
        assert auth.safe_next(bad) == "/"
    assert _login(TestClient(auth.app), nxt="//evil.com").headers["location"] == "/"


def test_unauthorized_redirects_browsers_and_401s_api():
    r = client.get("/auth/unauthorized", headers={**HTML, "x-original-uri": "/dashboard?a=1&b=2"}, follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"] == "/auth/login?next=%2Fdashboard%3Fa%3D1%26b%3D2"
    r = client.get("/auth/unauthorized", headers={"accept": "application/json", "x-original-uri": "/api/events"})
    assert r.status_code == 401


def test_login_page_escapes_next():
    r = client.get("/auth/login", params={"next": '/"><script>alert(1)</script>'})
    assert r.status_code == 200 and "<script>alert" not in r.text


def test_logout_clears_cookie():
    c = TestClient(auth.app)
    _login(c)
    r = c.get("/auth/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/auth/login"
    assert c.get("/auth/verify").status_code == 401


def test_missing_or_weak_credentials_refuse_to_start(monkeypatch):
    monkeypatch.delenv("AUTH_PASSWORD", raising=False)
    with pytest.raises(RuntimeError, match="AUTH_PASSWORD"):
        auth._required("AUTH_PASSWORD", auth.MIN_PASSWORD_LEN)
    monkeypatch.setenv("AUTH_PASSWORD", "kisa")
    with pytest.raises(RuntimeError, match="AUTH_PASSWORD"):
        auth._required("AUTH_PASSWORD", auth.MIN_PASSWORD_LEN)
    monkeypatch.setenv("AUTH_SECRET", "   ")
    with pytest.raises(RuntimeError, match="AUTH_SECRET"):
        auth._required("AUTH_SECRET", auth.MIN_SECRET_LEN)
