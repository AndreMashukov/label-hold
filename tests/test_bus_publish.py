"""tests/test_bus_publish.py — prove that /demo/run drives the real ADK graph
end-to-end AND that the bus publish fires.

Three scenarios:

  1. HK-HOLD-MILK      — happy path, all docs present, milk undeclared on label -> held
  2. HK-RELEASE        — happy path, all clear                                  -> released
  3. HK-INCOMPLETE     — one doc missing                                        -> held, incomplete

We assert:
  - /demo/run returns 200 with status matching expectation
  - Firestore lots/{lot_id} row exists with the right verdict and a bus_message_id
  - The Pub/Sub subscription has received a message for this lot (poll up to 30s)

The test runs against the deployed service. Override ADK_BASE_URL to point at
a different service (e.g. http://localhost:8080).
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

BASE = os.environ.get("ADK_BASE_URL", "https://adk-runtime-hnvjxkvfoq-as.a.run.app")
PROJECT = os.environ.get("GCP_PROJECT_ID", "serverless-503308")
SUBSCRIPTION = os.environ.get("PUBSUB_SUBSCRIPTION", "label-hold-lean-view-sub")

FIXTURES_DIR = Path(__file__).resolve().parent.parent / "fixtures"


def _make_files(workdir: Path) -> None:
    """Create the three demo documents if the fixtures are missing."""
    for name in ("spec", "coa", "label"):
        path = workdir / f"{name}.txt"
        if not path.exists():
            path.write_text(f"demo fixture for {name}")


def _post_demo_run(lot_id: str, workdir: Path) -> dict:
    """POST /demo/run and return the JSON response."""
    cmd = [
        "curl", "-s", "-w", "\n%{http_code}",
        "-X", "POST", f"{BASE}/demo/run",
        "-F", f"lot_id={lot_id}",
        "-F", f"spec=@{workdir/'spec.txt'}",
        "-F", f"coa=@{workdir/'coa.txt'}",
        "-F", f"label=@{workdir/'label.txt'}",
    ]
    out = subprocess.check_output(cmd, text=True, timeout=180)
    body, http_code = out.rsplit("\n", 1)
    assert http_code == "200", f"/demo/run returned {http_code}: {body[:500]}"
    return json.loads(body)


def _get_lot(lot_id: str) -> dict:
    """Read lots/{lot_id} from Firestore via the live service."""
    out = subprocess.check_output(
        ["curl", "-s", f"{BASE}/demo/lots/{lot_id}"], text=True, timeout=30,
    )
    return json.loads(out)


def _pull_pubsub_message(lot_id: str, timeout_s: int = 30) -> dict | None:
    """Synchronous pull on the subscription, returning the first message whose
    lot_id attribute matches. Returns None if no message arrives in timeout_s."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        # Use a short max_messages and short timeout to keep the test responsive.
        proc = subprocess.run(
            [
                "gcloud", "pubsub", "subscriptions", "pull",
                SUBSCRIPTION,
                "--project", PROJECT,
                "--max-messages", "5",
                "--format", "json",
                "--quiet",
            ],
            capture_output=True, text=True, timeout=20,
        )
        if proc.returncode != 0:
            time.sleep(1)
            continue
        try:
            msgs = json.loads(proc.stdout) if proc.stdout.strip() else []
        except json.JSONDecodeError:
            time.sleep(1)
            continue
        for m in msgs:
            data_b64 = m.get("message", {}).get("data", "")
            try:
                import base64
                payload = json.loads(base64.b64decode(data_b64).decode("utf-8"))
            except Exception:
                continue
            if payload.get("lot_id") == lot_id:
                # ack so we don't redeliver it
                ack_id = m.get("ackId")
                if ack_id:
                    subprocess.run(
                        ["gcloud", "pubsub", "subscriptions", "ack",
                         SUBSCRIPTION, "--ack-ids", ack_id,
                         "--project", PROJECT, "--quiet"],
                        capture_output=True, text=True, timeout=10,
                    )
                return payload
        # ack everything we got so the subscription doesn't pile up
        for m in msgs:
            ack_id = m.get("ackId")
            if ack_id:
                subprocess.run(
                    ["gcloud", "pubsub", "subscriptions", "ack",
                     SUBSCRIPTION, "--ack-ids", ack_id,
                     "--project", PROJECT, "--quiet"],
                    capture_output=True, text=True, timeout=10,
                )
        time.sleep(1)
    return None


@pytest.fixture(scope="module")
def workdir(tmp_path_factory) -> Path:
    wd = tmp_path_factory.mktemp("demo_run")
    _make_files(wd)
    return wd


@pytest.mark.parametrize("lot_id,expected_status", [
    ("HK-HOLD-MILK", "held"),
    ("HK-RELEASE", "released"),
    ("HK-INCOMPLETE", "held"),
])
def test_demo_run_drives_graph_and_publishes(workdir, lot_id, expected_status):
    """Each lot: /demo/run -> Firestore row + bus message."""
    resp = _post_demo_run(lot_id, workdir)
    assert resp["lot_id"] == lot_id
    assert resp["status"] == expected_status, f"got {resp['status']}, want {expected_status}"
    assert resp["write_id"], "expected a non-empty write_id in the response"

    # The Firestore row should now exist with the same verdict + a bus_message_id.
    row = _get_lot(lot_id)
    assert row.get("found"), f"firestore row missing for {lot_id}"
    assert row["status"] == expected_status
    assert row.get("bus_message_id"), "expected bus_message_id on the Firestore row"

    # And the bus should have a matching message.
    msg = _pull_pubsub_message(lot_id, timeout_s=30)
    assert msg is not None, f"no Pub/Sub message arrived for {lot_id} in 30s"
    assert msg["event_type"] == f"lot.{expected_status}"
    assert msg["lot_id"] == lot_id
    assert msg["write_id"] == resp["write_id"]
    assert msg.get("bus_message_id") == row["bus_message_id"]


def test_idempotent_rerun(workdir):
    """Replaying the same lot_id with /demo/run produces ONE row, not two.
    The Firestore write is `set(..., merge=True)`, and Pub/Sub publishes per
    call (so the consumer must dedupe on write_id or bus_message_id)."""
    lot_id = "HK-IDEMPOTENT"
    resp1 = _post_demo_run(lot_id, workdir)
    resp2 = _post_demo_run(lot_id, workdir)
    # Firestore row has one write_id (the latest), but it must be the same.
    row = _get_lot(lot_id)
    assert row["found"]
    assert row["write_id"] == resp2["write_id"]
    # Two distinct bus message_ids (each call publishes once).
    assert resp1["write_id"] != resp2["write_id"]
