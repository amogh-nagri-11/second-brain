"""`python -m src serve`: run the service in the foreground.

This is the process that `python -m src service install` starts at login.
"""

import atexit
import signal
import socket
import sys

from src.config.paths import log_dir
from src.core import client

PREFERRED_PORT = 8765
# keep one old log beside the current one, so a restart doesn't wipe the evidence
MAX_LOG_BYTES = 5 * 1024 * 1024


def _log_to_file():
    """Send everything printed to the service log.

    pythonw (how Windows runs it without a console) has no stdout at all, and under
    launchd and systemd a file of our own keeps the log in the same place on every
    OS: `python -m src service logs` knows where to look.
    """
    path = log_dir() / "service.log"
    if path.exists() and path.stat().st_size > MAX_LOG_BYTES:
        path.replace(path.with_suffix(".log.1"))
    handle = open(path, "a", buffering=1, encoding="utf-8")
    sys.stdout = sys.stderr = handle


def _bind() -> socket.socket:
    """The usual port if it's free, so a bookmarked window keeps working; any free
    port otherwise. Clients find it through service.json either way."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform != "win32":
        # connections from the last run linger in TIME_WAIT for a minute or so and
        # would otherwise push a quick restart onto a random port. (On Windows the
        # same option would let another program take the port over, so not there.)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("127.0.0.1", PREFERRED_PORT))
    except OSError:
        sock.bind(("127.0.0.1", 0))
    return sock


def serve(log_file: bool = False) -> int:
    if log_file or sys.stdout is None:
        _log_to_file()
    else:
        # piped or redirected output is block-buffered by default, which holds log
        # lines back until the buffer fills
        sys.stdout.reconfigure(line_buffering=True)

    if client.is_running():
        print(f"already running at {client.base_url()}")
        return 1

    import uvicorn

    from src.core.api import create_app, new_token
    from src.core.service import Brain

    sock = _bind()
    port = sock.getsockname()[1]
    token = new_token()

    brain = Brain()
    app = create_app(brain, token, port)

    service_file = client.write_service_file(port, token)

    def cleanup():
        brain.speaker.stop()
        # only remove it if it's still ours -- a second copy may have replaced it
        info = client.read_service_file()
        if info and info.get("token") == token:
            service_file.unlink(missing_ok=True)

    atexit.register(cleanup)
    # uvicorn shuts down cleanly on SIGTERM and then re-raises it, which under the
    # default handler kills the process before atexit runs -- exit normally instead
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    brain.start_scheduler()
    print(f"[serve] listening on http://127.0.0.1:{port}/", flush=True)

    config = uvicorn.Config(app, log_level="warning", access_log=False)
    uvicorn.Server(config).run(sockets=[sock])
    return 0
