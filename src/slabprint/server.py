# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Franz Sittampalam
"""An HTTP print service, so anything on the local network can print.

Deliberately minimal: standard library only, one endpoint, optional shared token.
Bind to a LAN address only on a network you trust — there is no TLS and a token
in a header is not real authentication.
"""

from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

MAX_BODY_BYTES = 1 << 20  # 1 MB is far more text than the paper can hold

# A client that opens a socket and says nothing must not be able to hold the
# service hostage. Applies per connection, before any request line is parsed.
REQUEST_TIMEOUT_SECONDS = 15


def make_handler(print_lines, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "slabprint"
        protocol_version = "HTTP/1.1"
        timeout = REQUEST_TIMEOUT_SECONDS

        def reply(self, code: int, body: str) -> None:
            payload = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def authorised(self) -> bool:
            if not token:
                return True
            # compare_digest rather than == so the check does not leak the token
            # length or prefix through timing. Cheap, so there is no reason not to.
            supplied = self.headers.get("X-Token") or ""
            return hmac.compare_digest(supplied, token)

        def body_length(self) -> int | None:
            """Validated Content-Length, or None if it is missing or nonsense."""
            raw = self.headers.get("Content-Length")
            if raw is None:
                return 0
            try:
                length = int(raw)
            except ValueError:
                return None
            # Negative lengths are the interesting case: read(-1) reads to EOF,
            # which would sail straight past the size limit.
            if length < 0 or length > MAX_BODY_BYTES:
                return None
            return length

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, "ok\n")
            else:
                self.reply(404, "try POST /print\n")

        def do_POST(self):
            if not self.authorised():
                return self.reply(403, "bad or missing X-Token\n")
            if self.path != "/print":
                return self.reply(404, "try POST /print\n")

            length = self.body_length()
            if length is None:
                return self.reply(413, f"body must be 0..{MAX_BODY_BYTES} bytes\n")

            body = self.rfile.read(length).decode("utf-8", "replace") if length else ""
            lines = body.splitlines()
            if not any(line.strip() for line in lines):
                return self.reply(400, "nothing to print\n")

            try:
                self.reply(200, print_lines(lines) + "\n")
            except Exception as exc:
                self.reply(500, f"{type(exc).__name__}: {exc}\n")

        def log_message(self, fmt, *args):
            pass  # quiet by default; the caller sees HTTP status codes

    return Handler


def serve(print_lines, host: str = "127.0.0.1", port: int = 8719, token: str | None = None):
    if not token:
        print("warning: no token set, anyone who can reach this port can print")
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: listening on {host}, which is reachable beyond this machine")

    # Threading, so one slow or idle client cannot block every other request.
    httpd = ThreadingHTTPServer((host, port), make_handler(print_lines, token))
    print(f"slabprint listening on http://{host}:{port}  (POST /print)")
    httpd.serve_forever()
