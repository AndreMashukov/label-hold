"""Firestore lots/{lot_id} writer.

`lots/` is the system of record. Only the ADK agent (via write_lot_status)
writes here. The leanview-consumer watches the bus event and writes the
denormalized snapshot to `lots-listen/` separately. See PLAN §2.
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

    Returns the merged document so the caller can confirm the write.
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

    # Publish a lot.* event to the bus so the leanview-consumer can materialize
    # the lean read model. Bus failures are swallowed inside publish_lot_event;
    # the Firestore write is the system of record and must not roll back.
    bus_message_id: str | None = None
    try:
        from label_hold.bus import publish_lot_event
        bus_message_id = publish_lot_event(
            lot_id=lot_id,
            status=status,
            undeclared=payload["undeclared"],
            reason=reason,
            write_id=payload["write_id"],
            missing_document=bool(missing_document),
            summary=str(summary or ""),
        )
        if bus_message_id:
            # Stamp the bus message_id into the Firestore row so the lean view
            # can dedupe replays without an extra round-trip to Pub/Sub.
            await doc_ref.set({"bus_message_id": bus_message_id}, merge=True)
            payload["bus_message_id"] = bus_message_id
    except Exception as e:  # noqa: BLE001  (defense-in-depth; publish_lot_event swallows)
        logger.exception("bus.publish wrapper failed lot=%s: %r", lot_id, e)

    return payload


async def get_lot_status(lot_id: str) -> dict[str, Any] | None:
    """Read lots/{lot_id}. Returns None if missing."""
    client = _client()
    doc_ref = client.collection(_collection()).document(lot_id)
    snap = await doc_ref.get()
    return snap.to_dict() if snap.exists else None
