"""An HTTP print service, so anything on the local network can print.

Deliberately minimal: standard library only, one endpoint, optional shared token.
Bind to a LAN address only on a network you trust — there is no TLS and a token
in a header is not real authentication.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer

MAX_BODY_BYTES = 1 << 20  # 1 MB is far more text than the paper can hold


def make_handler(print_lines, token):
    class Handler(BaseHTTPRequestHandler):
        server_version = "nemonic"

        def reply(self, code: int, body: str) -> None:
            payload = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            if self.path == "/health":
                self.reply(200, "ok\n")
            else:
                self.reply(404, "try POST /print\n")

        def do_POST(self):
            if token and self.headers.get("X-Token") != token:
                return self.reply(403, "bad or missing X-Token\n")
            if self.path != "/print":
                return self.reply(404, "try POST /print\n")

            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                return self.reply(413, "body too large\n")
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


def serve(print_lines, host: str = "0.0.0.0", port: int = 8719, token: str | None = None):
    httpd = HTTPServer((host, port), make_handler(print_lines, token))
    print(f"nemonic listening on http://{host}:{port}  (POST /print)")
    if not token:
        print("warning: no token set, anyone on this network can print")
    httpd.serve_forever()
