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

# binding to loopback keeps other machines out, but not other web pages: any site
# open in the browser can fire requests at 127.0.0.1, and one that rebinds its own
# DNS name to 127.0.0.1 can read the replies too. Only these names are ours.
def allowed_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


class _Handler(BaseHTTPRequestHandler):
    # set by the factory below
    app = None
    allowed_hosts: set[str] = set()

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

    def _trusted(self, write: bool) -> bool:
        """Refuse anything that didn't come from our own page.

        A rebinding site arrives with its own name in Host, so checking Host stops it
        reading state. Writes also need an Origin of our own, if one is sent, and a
        JSON content type -- a cross-site page can only send JSON after a CORS
        preflight, which this server never approves -- so a page can't start the
        microphone or spend API calls by posting a form here.
        """
        if self.headers.get("Host") not in self.allowed_hosts:
            return False
        if not write:
            return True
        origin = self.headers.get("Origin")
        if origin is not None and origin.removeprefix("http://") not in self.allowed_hosts:
            return False
        content_type = self.headers.get("Content-Type", "")
        return content_type.split(";")[0].strip() == "application/json"

    # --- routes ---------------------------------------------------------------

    def do_GET(self):
        if not self._trusted(write=False):
            self._json({"error": "forbidden"}, 403)
            return
        if self.path in ("/", "/index.html"):
            # read per request so edits to the page show up on reload
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/state":
            self._json(self.app.ui_state())
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):
        if not self._trusted(write=True):
            self._json({"error": "forbidden"}, 403)
            return
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
        handler = type(
            "Handler", (_Handler,), {"app": self._app, "allowed_hosts": allowed_hosts(self.port)}
        )
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self):
        if self._server is not None:
            self._server.shutdown()
            # shutdown() only stops the loop; the listening socket stays bound
            self._server.server_close()
            self._server = None
