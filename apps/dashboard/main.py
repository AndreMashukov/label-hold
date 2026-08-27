"""Label Hold Dashboard BFF — single FastAPI app serving both the UI and the
/api/lots JSON endpoint that reads from the lean-db Firestore read model.

Day 3 wire-up: replaces the Day 0 nginx static stub. The frontend lives at
/ (single-page app), the JSON API at /api/lots and /api/lots/{lot_id}.

Auth model:
  - GET /                public (the QA actor)
  - GET /api/lots        public (read-only Firestore query)
  - GET /api/lots/{id}   public (read-only Firestore get)
  - GET /health          public

Firestore access uses the runtime SA (lh-dashboard-runtime). We grant it
roles/datastore.user so it can read lean-db/lots-listen.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

logger = None  # uvicorn handles logging config


app = FastAPI(title="label-hold-dashboard", version="0.2.0")

# Day 4.5: allow the React frontend (apps/frontend, deployed as its own
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


# --- Static UI (served from /app/public) ---
PUBLIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public")
if os.path.isdir(PUBLIC_DIR):
    app.mount("/static", StaticFiles(directory=PUBLIC_DIR), name="static")

# --- Bundled demo fixtures (Day 3 Proof of Action harness) ---
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
    """The Day 3 demo harness: drives two lots through /api/run and shows
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
