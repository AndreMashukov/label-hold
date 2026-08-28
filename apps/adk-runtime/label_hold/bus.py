"""Pub/Sub publish helper for lot-release events.

The Firestore Eventarc trigger on `lots/{lot_id}` is the sole producer of
`lot.held` / `lot.released` events. The poster writes Firestore only.
`label_hold/cdc.py` handles `/__eventarc/publish` and calls
`publish_lot_event()` here. This file is the only place that imports
`google-cloud-pubsub`.

Design notes:
- One synchronous publish per call (publisher.publish().result(timeout=5)).
  Async publisher is over-engineering for a low-rate gate; we publish at most
  a few events per minute.
- `publish_lot_event` logs errors and returns None instead of raising. The
  CDC handler turns None into HTTP 500 so Eventarc retries. The Firestore
  write already committed in a different process; a bus outage must not
  roll it back.
- Topic name comes from EVENTHUB_TOPIC env var (set on the Cloud Run service
  by Terraform). Defaults to "label-hold-events" for local dev.
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)


def _project_id() -> str:
    return os.environ.get("GCP_PROJECT_ID", "serverless-503308")


def _topic_name() -> str:
    """Topic ID or full resource path. Resolved against the project."""
    name = os.environ.get("EVENTHUB_TOPIC", "label-hold-events")
    if name.startswith("projects/"):
        return name
    return f"projects/{_project_id()}/topics/{name}"


def _publisher():
    """Lazy-import google-cloud-pubsub so tests can stub the module out."""
    from google.cloud import pubsub_v1

    return pubsub_v1.PublisherClient()


def publish_lot_event(
    *,
    lot_id: str,
    status: str,
    undeclared: list[str] | tuple[str, ...],
    reason: str,
    write_id: str,
    missing_document: bool = False,
    summary: str = "",
    extra: dict[str, Any] | None = None,
) -> str | None:
    """Publish a lot.* event to the bus. Returns the Pub/Sub message_id on ack,
    or None if the publish failed (errors are logged, never raised).

    The payload shape is the CPCQ contract: downstream consumers must be able
    to deserialize this and write a denormalized row to lots-listen/{lot_id}.
    """
    payload: dict[str, Any] = {
        "event_type": f"lot.{status}",
        "lot_id": lot_id,
        "status": status,
        "undeclared": list(undeclared or []),
        "reason": reason,
        "missing_document": bool(missing_document),
        "write_id": write_id,
        "summary": str(summary or ""),  # Gemma-generated, only set on HELD
        "ts": time.time(),
    }
    if extra:
        # shallow-merge so caller-provided keys can override (carefully)
        payload.update(extra)

    try:
        client = _publisher()
        data = json.dumps(payload, default=str).encode("utf-8")
        future = client.publish(_topic_name(), data=data)
        message_id = future.result(timeout=5)
        logger.info(
            "bus.publish ok lot=%s status=%s message_id=%s",
            lot_id, status, message_id,
        )
        return message_id
    except Exception as e:  # noqa: BLE001
        # CDC handler maps None to HTTP 500 so Eventarc retries. Do not raise
        # here; the lots/ write already landed in another process.
        logger.exception(
            "bus.publish FAILED lot=%s status=%s err=%r",
            lot_id, status, e,
        )
        return None


__all__ = ["publish_lot_event"]
