"""The service's HTTP API, and the page it serves.

Loopback only, and three checks on top, because binding to 127.0.0.1 keeps other
machines out but not other web pages or other programs:

  Host     a page on another site that rebinds its DNS name to 127.0.0.1 arrives
           with its own name here, so anything else is refused -- it can't read
           the page, and so can't read the token in it
  Origin   writes must come from our own page, when a browser says where from
  token    every /api call carries the token this launch wrote to service.json,
           readable only by you. That is what shuts out other local programs.

The token changes every launch; a page left open across a restart reloads itself
to pick up the new one.
"""

import asyncio
import json
import secrets
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel

PAGE = Path(__file__).resolve().parents[1] / "ui" / "index.html"
TOKEN_PLACEHOLDER = "__SECOND_BRAIN_TOKEN__"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def allowed_hosts(port: int) -> set[str]:
    return {f"127.0.0.1:{port}", f"localhost:{port}"}


class AskBody(BaseModel):
    question: str


class RecordBody(BaseModel):
    # true / false to start or stop; omitted to toggle, which is what a
    # single keyboard shortcut needs
    recording: bool | None = None


class ReplayBody(BaseModel):
    index: int = 0


def create_app(brain, token: str, port: int) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    hosts = allowed_hosts(port)

    @app.middleware("http")
    async def same_site_only(request: Request, call_next):
        if request.headers.get("host") not in hosts:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        if request.method != "GET":
            origin = request.headers.get("origin")
            if origin is not None and origin.removeprefix("http://") not in hosts:
                return JSONResponse({"error": "forbidden"}, status_code=403)
            content_type = request.headers.get("content-type", "").split(";")[0].strip()
            if content_type != "application/json":
                return JSONResponse({"error": "expected application/json"}, status_code=415)
        return await call_next(request)

    def authorised(request: Request):
        # EventSource can't set headers, so the event stream takes it as a query
        # parameter instead; it never leaves the machine
        supplied = request.headers.get("x-second-brain-token") or request.query_params.get("token")
        if not supplied or not secrets.compare_digest(supplied, token):
            raise HTTPException(status_code=401, detail="bad or missing token")

    auth = [Depends(authorised)]

    @app.exception_handler(HTTPException)
    async def as_json(_request, error: HTTPException):
        return JSONResponse({"error": error.detail}, status_code=error.status_code)

    @app.get("/", response_class=HTMLResponse)
    def page():
        # read per request so edits to the page show up on reload
        return PAGE.read_text(encoding="utf-8").replace(TOKEN_PLACEHOLDER, token)

    @app.get("/api/state", dependencies=auth)
    def state():
        return brain.state()

    # plain `def` routes run in FastAPI's thread pool, so a slow answer blocks
    # only its own request
    @app.post("/api/ask", dependencies=auth)
    def ask(body: AskBody):
        question = body.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="empty question")
        return brain.ask(question, via="typed")

    @app.post("/api/record", dependencies=auth)
    def record(body: RecordBody):
        if body.recording is None:
            brain.toggle_recording()
        elif body.recording:
            brain.start_recording()
        else:
            brain.stop_recording()
        return {"recording": brain.recording}

    @app.post("/api/stop", dependencies=auth)
    def stop():
        brain.stop_speaking()
        return {"ok": True}

    @app.post("/api/replay", dependencies=auth)
    def replay(body: ReplayBody):
        brain.replay(body.index)
        return {"ok": True}

    @app.post("/api/sync", dependencies=auth)
    def sync():
        import threading
        threading.Thread(target=brain.sync, kwargs={"requested": True}, daemon=True).start()
        return {"ok": True}

    @app.get("/api/events", dependencies=auth)
    async def events(request: Request):
        """Server-sent events: the full state once on connect, then an event for
        every change. Clients render from these instead of polling."""
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        # the brain publishes from worker threads; hop onto this request's loop
        unsubscribe = brain.subscribe(lambda event: loop.call_soon_threadsafe(queue.put_nowait, event))
        first = {"type": "hello", "state": await asyncio.to_thread(brain.state)}

        async def stream():
            try:
                yield f"data: {json.dumps(first)}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                        yield f"data: {json.dumps(event)}\n\n"
                    except asyncio.TimeoutError:
                        # a comment line, so proxies and the client know it's alive
                        yield ": ping\n\n"
                    if await request.is_disconnected():
                        break
            finally:
                unsubscribe()

        return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"})

    return app
