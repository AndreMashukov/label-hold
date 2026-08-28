"""CDC publisher: Firestore Eventarc trigger → Pub/Sub lot.* events.

This module is the publish leg of the Firestore→Pub/Sub CDC pipeline.
The trigger is created by terraform/modules/bff-service when
enable_firestore_trigger = true (see terraform/envs/dev/main.tf,
module "adk_runtime"). It delivers DocumentEventData envelopes to
the /__eventarc/publish HTTP handler in main.py, which delegates here.

Design:
- The handler is the only place that publishes lot.* events. The poster
  LlmAgent's write_lot_status_tool writes lots/{lot_id} and stops.
- Idempotency: each publish carries the same write_id that was stamped
  on the row. Pub/Sub at-least-once + the consumer's (write_id, ts)
  guard means replays collapse to a no-op.
- Failure modes: transient Pub/Sub errors return 500 so Eventarc
  retries. The row stays put; we never double-publish because the
  trigger fires exactly once per committed write.
"""
from __future__ import annotations

import logging
import time
from typing import Any
from urllib.parse import unquote

from fastapi import Request

logger = logging.getLogger(__name__)


def _parse_document_path(ce_document: str) -> str | None:
    """Extract lot_id from Eventarc CloudEvent document/subject.

    Firestore Eventarc sets `ce-subject` to `documents/lots/{lot_id}` (and
    sometimes `ce-document` to the full resource name). Either form is
    accepted. Paths may be URL-encoded.
    """
    if not ce_document:
        return None
    decoded = unquote(ce_document).lstrip("/")
    if "/documents/" in decoded:
        rel = decoded.split("/documents/", 1)[1]
    elif decoded.startswith("documents/"):
        rel = decoded[len("documents/"):]
    else:
        rel = decoded
    if not rel.startswith("lots/"):
        return None
    lot_id = rel[len("lots/"):].split("/", 1)[0]
    return lot_id or None


async def _fetch_lot(lot_id: str) -> dict[str, Any] | None:
    """Read lots/{lot_id} from the system-of-record Firestore DB.

    Returns None if the row is missing (race: trigger fired but the
    document isn't visible yet — return 500 so Eventarc retries).
    """
    from google.cloud import firestore
    import os

    db_id = os.environ.get("FIRESTORE_DATABASE", "lots-db")
    client = firestore.AsyncClient(database=db_id)
    coll = os.environ.get("FIRESTORE_COLLECTION", "lots")
    snap = await client.collection(coll).document(lot_id).get()
    if not snap.exists:
        return None
    return snap.to_dict() or {}


def _build_payload(lot_id: str, doc: dict[str, Any]) -> dict[str, Any]:
    """Build the CPCQ lot.* payload from the persisted Firestore row.

    The shape must match what leanview-consumer expects under
    LEAF_PAYLOAD_KEYS = {event_type, lot_id, status, write_id, undeclared, reason}.
    Extra fields (missing_document, summary, ts) are forwarded and the
    consumer keeps them on lots-listen/.
    """
    status = str(doc.get("status") or "")
    if status not in ("held", "released"):
        raise ValueError(f"unexpected status on lots/{lot_id}: {status!r}")
    return {
        "event_type": f"lot.{status}",
        "lot_id": lot_id,
        "status": status,
        "undeclared": list(doc.get("undeclared") or []),
        "reason": str(doc.get("reason") or ""),
        "missing_document": bool(doc.get("missing_document", False)),
        "summary": str(doc.get("summary") or ""),
        "write_id": str(doc.get("write_id") or ""),
        "ts": time.time(),
        # Provenance: was this CDC-published from the Firestore trigger?
        "cdc": True,
    }


async def handle_eventarc_publish(req: Request) -> dict[str, Any]:
    """Handle one CloudEvent delivered by the Firestore Eventarc trigger.

    CloudEvent headers (`ce-*`) carry the event metadata. The body is a
    base64-encoded `DocumentEventData` protobuf per the Eventarc envelope
    spec. We don't decode the protobuf: the `ce-document` header alone
    is enough to identify the row, and we read the latest committed
    state from Firestore directly.

    Returns 200 with the published message_id on success.
    Returns 500 on transient failure (Pub/Sub down, Firestore read
    racing the trigger) so Eventarc retries.
    Returns 400 on a malformed envelope (ce-document missing or path
    not lots/{lot_id}).
    """
    from fastapi import HTTPException

    ce_document = req.headers.get("ce-document", "")
    ce_subject = req.headers.get("ce-subject", "")
    ce_type = req.headers.get("ce-type", "")
    lot_id = _parse_document_path(ce_document) or _parse_document_path(ce_subject)
    if lot_id is None:
        logger.warning(
            "eventarc.publish skip: ce-document=%r ce-subject=%r ce-type=%s",
            ce_document, ce_subject, ce_type,
        )
        raise HTTPException(
            status_code=400,
            detail=(
                f"no lots/{{lot_id}} in ce-document={ce_document!r} "
                f"ce-subject={ce_subject!r}"
            ),
        )

    doc = await _fetch_lot(lot_id)
    if doc is None:
        # Trigger fired before the row was visible (shouldn't happen, but
        # guard anyway). Returning 500 makes Eventarc retry.
        logger.warning(
            "eventarc.publish lot=%s: lots/%s not visible yet — retry",
            lot_id, lot_id,
        )
        raise HTTPException(
            status_code=500,
            detail=f"lots/{lot_id} not visible after create event",
        )

    try:
        payload = _build_payload(lot_id, doc)
    except (KeyError, ValueError) as e:
        logger.error("eventarc.publish lot=%s payload build failed: %r", lot_id, e)
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Defer to bus.publish_lot_event. It swallows transient errors and
    # returns None on failure — we convert that into a 500 so Eventarc
    # retries. Success returns the Pub/Sub message_id.
    from label_hold.bus import publish_lot_event

    bus_message_id = publish_lot_event(
        lot_id=payload["lot_id"],
        status=payload["status"],
        undeclared=payload["undeclared"],
        reason=payload["reason"],
        write_id=payload["write_id"],
        missing_document=payload["missing_document"],
        summary=payload["summary"],
        extra={
            "cdc": True,
            "ts": payload["ts"],
            # Under the dual-write architecture the poster stamped this
            # onto lots/{lot_id} before publishing. Under CDC we don't have
            # it yet (single-shot publish); leanview-consumer's logs will
            # show bus_message_id=None on this path. The row's write_id is
            # the canonical idempotency key for downstream dedup.
            "bus_message_id": None,
        },
    )
    if not bus_message_id:
        raise HTTPException(
            status_code=500,
            detail=f"bus.publish_lot_event returned None for lot={lot_id}",
        )

    logger.info(
        "eventarc.publish ok lot=%s status=%s write_id=%s message_id=%s",
        lot_id, payload["status"], payload["write_id"], bus_message_id,
    )
    return {
        "published": True,
        "lot_id": lot_id,
        "write_id": payload["write_id"],
        "message_id": bus_message_id,
        "ce_type": ce_type,
    }


__all__ = ["handle_eventarc_publish"]
