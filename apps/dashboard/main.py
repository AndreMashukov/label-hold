"""Label Hold Dashboard BFF — single FastAPI app serving both the UI and the
/api/lots JSON endpoints, plus the /api/stream SSE feed that powers the
live UI via Firestore `on_snapshot` on lots-listen/.

The frontend lives at
/ (single-page app), the JSON API at /api/lots and /api/lots/{lot_id},
and the SSE stream at /api/stream.

Auth model:
  - GET /                public (the QA actor)
  - GET /api/lots        public (read-only Firestore query)
  - GET /api/lots/{id}   public (read-only Firestore get)
  - GET /api/stream      public (SSE feed, read-only Firestore listen)
  - GET /health          public

Firestore access uses the runtime SA (lh-dashboard-runtime). We grant it
roles/datastore.user so it can read lean-db/lots-listen.
"""
from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

logger = None  # uvicorn handles logging config


app = FastAPI(title="label-hold-dashboard", version="0.2.0")

# Allow the React frontend (apps/frontend, deployed as its own
# Cloud Run service) to call the dashboard BFF cross-origin. The dashboard
# SPA at "/" still uses same-origin so this is just a safety net. We allow
# any origin because the demo is unauthenticated by design (see /api/run
# comment below).
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)


def _firestore_client():
    """Lazy import + lazy client so /health is fast on cold start."""
    from google.cloud import firestore
    db_id = os.environ.get("FIRESTORE_DATABASE", "lean-db")
    return firestore.AsyncClient(database=db_id), db_id


def _lean_collection() -> str:
    return os.environ.get("FIRESTORE_LEAN_COLLECTION", "lots-listen")


async def _read_lean_lots(limit: int = 50) -> list[dict[str, Any]]:
    """List lots-listen rows ordered by updated_at desc."""
    from google.cloud import firestore
    client, _ = _firestore_client()
    coll = client.collection(_lean_collection())
    # Firestore "order by" requires the field exists; use the index ts desc.
    q = coll.order_by("ts", direction=firestore.Query.DESCENDING).limit(limit)
    out: list[dict[str, Any]] = []
    async for snap in q.stream():
        d = snap.to_dict() or {}
        d["lot_id"] = d.get("lot_id") or snap.id
        # Firestore timestamp -> ISO string for JSON
        ts = d.get("updated_at")
        if hasattr(ts, "isoformat"):
            d["updated_at"] = ts.isoformat()
        out.append(d)
    return out


async def _read_lean_lot(lot_id: str) -> dict[str, Any] | None:
    client, _ = _firestore_client()
    snap = await client.collection(_lean_collection()).document(lot_id).get()
    if not snap.exists:
        return None
    d = snap.to_dict() or {}
    d["lot_id"] = d.get("lot_id") or snap.id
    ts = d.get("updated_at")
    if hasattr(ts, "isoformat"):
        d["updated_at"] = ts.isoformat()
    return d


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": os.environ.get("SERVICE_NAME", "dashboard"),
        "firestore_db": os.environ.get("FIRESTORE_DATABASE", "lean-db"),
    }


@app.post("/api/run")
async def api_run(
    lot_id: str = Form(...),
    spec: UploadFile = File(...),
    coa: UploadFile = File(...),
    label: UploadFile = File(...),
) -> JSONResponse:
    """Proxy multipart upload to adk-runtime's /demo/run.

    The dashboard BFF is the user-facing entry point. The adk-runtime service
    requires OIDC, so the proxy here uses an ID token to call it. Set
    ADK_RUNTIME_URL to the base URL of adk-runtime (no trailing slash).
    """
    target = os.environ.get("ADK_RUNTIME_URL", "").rstrip("/")
    if not target:
        raise HTTPException(
            status_code=500,
            detail="ADK_RUNTIME_URL not configured on the dashboard service",
        )

    # Build the upstream form (httpx supports file uploads via files kwarg).
    files = {
        "spec": (spec.filename or "spec", await spec.read(), spec.content_type or "application/octet-stream"),
        "coa":  (coa.filename  or "coa",  await coa.read(),  coa.content_type  or "application/octet-stream"),
        "label":(label.filename or "label",await label.read(),label.content_type or "application/octet-stream"),
    }
    data = {"lot_id": lot_id}

    # adk-runtime currently allows allUsers (the demo is unauthenticated by
    # design — the gate's only protection is the lot_id convention). When we
    # tighten adk-runtime to OIDC, swap this for an ID-token fetch like:
    #     from google.oauth2 import id_token
    #     id_tok = id_token.fetch_id_token(Request(), audience)
    #     headers = {"Authorization": f"Bearer {id_tok}"}
    headers = {}

    try:
        async with httpx.AsyncClient(timeout=180.0) as client:
            r = await client.post(f"{target}/demo/run", data=data, files=files, headers=headers)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"upstream error: {e!r}") from e

    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text[:1000])

    return JSONResponse(r.json())


@app.get("/api/lots")
async def api_lots() -> JSONResponse:
    """Return all known lots ordered most-recent first."""
    rows = await _read_lean_lots(limit=50)
    return JSONResponse({"count": len(rows), "lots": rows})


@app.get("/api/lots/{lot_id}")
async def api_lot(lot_id: str) -> JSONResponse:
    doc = await _read_lean_lot(lot_id)
    if doc is None:
        return JSONResponse({"lot_id": lot_id, "found": False}, status_code=404)
    return JSONResponse({"lot_id": lot_id, "found": True, **doc})


def _serialize_lean_row(snap) -> dict[str, Any]:
    """Convert a Firestore DocumentSnapshot from lots-listen into a JSON dict."""
    d = snap.to_dict() or {}
    d["lot_id"] = d.get("lot_id") or snap.id
    ts = d.get("updated_at")
    if hasattr(ts, "isoformat"):
        d["updated_at"] = ts.isoformat()
    return d


@app.get("/api/stream")
async def api_stream(req: Request) -> StreamingResponse:
    """SSE feed of every change to lots-listen/ in the lean-db Firestore.

    Wire format (one SSE event per Firestore change):

      event: snapshot
      data: {"rows": [{...lot...}, ...]}

      event: change
      data: {"lot_id": "...", "row": {...}}

    Each `change` event is a single upsert or delete from the Firestore
    `on_snapshot` callback. The `snapshot` event is sent once at the start
    of the connection so a fresh subscriber gets a complete view without
    having to also fetch /api/lots.

    Auth: public (the lean read model contains no PII; verdict + undeclared
    allergen list + summary is intended for QA reviewers). The runtime SA
    has roles/datastore.user on the project so listen() works.
    """

    async def event_stream():
        loop = asyncio.get_running_loop()
        # Queue for Firestore deltas marshalled from the watch thread back
        # into the asyncio loop. Capped so a runaway producer can't OOM us.
        delta_q: asyncio.Queue[str] = asyncio.Queue(maxsize=256)
        initial_rows: list[dict[str, Any]] = []
        initial_ready = asyncio.Event()

        def _on_snapshot(doc_snapshots, changes, read_time):
            """Firestore listen() callback. Runs on a background thread; we
            marshal back to the asyncio loop via call_soon_threadsafe for
            the initial snapshot, and put_nowait on the delta queue for
            individual changes (which is thread-safe).
            """
            try:
                if not initial_ready.is_set():
                    rows = [_serialize_lean_row(s) for s in doc_snapshots]
                    initial_rows.clear()
                    initial_rows.extend(rows)
                    loop.call_soon_threadsafe(initial_ready.set)
                for change in changes:
                    payload: dict[str, Any] = {
                        "lot_id": change.document.id,
                        "type": change.type.name,  # ADDED | MODIFIED | REMOVED
                    }
                    if change.type.name != "REMOVED":
                        payload["row"] = _serialize_lean_row(change.document)
                    data = json.dumps(payload, default=str)
                    delta_q.put_nowait(data)
            except Exception as e:  # noqa: BLE001
                # Don't crash the watch thread on a single bad snapshot.
                # The next snapshot will fire and recover.
                import traceback
                print(f"sse on_snapshot error: {e!r}\n{traceback.format_exc()}", flush=True)

        # The Firestore SDK exposes on_snapshot only on the sync client.
        # We start the watch on this thread (it returns immediately and
        # fires _on_snapshot on a worker thread for each change). The
        # asyncio generator below yields SSE events to the client.
        from google.cloud import firestore as fs_sync
        sync_client = fs_sync.Client(database=os.environ.get("FIRESTORE_DATABASE", "lean-db"))
        sync_q = (
            sync_client.collection(_lean_collection())
            .order_by("updated_at", direction=fs_sync.Query.DESCENDING)
            .limit(50)
        )
        watch = sync_q.on_snapshot(_on_snapshot)  # noqa: F841 (kept alive by closure)

        # Wait for the initial snapshot, then stream it once.
        await initial_ready.wait()
        yield (
            "event: snapshot\n"
            f"data: {json.dumps({'rows': initial_rows}, default=str)}\n\n"
        )

        try:
            while True:
                if await req.is_disconnected():
                    break
                try:
                    data = await asyncio.wait_for(delta_q.get(), timeout=15.0)
                    yield f"event: change\ndata: {data}\n\n"
                except asyncio.TimeoutError:
                    # Heartbeat keeps proxies (and Cloud Run's idle timeout)
                    # from buffering/closing the connection.
                    yield ": keepalive\n\n"
        finally:
            # Unsubscribe from the watch. `watch.close()` cancels the
            # underlying Firestore Listen RPC and frees the worker thread.
            try:
                watch.close()
            except Exception:  # noqa: BLE001
                pass

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering when behind a proxy
            "Connection": "keep-alive",
        },
    )


# --- Static UI (served from /app/public) ---
PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
if os.path.isdir(PUBLIC_DIR):
    app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")

# --- Bundled demo fixtures (Proof of Action harness) ---
# Path resolution: the dashboard image bundles /app/public/ but fixtures live
# at /opt/data/serverless/label-hold/fixtures. In Cloud Run the dashboard
# image doesn't carry fixtures. We expose a tiny static-style route that
# 404s cleanly if not found — for the demo harness, copy fixtures into the
# dashboard image at build time (see Dockerfile COPY step).
FIXTURES_DIR = os.environ.get("DASHBOARD_FIXTURES_DIR", "/app/fixtures")
if os.path.isdir(FIXTURES_DIR):
    app.mount("/fixtures", StaticFiles(directory=FIXTURES_DIR), name="fixtures")


@app.get("/", response_class=HTMLResponse)
async def index() -> HTMLResponse:
    """Serve the dashboard SPA shell. The JS hits /api/lots for live data."""
    with open(os.path.join(PUBLIC_DIR, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


@app.get("/proof-of-action.html", response_class=HTMLResponse)
async def proof_of_action() -> HTMLResponse:
    """The demo harness: drives two lots through /api/run and shows
    terminal log + dashboard verdicts + Firestore raw reads in one pane."""
    with open(os.path.join(PUBLIC_DIR, "proof-of-action.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        log_level="info",
    )
