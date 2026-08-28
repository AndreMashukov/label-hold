"""adk-runtime: Label Hold ADK Control Service.

Mounts the ADK graph from apps/adk-runtime/agents/ via get_fast_api_app
and exposes three BFF routes used by the dashboard + one Eventarc sink:

  GET  /health             liveness probe (no auth)
  POST /demo/run           multipart upload: spec, coa, label files + lot_id
                           kicks the agent off and returns the verdict.
  GET  /demo/lots/{id}     read lots/{id} from the system of record
  POST /__eventarc/publish CloudEvent sink for the Firestore→bus CDC trigger.
                           Invoked by Eventarc only; no public access.
"""
from __future__ import annotations

import json
import os
import uuid
from typing import Any

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse

from google.adk.cli.fast_api import get_fast_api_app
from google.genai import types as genai_types

# ADK + label_hold imports happen after env validation below.
APP_DIR = os.path.dirname(os.path.abspath(__file__))


def _env_or_die(name: str, default: str | None = None) -> str:
    val = os.environ.get(name, default)
    if val is None:
        raise RuntimeError(f"{name} must be set (Secret Manager or env var)")
    return val


def _validate_env() -> None:
    """Sanity-check the env before ADK tries to construct anything.

    On Cloud Run these come from Secret Manager / project metadata.
    """
    _env_or_die("GCP_PROJECT_ID")
    _env_or_die("FIRESTORE_DATABASE", "lots-db")
    # GEMINI_API_KEY is optional (canned stubs). Warn but do not crash.
    if not os.environ.get("GEMINI_API_KEY"):
        # Visible in /health so operators know they are on stub mode.
        os.environ["LABEL_HOLD_STUB_MODE"] = "1"


# Build the ADK FastAPI app. agents_dir points at ./agents/ so ADK's loader
# picks up release_pipeline/agent.py and its `root_agent` symbol.
_validate_env()
_adk_app = get_fast_api_app(
    agents_dir=os.path.join(APP_DIR, "agents"),
    session_service_uri=f"sqlite:///{os.environ.get('SESSION_DB_PATH', '/tmp/sessions.db')}",
    artifact_service_uri=None,  # InMemoryArtifactService by default
    web=False,
    allow_origins=["*"],  # Demo only. Tighten for production.
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8080")),
    auto_create_session=True,
)

app: FastAPI = _adk_app

# Replace ADK's default /health with our richer one that reports stub_mode
# and Firestore DB. FastAPI routes are matched in registration order; removing
# ADK's bare `/health` and re-adding ours makes ours win.
app.router.routes = [
    r for r in app.router.routes
    if not (getattr(r, "path", None) == "/health" and "GET" in getattr(r, "methods", set()))
]


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": os.environ.get("SERVICE_NAME", "adk-runtime"),
        "stub_mode": os.environ.get("LABEL_HOLD_STUB_MODE", "0") == "1",
        "gemini_key_present": bool(os.environ.get("GEMINI_API_KEY")),
        "firestore_db": os.environ.get("FIRESTORE_DATABASE", "lots-db"),
    }


@app.post("/demo/run")
async def demo_run(
    lot_id: str = Form(...),
    spec: UploadFile = File(...),
    coa: UploadFile = File(...),
    label: UploadFile = File(...),
) -> JSONResponse:
    """Drive the real ADK graph end-to-end via a self-call to /run.

    Constructs a /run-shaped payload (state_delta carries lot_id + file-size
    sanity checks), forwards it to the same service's /run endpoint over
    HTTP, and waits for the full event stream. The graph's poster
    LlmAgent calls write_lot_status_tool inside the runner, which writes
    lots/{lot_id} to Firestore. The Firestore Eventarc trigger on that
    collection is the sole publisher of the lot.* bus event — see
    label_hold/cdc.py:handle_eventarc_publish.

    The auth is "allow_unauthenticated" on this service, so no token dance.
    """
    if not lot_id.strip():
        raise HTTPException(status_code=400, detail="lot_id required")

    spec_bytes = await spec.read()
    coa_bytes = await coa.read()
    label_bytes = await label.read()

    # Build a multi-part user message that ADK will pass to the parallel
    # ingest agents. Each doc gets a [SPEC]/[CoA]/[LABEL] text prefix so the
    # per-agent instructions can target it without ambiguity. Non-text
    # uploads (PDF, image) are attached as base64 inline_data with the
    # prefix as a separate text Part so the model still sees the label.
    #
    # Wire format on /run: parts is a list of {text|inline_data|file_data}.
    # ADK accepts either camelCase ({"text": "..."}) or snake_case
    # ({"inline_data": {...}}) keys. We send the camelCase shapes that the
    # Pydantic types.Content expects.
    parts: list[dict[str, Any]] = []
    for label_name, doc_bytes, mime in (
        ("SPEC", spec_bytes, spec.content_type or "text/plain"),
        ("CoA", coa_bytes, coa.content_type or "text/plain"),
        ("LABEL", label_bytes, label.content_type or "text/plain"),
    ):
        if mime.startswith("text/"):
            try:
                text = doc_bytes.decode("utf-8")
            except UnicodeDecodeError:
                # Fall back to latin-1 (binary-safe) and add a marker so the
                # model at least knows there was content.
                text = doc_bytes.decode("latin-1")
            parts.append({"text": f"[{label_name}]\n{text}"})
        else:
            import base64 as _b64
            parts.append({
                "inline_data": {
                    "mime_type": mime,
                    "display_name": f"{label_name}-{lot_id}",
                    "data": _b64.b64encode(doc_bytes).decode("ascii"),
                },
            })
            # Also add the labelled marker so the per-agent instruction
            # "Look at the [LABEL] part" still makes sense for binary inputs.
            parts.append({"text": f"[{label_name} attached as {mime}]"})

    # All three documents present is the happy path; missing docs trigger the
    # incomplete_packet verdict on the matcher.
    state_delta = {
        "lot_id": lot_id,
        "spec_bytes": len(spec_bytes),
        "coa_bytes": len(coa_bytes),
        "label_bytes": len(label_bytes),
        "all_documents_present": bool(spec_bytes and coa_bytes and label_bytes),
    }

    payload = {
        "app_name": "release_pipeline",
        "user_id": "demo",
        "session_id": f"demo-{lot_id}",
        "new_message": {
            "role": "user",
            "parts": parts,
        },
        "state_delta": state_delta,
    }

    # Self-call via httpx against our own /run endpoint. This is the same
    # code path that the cloud shell uses when debugging the graph; we just
    # avoid the auth dance by reusing the running service.
    service_url = os.environ.get("ADK_RUNTIME_URL") or (
        f"http://localhost:{os.environ.get('PORT', '8080')}"
    )

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(f"{service_url}/run", json=payload)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"self-call to /run failed: {e!r}",
        ) from e

    if r.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"/run returned {r.status_code}: {r.text[:500]}",
        )

    events = r.json()

    # Extract the verdict from the critic's output. Fall back to "held" if we
    # cannot parse the critic's payload (defensive).
    verdict = "held"
    for ev in events:
        if ev.get("author") == "critic":
            text_parts = ev.get("content", {}).get("parts", [])
            for part in text_parts:
                try:
                    payload_obj = json.loads(part.get("text", "{}"))
                    if payload_obj.get("verdict") in ("held", "released"):
                        verdict = payload_obj["verdict"]
                except (json.JSONDecodeError, TypeError):
                    continue

    # Extract the poster's write_id from the tool-call payload. The poster's
    # stub returns an LlmResponse whose content is a JSON-text Part:
    # {"tool_result": "write_lot_status", "payload": {...}}. The bus_message_id
    # is no longer here — under the CDC architecture the bus event is
    # published by the Firestore Eventarc trigger, not by the agent.
    write_id = None
    for ev in events:
        if ev.get("author") == "poster":
            for part in ev.get("content", {}).get("parts", []):
                try:
                    obj = json.loads(part.get("text", "{}"))
                    if obj.get("tool_result") == "write_lot_status":
                        payload_obj = obj.get("payload", {})
                        if not isinstance(payload_obj, dict):
                            try:
                                payload_obj = json.loads(payload_obj)
                            except (json.JSONDecodeError, TypeError):
                                payload_obj = {}
                        write_id = payload_obj.get("write_id")
                except (json.JSONDecodeError, TypeError):
                    continue

    return JSONResponse({
        "lot_id": lot_id,
        "status": verdict,
        "events": len(events),
        "write_id": write_id,
        "sizes": {
            "spec": len(spec_bytes),
            "coa": len(coa_bytes),
            "label": len(label_bytes),
        },
    })


@app.get("/demo/lots/{lot_id}")
async def get_lot(lot_id: str) -> JSONResponse:
    """Read lots/{lot_id}. Used by the dashboard for direct fetch."""
    from label_hold.lots import get_lot_status
    doc = await get_lot_status(lot_id)
    if doc is None:
        return JSONResponse({"lot_id": lot_id, "found": False}, status_code=404)
    return JSONResponse({"lot_id": lot_id, "found": True, **doc})


@app.post("/__eventarc/publish")
async def eventarc_publish(req: Request) -> JSONResponse:
    """CloudEvent sink for the Firestore Eventarc trigger on lots/{lot_id}.

    The trigger is the sole producer of lot.* bus events. It fires
    exactly once per committed write, delivers a CloudEvent envelope to
    this path, and we forward to label_hold.bus.publish_lot_event after
    re-reading the row from Firestore (avoids trusting protobuf payload
    contents). See label_hold/cdc.py for the design notes.

    Auth: this path is invoked only by the Eventarc SA via OIDC push.
    The bff-service module grants `roles/run.invoker` on the
    `eventarc_sa_email` so the trigger can reach it. No public access.
    """
    from label_hold.cdc import handle_eventarc_publish
    return JSONResponse(await handle_eventarc_publish(req))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        log_level="info",
    )
