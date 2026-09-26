"""auth-svc — dış erişim (cloudflared tüneli) için oturum kapısı.

auth-proxy (nginx) her isteği önce `GET /auth/verify` ile sorar (auth_request); 204 dönmezse istek
web-ui/gateway'e HİÇ iletilmez. Oturum durumsuzdur: HMAC-SHA256 imzalı, süreli, HttpOnly çerez.

Uçlar:
  GET  /auth/verify        204 (geçerli oturum) | 401             — yalnızca nginx iç alt isteği
  GET  /auth/unauthorized  302 → /auth/login?next=… (tarayıcı) | 401 JSON (API istemcisi)
  GET  /auth/login         giriş formu
  POST /auth/login         form (username, password, next) veya JSON → çerez + 303 / 200 JSON
  GET|POST /auth/logout    çerezi siler → /auth/login
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import logging
import os
import time
from urllib.parse import parse_qs, quote

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

log = logging.getLogger("auth-svc")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))

MIN_PASSWORD_LEN = 12
MIN_SECRET_LEN = 32


def _required(name: str, min_len: int = 1) -> str:
    """Kimlik bilgisi KODDA VARSAYILAN TUTULMAZ: env yoksa/kısaysa servis açılmaz (sessizce bilinen bir şifreyle çalışmak yerine)."""
    value = os.getenv(name, "").strip()
    if len(value) < min_len:
        raise RuntimeError(
            f"{name} tanımlı değil veya {min_len} karakterden kısa — .env dosyasına ekleyin "
            f"(üret: python -c 'import secrets; print(secrets.token_urlsafe(24))')"
        )
    return value


USERNAME = _required("AUTH_USERNAME")
PASSWORD = _required("AUTH_PASSWORD", MIN_PASSWORD_LEN)
# Çerez imza anahtarı da zorunlu: rastgele üretilseydi her yeniden başlatmada tüm oturumlar düşerdi
SECRET = _required("AUTH_SECRET", MIN_SECRET_LEN).encode()
SESSION_TTL_S = int(float(os.getenv("AUTH_SESSION_HOURS", "12")) * 3600)
COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "uskoruma_session")

app = FastAPI(title="uskoruma auth-svc", docs_url=None, redoc_url=None, openapi_url=None)


# --- oturum çerezi -----------------------------------------------------------------------------------
def _sign(payload: bytes) -> str:
    return hmac.new(SECRET, payload, hashlib.sha256).hexdigest()


def make_token(user: str, now: float | None = None) -> str:
    exp = int((now if now is not None else time.time()) + SESSION_TTL_S)
    payload = base64.urlsafe_b64encode(json.dumps({"u": user, "exp": exp}).encode()).decode().rstrip("=")
    return f"{payload}.{_sign(payload.encode())}"


def check_token(token: str | None, now: float | None = None) -> bool:
    if not token or "." not in token:
        return False
    payload, sig = token.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload.encode())):
        return False
    try:
        data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return data.get("u") == USERNAME and int(data["exp"]) > (now if now is not None else time.time())
    except (ValueError, KeyError, TypeError):
        return False


def credentials_ok(user: str, pw: str) -> bool:
    # İki karşılaştırma da her zaman yapılır (kısa devre yok → zamanlama sızıntısı yok)
    u = hmac.compare_digest(user.encode(), USERNAME.encode())
    p = hmac.compare_digest(pw.encode(), PASSWORD.encode())
    return u and p


def safe_next(nxt: str | None) -> str:
    """Açık yönlendirmeyi engeller: yalnızca aynı origin içi mutlak yol."""
    if not nxt or not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt or nxt.startswith("/auth/"):
        return "/"
    return nxt


def _is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip() == "https"


def _wants_html(request: Request) -> bool:
    return "text/html" in request.headers.get("accept", "")


# --- uçlar -------------------------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/auth/verify")
def verify(request: Request) -> Response:
    return Response(status_code=204 if check_token(request.cookies.get(COOKIE_NAME)) else 401)


@app.api_route("/auth/unauthorized", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
def unauthorized(request: Request) -> Response:
    original = request.headers.get("x-original-uri", "/")
    if _wants_html(request):
        return RedirectResponse(f"/auth/login?next={quote(safe_next(original), safe='')}", status_code=302)
    return JSONResponse({"detail": "Giriş gerekli", "login": "/auth/login"}, status_code=401)


@app.get("/auth/login")
def login_page(request: Request, next: str = "/", error: int = 0) -> Response:
    if check_token(request.cookies.get(COOKIE_NAME)):
        return RedirectResponse(safe_next(next), status_code=303)
    return HTMLResponse(render_login(safe_next(next), bool(error)), headers={"Cache-Control": "no-store"})


@app.post("/auth/login")
async def login(request: Request) -> Response:
    body = await request.body()
    is_json = request.headers.get("content-type", "").startswith("application/json")
    if is_json:
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = {}
        user, pw, nxt = str(data.get("username", "")), str(data.get("password", "")), data.get("next")
    else:
        form = parse_qs(body.decode("utf-8", "replace"))
        user, pw, nxt = form.get("username", [""])[0], form.get("password", [""])[0], form.get("next", ["/"])[0]
    nxt = safe_next(nxt)

    if not credentials_ok(user, pw):
        log.warning("başarısız giriş: user=%r ip=%s", user[:64], request.headers.get("x-real-ip", "?"))
        if is_json:
            return JSONResponse({"detail": "Kullanıcı adı veya şifre hatalı"}, status_code=401)
        return HTMLResponse(render_login(nxt, True), status_code=401, headers={"Cache-Control": "no-store"})

    resp: Response = (JSONResponse({"ok": True, "next": nxt}) if is_json
                      else RedirectResponse(nxt, status_code=303))
    resp.set_cookie(COOKIE_NAME, make_token(USERNAME), max_age=SESSION_TTL_S, path="/",
                    httponly=True, samesite="lax", secure=_is_https(request))
    return resp


@app.api_route("/auth/logout", methods=["GET", "POST"])
def logout() -> Response:
    resp = RedirectResponse("/auth/login", status_code=303)
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp


# --- giriş sayfası -----------------------------------------------------------------------------------
def render_login(nxt: str, error: bool) -> str:
    err = '<div class="err" role="alert">Kullanıcı adı veya şifre hatalı.</div>' if error else ""
    return _PAGE.replace("{{NEXT}}", html.escape(nxt, quote=True)).replace("{{ERROR}}", err)


_PAGE = """<!doctype html>
<html lang="tr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex, nofollow">
<title>Üs Koruma — Giriş</title>
<style>
  :root {
    --bg: #0b0f14; --panel: #121821; --panel-2: #0f141b; --border: #1f2a37;
    --text: #d7e0ea; --muted: #7d8b9a; --accent: #4cc9f0; --high: #ff453a;
    --mono: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
    color-scheme: dark;
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    background:
      radial-gradient(circle at 50% 38%, rgba(76,201,240,.10), transparent 55%),
      repeating-radial-gradient(circle at 50% 38%, transparent 0 59px, rgba(76,201,240,.05) 60px 61px),
      linear-gradient(rgba(31,42,55,.35) 1px, transparent 1px) 0 0 / 40px 40px,
      linear-gradient(90deg, rgba(31,42,55,.35) 1px, transparent 1px) 0 0 / 40px 40px,
      var(--bg);
    color: var(--text);
    font: 14px/1.5 system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif;
    display: grid; place-items: center; padding: 16px;
  }
  .card {
    width: 100%; max-width: 380px; background: var(--panel); border: 1px solid var(--border);
    border-radius: 12px; padding: 28px 26px 22px; box-shadow: 0 20px 60px rgba(0,0,0,.55);
    position: relative; overflow: hidden;
  }
  .card::before {
    content: ""; position: absolute; inset: 0 0 auto 0; height: 3px;
    background: linear-gradient(90deg, transparent, var(--accent), transparent);
  }
  .brand { display: flex; align-items: center; gap: 12px; margin-bottom: 22px; }
  .radar { width: 44px; height: 44px; flex: none; }
  .radar .sweep { transform-origin: 22px 22px; animation: spin 3s linear infinite; }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (prefers-reduced-motion: reduce) { .radar .sweep { animation: none; } }
  h1 { font-size: 17px; margin: 0; letter-spacing: .3px; }
  .sub { color: var(--muted); font-size: 12px; font-family: var(--mono); letter-spacing: .6px; }
  label { display: block; font-size: 12px; color: var(--muted); margin: 14px 0 6px; text-transform: uppercase; letter-spacing: .8px; }
  input[type=text], input[type=password] {
    width: 100%; padding: 11px 12px; background: var(--panel-2); color: var(--text);
    border: 1px solid var(--border); border-radius: 8px; font: inherit; outline: none;
    transition: border-color .15s, box-shadow .15s;
  }
  input:focus { border-color: var(--accent); box-shadow: 0 0 0 3px rgba(76,201,240,.18); }
  button {
    margin-top: 20px; width: 100%; padding: 11px; border: 0; border-radius: 8px; cursor: pointer;
    background: var(--accent); color: #04121a; font: 600 14px/1 system-ui, sans-serif; letter-spacing: .4px;
  }
  button:hover { filter: brightness(1.08); }
  button:focus-visible { outline: 2px solid var(--text); outline-offset: 2px; }
  .err {
    margin-top: 4px; padding: 9px 12px; border-radius: 8px; font-size: 13px;
    background: rgba(255,69,58,.10); border: 1px solid rgba(255,69,58,.45); color: #ffb3ad;
  }
  .foot { margin-top: 18px; text-align: center; color: var(--muted); font-size: 11px; font-family: var(--mono); letter-spacing: .5px; }
  .dot { display: inline-block; width: 6px; height: 6px; border-radius: 50%; background: var(--high); margin-right: 6px; vertical-align: 1px; }
</style>
</head>
<body>
<main class="card">
  <div class="brand">
    <svg class="radar" viewBox="0 0 44 44" aria-hidden="true">
      <circle cx="22" cy="22" r="20" fill="none" stroke="#1f2a37"/>
      <circle cx="22" cy="22" r="13" fill="none" stroke="#1f2a37"/>
      <circle cx="22" cy="22" r="6" fill="none" stroke="#1f2a37"/>
      <g class="sweep"><path d="M22 22 L22 2 A20 20 0 0 1 39.3 12 Z" fill="rgba(76,201,240,.28)"/>
        <line x1="22" y1="22" x2="22" y2="2" stroke="#4cc9f0" stroke-width="1.5"/></g>
      <circle cx="30" cy="14" r="2" fill="#ff453a"/>
    </svg>
    <div>
      <h1>Üs Koruma Sistemi</h1>
      <div class="sub">TEHLİKE ALARM · YETKİLİ ERİŞİM</div>
    </div>
  </div>
  {{ERROR}}
  <form method="post" action="/auth/login" autocomplete="on">
    <input type="hidden" name="next" value="{{NEXT}}">
    <label for="u">Kullanıcı adı</label>
    <input id="u" name="username" type="text" autocomplete="username" autocapitalize="none" spellcheck="false" required autofocus>
    <label for="p">Şifre</label>
    <input id="p" name="password" type="password" autocomplete="current-password" required>
    <button type="submit">Giriş yap</button>
  </form>
  <div class="foot"><span class="dot"></span>Oturum tüm erişimler için kayıt altındadır</div>
</main>
</body>
</html>
"""
