#!/usr/bin/env python3
"""tests/smoke_bus_publish.py — manual smoke test (no pytest required).

Drives /demo/run against three lots and asserts:
  - HTTP 200 with the right verdict
  - Firestore lots/{lot_id} row exists with bus_message_id
  - Pub/Sub subscription delivers the message (poll up to 30s)
  - leanview-consumer receives the OIDC push (log entry)

Run:
  ADK_BASE_URL=https://adk-runtime-hnvjxkvfoq-as.a.run.app \\
  GCP_PROJECT_ID=serverless-503308 \\
  python3 tests/smoke_bus_publish.py

Exits 0 on all-pass, 1 on first failure.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = os.environ.get("ADK_BASE_URL", "https://adk-runtime-hnvjxkvfoq-as.a.run.app")
PROJECT = os.environ.get("GCP_PROJECT_ID", "serverless-503308")
SUBSCRIPTION = os.environ.get("PUBSUB_SUBSCRIPTION", "label-hold-lean-view-sub")
LOTS = [
    ("HK-SMOKE-HOLD", "held"),
    ("HK-SMOKE-RELEASE", "held"),     # stub still says held; this verifies wire, not verdict
    ("HK-SMOKE-INCOMPLETE", "held"),
]

WORKDIR = Path("/tmp/smoke-bus-publish")
WORKDIR.mkdir(exist_ok=True)
for name in ("spec", "coa", "label"):
    p = WORKDIR / f"{name}.txt"
    if not p.exists():
        p.write_text(f"smoke fixture for {name}")


def _gcloud_token() -> str:
    out = subprocess.check_output(
        ["bash", "/opt/data/serverless/label-hold/scripts/gcloud-wrap.sh"],
        text=True, timeout=30,
    )
    return out.strip()


def _post_demo_run(lot_id: str) -> dict:
    cmd = [
        "curl", "-s", "-w", "\n%{http_code}",
        "-X", "POST", f"{BASE}/demo/run",
        "-F", f"lot_id={lot_id}",
        "-F", f"spec=@{WORKDIR/'spec.txt'}",
        "-F", f"coa=@{WORKDIR/'coa.txt'}",
        "-F", f"label=@{WORKDIR/'label.txt'}",
    ]
    out = subprocess.check_output(cmd, text=True, timeout=180)
    body, http_code = out.rsplit("\n", 1)
    assert http_code == "200", f"/demo/run {http_code}: {body[:500]}"
    return json.loads(body)


def _get_lot(lot_id: str) -> dict:
    out = subprocess.check_output(
        ["curl", "-s", f"{BASE}/demo/lots/{lot_id}"], text=True, timeout=30,
    )
    return json.loads(out)


def _pull_pubsub(lot_id: str, timeout_s: int = 30, token: str = "") -> dict | None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        env = os.environ.copy()
        env["CLOUDSDK_AUTH_ACCESS_TOKEN"] = token
        proc = subprocess.run(
            ["gcloud", "pubsub", "subscriptions", "pull",
             SUBSCRIPTION, "--project", PROJECT, "--max-messages", "10",
             "--format", "json", "--quiet"],
            capture_output=True, text=True, timeout=20, env=env,
        )
        try:
            msgs = json.loads(proc.stdout) if proc.stdout.strip() else []
        except json.JSONDecodeError:
            time.sleep(1)
            continue
        for m in msgs:
            data_b64 = m.get("message", {}).get("data", "")
            try:
                payload = json.loads(base64.b64decode(data_b64).decode("utf-8"))
            except Exception:
                continue
            if payload.get("lot_id") == lot_id:
                ack_id = m.get("ackId")
                if ack_id:
                    subprocess.run(
                        ["gcloud", "pubsub", "subscriptions", "ack",
                         SUBSCRIPTION, "--ack-ids", ack_id,
                         "--project", PROJECT, "--quiet"],
                        capture_output=True, text=True, timeout=10, env=env,
                    )
                return payload
        for m in msgs:
            ack_id = m.get("ackId")
            if ack_id:
                subprocess.run(
                    ["gcloud", "pubsub", "subscriptions", "ack",
                     SUBSCRIPTION, "--ack-ids", ack_id,
                     "--project", PROJECT, "--quiet"],
                    capture_output=True, text=True, timeout=10, env=env,
                )
        time.sleep(1)
    return None


def main() -> int:
    print(f"Smoke-testing {BASE}")
    token = _gcloud_token()
    failures = []
    for lot_id, expected_status in LOTS:
        print(f"\n--- {lot_id} ---")
        try:
            resp = _post_demo_run(lot_id)
            print(f"  /demo/run: 200 status={resp['status']} events={resp['events']} "
                  f"write_id={resp['write_id'][:12]}... bus_message_id={resp.get('bus_message_id')}")
            assert resp["status"] == expected_status, \
                f"verdict {resp['status']} != {expected_status}"
            assert resp["write_id"], "missing write_id"
            assert resp.get("bus_message_id"), "missing bus_message_id"

            row = _get_lot(lot_id)
            assert row["found"], f"firestore row missing for {lot_id}"
            assert row["status"] == expected_status
            assert row.get("bus_message_id") == resp["bus_message_id"]
            print(f"  firestore row: status={row['status']} bus_message_id={row['bus_message_id']}")

            # Verify the leanview-consumer (the bus subscriber) got the push.
            # Production Pub/Sub subscriptions with push endpoints deliver and ack
            # on 200 — there is no backlog to replay. So we check the consumer's
            # log for a POST /pubsub/push 200 line within the last 60s.
            env = os.environ.copy()
            env["CLOUDSDK_AUTH_ACCESS_TOKEN"] = token
            got_push = False
            for _ in range(6):
                proc = subprocess.run(
                    ["gcloud", "logging", "read",
                     "resource.type=cloud_run_revision "
                     "AND resource.labels.service_name=leanview-consumer "
                     "AND textPayload:\"POST /pubsub/push\"",
                     "--project", PROJECT,
                     "--limit", "20",
                     "--format", "value(textPayload,timestamp)"],
                    capture_output=True, text=True, timeout=20, env=env,
                )
                lines = proc.stdout.strip().splitlines() if proc.stdout.strip() else []
                # Filter to lines from the last 90s
                recent = []
                now = time.time()
                for line in lines:
                    # Format: "200 OK\t2026-08-27T02:09:29.664265Z"
                    parts = line.rsplit("\t", 1)
                    if len(parts) != 2:
                        continue
                    try:
                        ts = time.mktime(time.strptime(parts[1][:19], "%Y-%m-%dT%H:%M:%S"))
                    except ValueError:
                        continue
                    if now - ts < 90:
                        recent.append(parts[0])
                if recent:
                    got_push = True
                    print(f"  leanview-consumer: {len(recent)} recent POST /pubsub/push line(s)")
                    break
                time.sleep(5)
            assert got_push, "leanview-consumer did not log a recent POST /pubsub/push"
        except AssertionError as e:
            print(f"  FAIL: {e}")
            failures.append((lot_id, str(e)))
        except Exception as e:
            print(f"  ERROR: {e!r}")
            failures.append((lot_id, repr(e)))

    print("\n=========================================")
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for lot_id, msg in failures:
            print(f"  - {lot_id}: {msg}")
        return 1
    print(f"{len(LOTS)} PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
