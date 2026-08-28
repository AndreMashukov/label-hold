"""Firestore lots/{lot_id} writer.

`lots/` is the system of record. Only the ADK agent (via write_lot_status)
writes here. The Firestore Eventarc trigger on this collection is the sole
publisher of lot.* events to the bus (see terraform/modules/bff-service,
enable_firestore_trigger = true on the adk-runtime module). The trigger
delivers DocumentEventData to /__eventarc/publish on this same service,
which publishes lot.held / lot.released to label-hold-events.

Splitting the write and the publish means the write is never lost or
double-counted: it happens in one Firestore set, and the trigger fires
exactly once per committed write. The publish call lives in a separate
process and is retried by Pub/Sub on transient failure.

The leanview-consumer is the only thing that writes lots-listen/. The
dashboard BFF reads lots-listen via Firestore listen() (see /api/stream).
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import uuid
from typing import Any

from google.cloud import firestore
from google.cloud.firestore_v1 import AsyncClient

logger = logging.getLogger(__name__)


def _client() -> AsyncClient:
    """Firestore client targeting the lots database.

    `FIRESTORE_DATABASE` env var selects the DB. Defaults to `lots-db` per PLAN §5.
    """
    db_id = os.environ.get("FIRESTORE_DATABASE", "lots-db")
    return firestore.AsyncClient(database=db_id)


def _collection() -> str:
    return os.environ.get("FIRESTORE_COLLECTION", "lots")


async def write_lot_status(
    *,
    lot_id: str,
    status: str,
    undeclared: list[str] | tuple[str, ...] | None = None,
    reason: str = "",
    missing_document: bool = False,
    spec: dict[str, Any] | None = None,
    coa: dict[str, Any] | None = None,
    label: dict[str, Any] | None = None,
    summary: str = "",
) -> dict[str, Any]:
    """Upsert lots/{lot_id}. Idempotent on lot_id: re-running produces one row.

    Returns the merged document so the caller can confirm the write. The
    Firestore write is the ONLY side effect of this function — no inline
    bus publish, no second Firestore stamp. The Firestore Eventarc trigger
    is the sole producer of bus events for this collection; see the module
    docstring above.
    """
    if status not in ("held", "released"):
        raise ValueError(f"status must be 'held' or 'released', got {status!r}")

    client = _client()
    doc_ref = client.collection(_collection()).document(lot_id)
    payload = {
        "lot_id": lot_id,
        "status": status,
        "undeclared": list(undeclared or []),
        "reason": reason,
        "missing_document": bool(missing_document),
        "spec": spec,
        "coa": coa,
        "label": label,
        "summary": str(summary or ""),  # Gemma-generated executive summary (held lots)
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "write_id": uuid.uuid4().hex,  # unique per write; lets the bus dedupe
    }
    # `set(..., merge=True)` is the idempotency guarantee from PLAN §10 risks.
    await doc_ref.set(payload, merge=True)
    logger.info("lots.write ok lot=%s status=%s write_id=%s", lot_id, status, payload["write_id"])
    return payload


async def get_lot_status(lot_id: str) -> dict[str, Any] | None:
    """Read lots/{lot_id}. Returns None if missing."""
    client = _client()
    doc_ref = client.collection(_collection()).document(lot_id)
    snap = await doc_ref.get()
    return snap.to_dict() if snap.exists else None
