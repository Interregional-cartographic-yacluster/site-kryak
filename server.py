#!/usr/bin/env python3
"""Static-only server for the public КРЯК website.

The application intentionally has no registration, authentication, submission,
database, session-cookie, or personal-data API. КРЯКтика source files remain in
the repository but are not served before 1 July 2027 under the owner's family
agreement. Publishing after that date still requires an explicit code change.
"""

import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "8000"))


class StaticSiteHandler(SimpleHTTPRequestHandler):
    server_version = "KRYAKStatic/1.0"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def log_message(self, _format: str, *args) -> None:
        # Do not persist visitor IP addresses or request paths in application logs.
        return

    def end_headers(self) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "SAMEORIGIN")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        super().end_headers()

    def translate_path(self, path: str) -> str:
        result = Path(super().translate_path(path)).resolve()
        try:
            relative = result.relative_to(ROOT)
        except ValueError:
            return str(ROOT / "__not_found__")
        hidden_file = relative in {
            Path("портал.html"),
            Path("портал-дизайн-система.html"),
            Path("пользовательское-соглашение-портала.html"),
            Path("КРЯКТИКА-НЕ-ПУБЛИКОВАТЬ-ДО-2027-07-01.md"),
        }
        hidden_directory = relative.parts[:1] in {("data",), ("documents",), ("портал",)}
        if ".git" in result.parts or hidden_file or hidden_directory or result.name.startswith(".env"):
            return str(ROOT / "__not_found__")
        if not result.exists() and not result.suffix:
            html_result = result.with_suffix(".html")
            if html_result.is_file():
                return str(html_result)
        return str(result)

    def do_GET(self) -> None:
        if urlparse(self.path).path.startswith("/api/"):
            payload = json.dumps(
                {"error": "API удалён: сайт работает только как статическая демонстрация без обработки персональных данных"},
                ensure_ascii=False,
            ).encode("utf-8")
            self.send_response(410)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        super().do_GET()

    def reject_write(self) -> None:
        payload = b'{"error":"Static site: write operations are not supported"}'
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_POST = reject_write
    do_PUT = reject_write
    do_PATCH = reject_write
    do_DELETE = reject_write


if __name__ == "__main__":
    print(f"Статический сайт КРЯК: http://{HOST}:{PORT}/")
    print("КРЯКтика временно не публикуется до отдельного решения после 30 июня 2027 года.")
    ThreadingHTTPServer((HOST, PORT), StaticSiteHandler).serve_forever()
