#!/usr/bin/env python3
"""КРЯК Portal: static file server, authentication and SQLite storage.

SQLite is free and stores data in one file on the application server. For real
personal data, run this application only on infrastructure physically located
in the Russian Federation and set DATA_RESIDENCY_CONFIRMED=RU.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from http import HTTPStatus
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from tabrica_storage import TabricaError, TabricaStorage

ROOT = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("PORTAL_DB_PATH", ROOT / "data" / "portal.db")).resolve()
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))
PRODUCTION = os.environ.get("APP_ENV") == "production"
COOKIE_SECURE = PRODUCTION or os.environ.get("COOKIE_SECURE") == "1"
SESSION_SECONDS = 30 * 24 * 60 * 60
CONSENT_VERSION = "1.0-2026-11-14"
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
RATE_LIMIT: dict[str, list[float]] = {}
STORAGE_MODE = os.environ.get("PORTAL_STORAGE", "sqlite").lower()
DATA_PROCESSING_ENABLED = os.environ.get("PORTAL_DATA_PROCESSING_ENABLED") == "1"
TABRICA: TabricaStorage | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db() -> None:
    global TABRICA
    if STORAGE_MODE == "tabrica":
        TABRICA = TabricaStorage()
        TABRICA.healthcheck()
        return
    if STORAGE_MODE != "sqlite":
        raise RuntimeError("PORTAL_STORAGE must be 'sqlite' or 'tabrica'")
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash BLOB NOT NULL,
                password_salt BLOB NOT NULL,
                first_name TEXT NOT NULL,
                last_name TEXT NOT NULL,
                region TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'citizen'
                    CHECK (role IN ('citizen', 'member', 'lead')),
                consent_at TEXT NOT NULL,
                consent_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash TEXT NOT NULL UNIQUE,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS applications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                public_id TEXT UNIQUE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
                service_type TEXT NOT NULL,
                city TEXT NOT NULL,
                address TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'received'
                    CHECK (status IN ('received', 'in_progress', 'resolved', 'rejected')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);
            CREATE INDEX IF NOT EXISTS idx_applications_user ON applications(user_id, created_at);
            """
        )
        columns = {row["name"] for row in db.execute("PRAGMA table_info(users)")}
        if "consent_version" not in columns:
            db.execute("ALTER TABLE users ADD COLUMN consent_version TEXT NOT NULL DEFAULT 'legacy-before-2026-11-14'")
        db.execute("DELETE FROM sessions WHERE expires_at < ?", (int(time.time()),))


def password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1, dklen=32)


def clean_text(value: object, max_length: int) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.strip().split())[:max_length]


def public_user(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"], "email": row["email"], "firstName": row["first_name"],
        "lastName": row["last_name"], "region": row["region"], "role": row["role"],
        "createdAt": row["created_at"],
    }


class PortalHandler(SimpleHTTPRequestHandler):
    server_version = "KRYAKPortal/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}")

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "same-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def translate_path(self, path: str) -> str:
        result = Path(super().translate_path(path)).resolve()
        try:
            result.relative_to(ROOT)
        except ValueError:
            return str(ROOT / "__not_found__")
        if result == DB_PATH or ".git" in result.parts or "data" in result.relative_to(ROOT).parts[:1]:
            return str(ROOT / "__not_found__")
        return str(result)

    def json_response(self, status: int, payload: dict, cookie: str | None = None) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def read_json(self) -> dict | None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 2 or length > 64_000:
                return None
            data = json.loads(self.rfile.read(length))
            return data if isinstance(data, dict) else None
        except (ValueError, json.JSONDecodeError):
            return None

    def rate_limited(self) -> bool:
        ip = self.client_address[0]
        now = time.time()
        attempts = [x for x in RATE_LIMIT.get(ip, []) if now - x < 300]
        RATE_LIMIT[ip] = attempts
        if len(attempts) >= 20:
            return True
        attempts.append(now)
        return False

    def session_token(self) -> str | None:
        raw = self.headers.get("Cookie")
        if not raw:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
            return cookie["kryak_session"].value if "kryak_session" in cookie else None
        except Exception:
            return None

    def current_user(self) -> sqlite3.Row | None:
        token = self.session_token()
        if not token:
            return None
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        if TABRICA:
            session = TABRICA.session_by_hash(token_hash)
            if not session or int(session.get("expires_at", 0)) <= int(time.time()):
                return None
            return TABRICA.user_by_id(int(session["user_id"]))
        with connect() as db:
            return db.execute(
                """SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
                   WHERE s.token_hash=? AND s.expires_at>?""",
                (token_hash, int(time.time())),
            ).fetchone()

    def issue_session(self, user_id: int) -> str:
        token = secrets.token_urlsafe(32)
        values = {"user_id": user_id, "token_hash": hashlib.sha256(token.encode()).hexdigest(), "expires_at": int(time.time()) + SESSION_SECONDS, "created_at": now_iso()}
        if TABRICA:
            TABRICA.create_session(values)
        else:
            with connect() as db:
                db.execute(
                    "INSERT INTO sessions(user_id,token_hash,expires_at,created_at) VALUES(?,?,?,?)",
                    (values["user_id"], values["token_hash"], values["expires_at"], values["created_at"]),
                )
        secure = "; Secure" if COOKIE_SECURE else ""
        return f"kryak_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age={SESSION_SECONDS}{secure}"

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            self.json_response(200, {"ok": True, "database": STORAGE_MODE, "dataProcessing": DATA_PROCESSING_ENABLED, "residency": "Russia (provider confirmation required)" if TABRICA else "server-local"})
            return
        if path.startswith("/api/") and not DATA_PROCESSING_ENABLED:
            self.json_response(503, {"error": "Обработка персональных данных не ведётся до направления уведомления Роскомнадзору и официального запуска"})
            return
        if path == "/api/auth/me":
            try:
                user = self.current_user()
            except TabricaError:
                self.json_response(503, {"error": "Российское хранилище временно недоступно"})
                return
            self.json_response(200, {"authenticated": bool(user), "user": public_user(user) if user else None})
            return
        if path == "/api/applications":
            try:
                user = self.current_user()
                if not user:
                    self.json_response(401, {"error": "Требуется вход в личный кабинет"})
                    return
                if TABRICA:
                    rows = TABRICA.applications_for_user(int(user["id"]))
                    allowed = ("public_id", "service_type", "city", "address", "category", "description", "status", "created_at")
                    applications = [{key: row.get(key) for key in allowed} for row in rows]
                else:
                    with connect() as db:
                        rows = db.execute(
                            "SELECT public_id,service_type,city,address,category,description,status,created_at FROM applications WHERE user_id=? ORDER BY id DESC",
                            (user["id"],),
                        ).fetchall()
                    applications = [dict(x) for x in rows]
            except TabricaError:
                self.json_response(503, {"error": "Российское хранилище временно недоступно"})
                return
            self.json_response(200, {"applications": applications})
            return
        super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path.startswith("/api/") and not DATA_PROCESSING_ENABLED:
            self.json_response(503, {"error": "Обработка персональных данных не ведётся до направления уведомления Роскомнадзору и официального запуска"})
            return
        if path in {"/api/auth/register", "/api/auth/login"} and self.rate_limited():
            self.json_response(429, {"error": "Слишком много попыток. Повторите через несколько минут."})
            return
        if path == "/api/auth/register":
            self.register()
        elif path == "/api/auth/login":
            self.login()
        elif path == "/api/auth/logout":
            self.logout()
        elif path == "/api/applications":
            self.create_application()
        else:
            self.json_response(404, {"error": "Маршрут не найден"})

    def register(self) -> None:
        data = self.read_json()
        if data is None:
            self.json_response(400, {"error": "Некорректный запрос"})
            return
        email = clean_text(data.get("email"), 254).lower()
        password = data.get("password") if isinstance(data.get("password"), str) else ""
        first = clean_text(data.get("firstName"), 80)
        last = clean_text(data.get("lastName"), 80)
        region = clean_text(data.get("region"), 120)
        consent = data.get("consent") is True
        consent_version = clean_text(data.get("consentVersion"), 40)
        if not EMAIL_RE.match(email):
            self.json_response(400, {"error": "Укажите корректный email"}); return
        if len(password) < 10 or len(password) > 200:
            self.json_response(400, {"error": "Пароль должен содержать не менее 10 символов"}); return
        if not first or not last or not region:
            self.json_response(400, {"error": "Заполните имя, фамилию и регион"}); return
        if not consent or consent_version != CONSENT_VERSION:
            self.json_response(400, {"error": "Необходимо подтвердить актуальную версию согласия на обработку персональных данных"}); return
        salt = secrets.token_bytes(16)
        created = now_iso()
        try:
            if TABRICA:
                if TABRICA.user_by_email(email):
                    self.json_response(409, {"error": "Пользователь с таким email уже зарегистрирован"}); return
                user = TABRICA.create_user({
                    "email": email,
                    "password_hash": base64.b64encode(password_hash(password, salt)).decode(),
                    "password_salt": base64.b64encode(salt).decode(),
                    "first_name": first, "last_name": last, "region": region, "role": "citizen",
                    "consent_at": created, "consent_version": CONSENT_VERSION,
                    "created_at": created, "updated_at": created,
                })
                user_id = int(user["id"])
            else:
                with connect() as db:
                    cur = db.execute(
                        """INSERT INTO users(email,password_hash,password_salt,first_name,last_name,region,consent_at,consent_version,created_at,updated_at)
                           VALUES(?,?,?,?,?,?,?,?,?,?)""",
                        (email, password_hash(password, salt), salt, first, last, region, created, CONSENT_VERSION, created, created),
                    )
                    user_id = cur.lastrowid
                    user = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        except sqlite3.IntegrityError:
            self.json_response(409, {"error": "Пользователь с таким email уже зарегистрирован"}); return
        except TabricaError:
            self.json_response(503, {"error": "Российское хранилище временно недоступно"}); return
        try:
            cookie = self.issue_session(user_id)
        except TabricaError:
            self.json_response(503, {"error": "Учётная запись создана, но российское хранилище не смогло открыть сессию. Повторите вход позже."}); return
        self.json_response(201, {"ok": True, "user": public_user(user)}, cookie)

    def login(self) -> None:
        data = self.read_json()
        if data is None:
            self.json_response(400, {"error": "Некорректный запрос"}); return
        email = clean_text(data.get("email"), 254).lower()
        password = data.get("password") if isinstance(data.get("password"), str) else ""
        try:
            if TABRICA:
                user = TABRICA.user_by_email(email)
                salt = base64.b64decode(user["password_salt"]) if user else b""
                expected = base64.b64decode(user["password_hash"]) if user else b""
            else:
                with connect() as db:
                    user = db.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
                salt = user["password_salt"] if user else b""
                expected = user["password_hash"] if user else b""
        except (TabricaError, ValueError):
            self.json_response(503, {"error": "Российское хранилище временно недоступно"}); return
        valid = bool(user) and hmac.compare_digest(password_hash(password, salt), expected)
        if not valid:
            self.json_response(401, {"error": "Неверный email или пароль"}); return
        try:
            cookie = self.issue_session(user["id"])
        except TabricaError:
            self.json_response(503, {"error": "Российское хранилище не смогло открыть сессию. Повторите позже."}); return
        self.json_response(200, {"ok": True, "user": public_user(user)}, cookie)

    def logout(self) -> None:
        token = self.session_token()
        if token:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            if TABRICA:
                try:
                    TABRICA.delete_session(token_hash)
                except TabricaError:
                    pass
            else:
                with connect() as db:
                    db.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))
        secure = "; Secure" if COOKIE_SECURE else ""
        self.json_response(200, {"ok": True}, f"kryak_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0{secure}")

    def create_application(self) -> None:
        try:
            user = self.current_user()
        except TabricaError:
            self.json_response(503, {"error": "Российское хранилище временно недоступно"}); return
        if not user:
            self.json_response(401, {"error": "Сначала войдите в личный кабинет"}); return
        data = self.read_json()
        if data is None:
            self.json_response(400, {"error": "Некорректный запрос"}); return
        city = clean_text(data.get("city"), 120)
        address = clean_text(data.get("address"), 250)
        category = clean_text(data.get("category"), 120)
        description = clean_text(data.get("description"), 4000)
        if not all((city, address, category, description)):
            self.json_response(400, {"error": "Заполните все поля обращения"}); return
        stamp = now_iso()
        if TABRICA:
            public_id = f"КР-{datetime.now().year}-{secrets.randbelow(1_000_000):06d}"
            try:
                TABRICA.create_application({
                    "public_id": public_id, "user_id": int(user["id"]), "service_type": "map_inaccuracy",
                    "city": city, "address": address, "category": category, "description": description,
                    "status": "received", "created_at": stamp, "updated_at": stamp,
                })
            except TabricaError:
                self.json_response(503, {"error": "Российское хранилище временно недоступно"}); return
        else:
            with connect() as db:
                cur = db.execute(
                    """INSERT INTO applications(user_id,service_type,city,address,category,description,created_at,updated_at)
                       VALUES(?,?,?,?,?,?,?,?)""",
                    (user["id"], "map_inaccuracy", city, address, category, description, stamp, stamp),
                )
                app_id = cur.lastrowid
                public_id = f"КР-{datetime.now().year}-{app_id:06d}"
                db.execute("UPDATE applications SET public_id=? WHERE id=?", (public_id, app_id))
        self.json_response(201, {"ok": True, "application": {"id": public_id, "status": "received", "createdAt": stamp}})


if __name__ == "__main__":
    if PRODUCTION and os.environ.get("DATA_RESIDENCY_CONFIRMED") != "RU":
        raise SystemExit("Production start blocked: set DATA_RESIDENCY_CONFIRMED=RU only on infrastructure physically located in Russia.")
    if PRODUCTION:
        policy = (ROOT / "политика-обработки-персональных-данных.html").read_text(encoding="utf-8")
        if "ДО ПУБЛИЧНОГО ЗАПУСКА УКАЗАТЬ" in policy or "[УКАЗАТЬ АДРЕС" in policy:
            raise SystemExit("Production start blocked: fill the individual operator's registration/residence address in all legal documents.")
        required_confirmations = {
            "RKN_NOTIFICATION_FILED": "file the Roskomnadzor notification",
            "PD_DOCUMENTS_APPROVED": "approve the personal-data documents",
            "PROCESSOR_TERMS_CONFIRMED": "confirm processor terms and Russian storage locations",
        }
        missing = [message for name, message in required_confirmations.items() if os.environ.get(name) != "1"]
        if missing:
            raise SystemExit("Production start blocked: " + "; ".join(missing) + ".")
    init_db()
    print(f"КРЯКтика: http://{HOST}:{PORT}/портал.html")
    print("Storage: Tabrica (Russia)" if TABRICA else f"SQLite: {DB_PATH}")
    ThreadingHTTPServer((HOST, PORT), PortalHandler).serve_forever()
