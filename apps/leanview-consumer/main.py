"""lean-view-consumer: Pub/Sub push -> Firestore lots-listen materializer.

Takes the lot.* event published by adk-runtime's poster and
upserts a denormalized row to lots-listen/{lot_id} in the lean-db Firestore
database. The dashboard reads from lots-listen via Firestore live subscribe.

The Pub/Sub push envelope wraps our JSON payload in base64:
    {
      "message": {
        "data": "<base64 JSON>",
        "messageId": "...",
        "publishTime": "..."
      },
      "subscription": "projects/.../subscriptions/label-hold-lean-view-sub"
    }

We decode, validate the CPCQ contract (event_type, lot_id, status, write_id),
and write the lean row. Errors return non-200 so Pub/Sub retries (max 5).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
from typing import Any

from fastapi import FastAPI, HTTPException, Request

logger = logging.getLogger("leanview-consumer")
logging.basicConfig(level=logging.INFO)


# Pub/Sub retries up to 5 times on non-2xx. Returning 200 acknowledges.
# Returning 4xx drops the message (no retry). Returning 5xx retries.
# We always return 200 unless the message is malformed (400) or our
# downstream is permanently broken (500). For transient Firestore errors
# we let them bubble up as 500 so Pub/Sub retries.

LEAF_PAYLOAD_KEYS = {"event_type", "lot_id", "status", "write_id", "undeclared", "reason"}


app = FastAPI(title="label-hold-lean-view-consumer", version="0.2.0")


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": os.environ.get("SERVICE_NAME", "lean-view-consumer"),
        "stub": False,
        "firestore_db": os.environ.get("FIRESTORE_DATABASE", "lean-db"),
    }


def _firestore_client():
    """Lazy import + lazy client so /health is fast on cold start."""
    from google.cloud import firestore
    db_id = os.environ.get("FIRESTORE_DATABASE", "lean-db")
    return firestore.AsyncClient(database=db_id), db_id


async def _materialize_lot_row(payload: dict[str, Any]) -> dict[str, Any]:
    """Upsert lots-listen/{lot_id} from the bus payload.

    Idempotent on (lot_id, write_id): re-delivering the same payload just
    re-stamps updated_at. Re-delivering an OLDER payload (lower ts) is a
    no-op because we never regress write_id.
    """
    from google.cloud import firestore

    client, db_id = _firestore_client()
    collection = os.environ.get("FIRESTORE_LEAN_COLLECTION", "lots-listen")

    lot_id = str(payload["lot_id"])
    write_id = str(payload["write_id"])
    status = str(payload["status"])
    if status not in ("held", "released"):
        raise ValueError(f"unexpected status {status!r}")
    undeclared = list(payload.get("undeclared") or [])
    reason = str(payload.get("reason", ""))
    missing_document = bool(payload.get("missing_document", False))
    summary = str(payload.get("summary", ""))  # Gemma-generated, HELD-only
    bus_message_id = payload.get("bus_message_id")
    ts = payload.get("ts")

    doc_ref = client.collection(collection).document(lot_id)

    # Idempotency guard: read current write_id. If our incoming write_id is
    # older than the one already persisted, drop it. Out-of-order retries
    # from Pub/Sub should never regress state.
    snap = await doc_ref.get()
    existing = snap.to_dict() if snap.exists else {}
    existing_write_id = existing.get("write_id")
    if existing_write_id and existing_write_id != write_id:
        # If our ts is older than the stored ts, this is a stale replay.
        existing_ts = existing.get("ts") or 0
        if ts is not None and ts < existing_ts:
            logger.info(
                "stale bus payload lot=%s incoming_write_id=%s stored_write_id=%s — skipping",
                lot_id, write_id, existing_write_id,
            )
            return {"skipped": "stale", "lot_id": lot_id, "write_id": write_id}

    lean_row = {
        "lot_id": lot_id,
        "status": status,
        "undeclared": undeclared,
        "reason": reason,
        "missing_document": missing_document,
        "summary": summary,  # Gemma-generated executive summary (HELD lots only)
        "write_id": write_id,
        "ts": ts,
        "updated_at": firestore.SERVER_TIMESTAMP,
    }
    if bus_message_id:
        lean_row["bus_message_id"] = str(bus_message_id)

    await doc_ref.set(lean_row, merge=True)
    logger.info(
        "materialized lots-listen/%s status=%s write_id=%s bus_message_id=%s",
        lot_id, status, write_id, bus_message_id,
    )
    return {
        "materialized": True,
        "lot_id": lot_id,
        "write_id": write_id,
        "firestore_db": db_id,
    }


@app.post("/pubsub/push")
async def pubsub_push(req: Request) -> dict:
    """Pub/Sub push handler.

    Decodes the envelope, validates the leaf payload has the keys we need,
    and writes the lean row. Returns 200 on success, 400 on malformed input.
    Transient errors (Firestore unavailable, etc.) bubble up as 500 so
    Pub/Sub retries up to 5 times.
    """
    body = await req.body()
    try:
        envelope = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error("pubsub envelope JSON decode failed: %r", e)
        raise HTTPException(status_code=400, detail=f"envelope not JSON: {e!r}") from e

    message = envelope.get("message") or {}
    data_b64 = message.get("data") or ""
    try:
        payload_bytes = base64.b64decode(data_b64)
        payload = json.loads(payload_bytes)
    except (ValueError, json.JSONDecodeError) as e:
        logger.error("pubsub leaf payload decode failed: %r data_b64[:80]=%r", e, data_b64[:80])
        raise HTTPException(status_code=400, detail=f"leaf payload not decodable: {e!r}") from e

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="leaf payload must be a JSON object")

    missing_keys = LEAF_PAYLOAD_KEYS - set(payload.keys())
    if missing_keys:
        # Malformed: drop with 400 (Pub/Sub will NOT retry).
        raise HTTPException(
            status_code=400,
            detail=f"leaf payload missing keys {sorted(missing_keys)}",
        )

    try:
        result = await _materialize_lot_row(payload)
    except ValueError as e:
        # Schema validation — bad status, missing required field, etc.
        logger.error("payload validation failed: %r payload=%r", e, payload)
        raise HTTPException(status_code=400, detail=str(e)) from e

    return result


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", "8080")),
        log_level="info",
    )
