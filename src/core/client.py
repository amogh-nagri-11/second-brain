"""Talking to the running service, from the CLI, the menubar or a hotkey.

Standard library only, and nothing heavy imported: `python -m src record` is what
a keyboard shortcut runs, so it has to start in a fraction of a second rather
than load numpy and the embedding model just to send one request.
"""

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

SERVICE_FILE = "service.json"


class ServiceNotRunning(RuntimeError):
    def __init__(self):
        super().__init__("the service isn't running -- start it with: python -m src serve")


def service_file() -> Path:
    # platformdirs is small, but the data folder logic also copies legacy files;
    # reuse it so there is exactly one idea of where the app folder is
    from src.config.paths import data_dir
    return data_dir() / SERVICE_FILE


def write_service_file(port: int, token: str) -> Path:
    path = service_file()
    # create it private before the token goes in, rather than chmod afterwards
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"port": port, "token": token, "pid": os.getpid()}, handle)
    return path


def read_service_file() -> dict | None:
    try:
        return json.loads(service_file().read_text())
    except (FileNotFoundError, ValueError):
        return None


def base_url(info: dict | None = None) -> str:
    info = info or read_service_file()
    if info is None:
        raise ServiceNotRunning()
    return f"http://127.0.0.1:{info['port']}"


def call(method: str, path: str, body: dict | None = None, timeout: float = 120.0) -> dict:
    info = read_service_file()
    if info is None:
        raise ServiceNotRunning()

    data = json.dumps(body or {}).encode() if method != "GET" else None
    request = urllib.request.Request(
        base_url(info) + path,
        data=data,
        method=method,
        headers={"X-Second-Brain-Token": info["token"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        try:
            message = json.loads(error.read()).get("error", error.reason)
        except ValueError:
            message = error.reason
        raise RuntimeError(f"{error.code}: {message}") from None
    except (urllib.error.URLError, ConnectionError):
        # a service.json left behind by a service that crashed
        raise ServiceNotRunning() from None


def is_running() -> bool:
    try:
        call("GET", "/api/state", timeout=2)
        return True
    except (ServiceNotRunning, RuntimeError):
        return False


def events():
    """Yield each event the service publishes, starting with its full state.
    Returns when the connection drops; callers reconnect."""
    info = read_service_file()
    if info is None:
        raise ServiceNotRunning()

    request = urllib.request.Request(
        f"{base_url(info)}/api/events",
        headers={"X-Second-Brain-Token": info["token"]},
    )
    try:
        response = urllib.request.urlopen(request, timeout=30)
    except (urllib.error.URLError, ConnectionError):
        raise ServiceNotRunning() from None

    with response:
        for raw in response:
            line = raw.decode("utf-8").rstrip("\n")
            if line.startswith("data: "):
                yield json.loads(line[len("data: "):])
