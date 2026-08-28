#!/usr/bin/env python3
"""tests/smoke_cdc.py - end-to-end CDC smoke (dashboard BFF entry).

Chain under test:

  /api/run on the dashboard BFF (multipart)
       -> dashboard forwards to adk-runtime /demo/run
       -> adk-runtime writes lots/{lot_id} in lots-db (status, write_id, ...)
       -> Firestore Eventarc trigger (created + updated on lots/{lot_id})
          fires and pushes a CloudEvent to /__eventarc/publish on
          adk-runtime
       -> /__eventarc/publish re-reads the row and publishes a
          lot.{held,released} message to label-hold-events
       -> leanview-consumer (Pub/Sub push, OIDC) materializes the
          event into lots-listen/{lot_id} on lean-db
       -> /api/lots on the dashboard BFF returns the new row

Run after a deploy:

  python3 tests/smoke_cdc.py

Defaults:

  DASHBOARD_BASE_URL = https://dashboard-hnvjxkvfoq-as.a.run.app
  GCP_PROJECT_ID     = serverless-503308
  LOT_ID             = HK-MULTI-REL
  FIXTURE_DIR        = ./fixtures/hk-multi-allergen-released/{spec,coa,label}

Exits 0 the moment /api/lots returns the row with the matching
write_id (within 30 s). Exits 1 with a diagnostic block if the row
never arrives -- the diagnostic block queries Cloud Logging for an
Eventarc hit on /__eventarc/publish (proves the trigger fired) and
queries lean-db directly via gcloud firestore (proves the consumer
materialized it; absence here with presence in lean-db is a dashboard
BFF caching issue, not a CDC issue).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import requests

DASH = os.environ.get(
    "DASHBOARD_BASE_URL", "https://dashboard-hnvjxkvfoq-as.a.run.app"
)
PROJECT = os.environ.get("GCP_PROJECT_ID", "serverless-503308")
LOT_ID = os.environ.get("SMOKE_LOT_ID", "HK-MULTI-REL")
FIX = Path(
    os.environ.get(
        "SMOKE_FIXTURE_DIR",
        "/opt/data/serverless/label-hold/fixtures/hk-multi-allergen-released",
    )
)
WAIT_S = int(os.environ.get("SMOKE_WAIT_S", "30"))
TIMEOUT_S = int(os.environ.get("SMOKE_REQUEST_TIMEOUT_S", "180"))


def _run_status() -> str:
    """POST /api/run on the dashboard BFF with the multi-allergen-released
    fixture set (spec.png, coa.png, label.jpg). Verdict must be 'released'."""
    spec = FIX / "spec.png"
    coa = FIX / "coa.png"
    label = FIX / "label.jpg"
    for p in (spec, coa, label):
        if not p.exists():
            print(f"  missing fixture: {p}", file=sys.stderr)
            sys.exit(2)

    with open(spec, "rb") as fs, open(coa, "rb") as fc, open(label, "rb") as fl:
        files = {
            "spec": (spec.name, fs, "image/png"),
            "coa": (coa.name, fc, "image/png"),
            "label": (label.name, fl, "image/jpeg"),
        }
        data = {"lot_id": LOT_ID}
        r = requests.post(
            f"{DASH}/api/run", data=data, files=files, timeout=TIMEOUT_S
        )
    print(f"  POST /api/run -> HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"    body: {r.text[:500]}", file=sys.stderr)
        raise AssertionError(
            f"dashboard /api/run returned {r.status_code}, expected 200"
        )
    body = r.json()
    print(
        f"  verdict: status={body.get('status')} "
        f"write_id={(body.get('write_id') or '')[:12]}..."
    )
    return body


def _poll_lots(write_id: str, deadline: float) -> dict | None:
    """Poll GET /api/lots on the dashboard BFF. Return the matching row
    (by lot_id AND write_id) or None on timeout. A row with the same
    lot_id but a different write_id is treated as stale and skipped."""
    while time.time() < deadline:
        r = requests.get(f"{DASH}/api/lots", timeout=20)
        if r.status_code == 200:
            rows = (r.json() or {}).get("lots") or []
            for row in rows:
                if row.get("lot_id") == LOT_ID:
                    if row.get("write_id") and row["write_id"] == write_id:
                        return row
        time.sleep(1)
    return None


def _diag_eventarc_logs() -> str:
    """Query Cloud Logging for an Eventarc hit on /__eventarc/publish.

    The hit itself proves the Firestore trigger fired (regardless of
    whether the bus publish succeeded). The absence of that log + the
    absence of the lean-db row means the trigger isn't routing
    correctly -- usually Eventarc IAM hasn't propagated yet (the
    deploy script's 90s sleep is sometimes not enough on a cold IAM
    cache).
    """
    if not _have_gcloud():
        return "(gcloud not on PATH; cannot query logs)"
    try:
        proc = subprocess.run(
            [
                "gcloud", "logging", "read",
                "resource.type=cloud_run_revision "
                "AND resource.labels.service_name=adk-runtime "
                "AND textPayload:__eventarc/publish",
                "--project", PROJECT,
                "--limit", "10",
                "--format", "value(timestamp,textPayload)",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return f"(gcloud failed rc={proc.returncode}: {proc.stderr.strip()[:200]})"
        out = proc.stdout.strip()
        return out if out else "(no /__eventarc/publish log entries in last run window)"
    except subprocess.TimeoutExpired:
        return "(gcloud logging read timed out)"


def _diag_lean_db() -> str:
    """Query lean-db directly via gcloud firestore. A row exists
    even when /api/lots doesn't return it = dashboard BFF caching issue
    (or the BFF process hasn't picked up the new revision yet)."""
    if not _have_gcloud():
        return "(gcloud not on PATH; cannot query lean-db)"
    try:
        proc = subprocess.run(
            [
                "gcloud", "firestore", "documents", "get",
                f"lots-listen/{LOT_ID}", "--database", "lean-db",
                "--project", PROJECT,
                "--format", "value(name)",
            ],
            capture_output=True, text=True, timeout=30,
        )
        if proc.returncode != 0:
            return (
                f"(gcloud firestore rc={proc.returncode}: {proc.stderr.strip()[:200]})"
            )
        out = proc.stdout.strip()
        if out:
            return f"lean-db lots-listen/{LOT_ID} EXISTS ({out})"
        return f"lean-db lots-listen/{LOT_ID}: MISSING (CDC chain did NOT materialize)"
    except subprocess.TimeoutExpired:
        return "(gcloud firestore get timed out)"


def _have_gcloud() -> bool:
    from shutil import which
    return which("gcloud") is not None


def main() -> int:
    print(f"smoke_cdc: dashboard={DASH} project={PROJECT} lot_id={LOT_ID}")
    print(f"  fixture dir: {FIX}")
    print(f"  wait budget: {WAIT_S}s")

    print("\n[1/3] driving lot through dashboard BFF /api/run")
    resp = _run_status()
    status = resp.get("status")
    write_id = resp.get("write_id")
    if status != "released":
        print(
            f"  FAIL: expected status='released', got '{status}'",
            file=sys.stderr,
        )
        return 1
    if not write_id:
        print("  FAIL: response missing write_id", file=sys.stderr)
        return 1

    print(
        f"\n[2/3] polling /api/lots for lot_id={LOT_ID} write_id={write_id[:12]}..."
    )
    deadline = time.time() + WAIT_S
    row = _poll_lots(write_id, deadline)
    if row:
        ts = row.get("updated_at", "?")
        print(
            f"  PASS: /api/lots returned the row after "
            f"{WAIT_S - int(deadline - time.time())}s (updated_at={ts})"
        )
        print(f"  undeclared: {row.get('undeclared') or row.get('reason') or '—'}")
        return 0

    # ---- timeout branch: surface the failure with diagnostics ----
    print(
        f"\n[3/3] TIMEOUT: /api/lots did not return the row within {WAIT_S}s"
    )
    print("  collecting diagnostics (these run sequentially, ~30s total)...")

    print("\n  --- diagnostic A: Eventarc /__eventarc/publish on adk-runtime ---")
    print("  " + _diag_eventarc_logs().replace("\n", "\n  "))

    print("\n  --- diagnostic B: lean-db lots-listen row ---")
    print("  " + _diag_lean_db().replace("\n", "\n  "))

    print(
        "\n  interpretation:\n"
        "    A empty + B MISSING    -> trigger never fired (IAM not\n"
        "                              propagated, or trigger isn't\n"
        "                              attached to the new adk-runtime\n"
        "                              revision -- rerun deploy_cdc_sse\n"
        "                              and wait the full 90s)\n"
        "    A empty + B EXISTS     -> CDC chain OK; dashboard BFF is\n"
        "                              caching / hasn't picked up the new\n"
        "                              image (a refresh + redeploy fixes it)\n"
        "    A has /__eventarc/publish -> trigger fired; problem is\n"
        "                              downstream (bus publish or\n"
        "                              leanview-consumer materializer)"
    )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        print(f"  FAIL: {e}", file=sys.stderr)
        sys.exit(1)
    except requests.RequestException as e:
        print(f"  NETERR: {e!r}", file=sys.stderr)
        sys.exit(1)
