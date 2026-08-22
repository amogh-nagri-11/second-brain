"""A local page for the app, served by the app.

A macOS menu is drawn by the system -- there is no styling it -- so anything that
should look like something gets its own surface. This binds to loopback only and
is started by the menubar app, which stays the way you launch and control it.
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HOST = "127.0.0.1"
PORT = 8765

PAGE = Path(__file__).with_name("index.html")


class _Handler(BaseHTTPRequestHandler):
    # set by the factory below
    app = None

    def log_message(self, *args):
        # the default handler writes a line per request to stderr, which would bury
        # the app's own logging
        pass

    # --- plumbing -------------------------------------------------------------

    def _send(self, status: int, body: bytes, content_type: str):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, payload, status: int = 200):
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except (ValueError, UnicodeDecodeError):
            return {}

    # --- routes ---------------------------------------------------------------

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            # read per request so edits to the page show up on reload
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(self.app.ui_state())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if self.path == "/api/ask":
            question = (self._body().get("question") or "").strip()
            if not question:
                self._json({"error": "empty question"}, 400)
                return
            # blocking, which is fine: the server is threaded, and the page shows a
            # pending state while it waits
            self._json(self.app.ask_text(question))

        elif self.path == "/api/stop":
            self.app.speaker.stop()
            self._json({"ok": True})

        elif self.path == "/api/replay":
            self.app.replay_index(int(self._body().get("index", 0)))
            self._json({"ok": True})

        elif self.path == "/api/record":
            wanted = self._body().get("recording")
            if wanted:
                self.app._start_recording()
            else:
                self.app._stop_recording()
            self._json({"ok": True})

        elif self.path == "/api/sync":
            threading.Thread(target=self.app._sync, args=(False,), daemon=True).start()
            self._json({"ok": True})

        else:
            self._json({"error": "not found"}, 404)


class UIServer:
    def __init__(self, app, host: str = HOST, port: int = PORT):
        self.host = host
        self.port = port
        self._app = app
        self._server: ThreadingHTTPServer | None = None

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/"

    def start(self):
        handler = type("Handler", (_Handler,), {"app": self._app})
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            self._server = None
