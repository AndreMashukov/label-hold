"""Pub/Sub publish helper for lot-release events.

The poster's write_lot_status tool mutates Firestore AND publishes a
`lot.held` / `lot.released` event to the `label-hold-events` topic so the
leanview-consumer can materialize the lean read model. This file is the only
place that owns the publish logic; lots.py calls publish_lot_event() and does
not import google-cloud-pubsub directly.

Design notes:
- One synchronous publish per call (publisher.publish().result(timeout=5)).
  Async publisher is over-engineering for a low-rate gate; we publish at most
  a few events per minute.
- Errors are caught and logged but never propagated. A Pub/Sub outage must
  not fail the Firestore write (the system of record wins; the read model
  will catch up via replay if we add one later).
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
        # Never fail the surrounding Firestore write because the bus is down.
        logger.exception(
            "bus.publish FAILED lot=%s status=%s err=%r",
            lot_id, status, e,
        )
        return None


__all__ = ["publish_lot_event"]
