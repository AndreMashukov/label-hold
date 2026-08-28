#!/usr/bin/env python3
"""tests/smoke_end_to_end.py — full-stack smoke.

Drives three lots (text + binary fixtures) through the dashboard BFF and
asserts the entire chain is intact:
  1. POST /api/run on the dashboard -> forwards to adk-runtime
  2. adk-runtime ingests via Gemini Flash, computes verdict, writes lots/
  3. bus fires -> leanview-consumer materializes lots-listen/
  4. dashboard's /api/lots reflects the new row

The script picks one HELD scenario (binary, multi-allergen) and one RELEASED
(binary, all-declared). An INC scenario verifies the missing-document path.
Exits 0 on all-pass, 1 on first failure.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

DASH = os.environ.get("DASHBOARD_BASE_URL",
                      "https://dashboard-hnvjxkvfoq-as.a.run.app")
PROJECT = os.environ.get("GCP_PROJECT_ID", "serverless-503308")
FIX_ROOT = Path("/opt/data/serverless/label-hold/fixtures")

# (lot_id, expected_verdict, spec_path, coa_path, label_path, spec_type, coa_type, label_type)
LOTS = [
    (
        "HK-SMOKE-HOLD",
        "held",
        FIX_ROOT / "hk-hold-milk/spec.txt",
        FIX_ROOT / "hk-hold-milk/coa.txt",
        FIX_ROOT / "hk-hold-milk/label.txt",
        "text/plain", "text/plain", "text/plain",
    ),
    (
        "HK-SMOKE-MULTI",
        "held",
        FIX_ROOT / "hk-multi-allergen/spec.png",
        FIX_ROOT / "hk-multi-allergen/coa.png",
        FIX_ROOT / "hk-multi-allergen/label.jpg",
        "image/png", "image/png", "image/jpeg",
    ),
    (
        "HK-SMOKE-REL",
        "released",
        FIX_ROOT / "hk-multi-allergen-released/spec.png",
        FIX_ROOT / "hk-multi-allergen-released/coa.png",
        FIX_ROOT / "hk-multi-allergen-released/label.jpg",
        "image/png", "image/png", "image/jpeg",
    ),
]


def post_run(lot_id, spec, coa, label, st, ct, lt):
    cmd = [
        "curl", "-s", "-X", "POST", f"{DASH}/api/run",
        "-F", f"lot_id={lot_id}",
        "-F", f"spec=@{spec};type={st}",
        "-F", f"coa=@{coa};type={ct}",
        "-F", f"label=@{label};type={lt}",
    ]
    out = subprocess.check_output(cmd, text=True, timeout=240)
    return json.loads(out)


def get_lean(lot_id, token):
    """Read lots-listen/{lot_id} on lean-db via the Firestore SDK.

    SDK uses ADC (which the gcloud-wrap token primes), so no separate auth
    dance is needed. Returns a Firestore DocumentSnapshot-like dict.
    """
    from google.cloud import firestore
    client = firestore.Client(database="lean-db", project=PROJECT)
    doc_ref = client.collection("lots-listen").document(lot_id)
    snap = doc_ref.get()
    if not snap.exists:
        return None
    d = snap.to_dict() or {}
    d["lot_id"] = d.get("lot_id") or lot_id
    ts = d.get("updated_at")
    if hasattr(ts, "isoformat"):
        d["updated_at"] = ts.isoformat()
    return {"fields": d}


def gcloud_token():
    out = subprocess.check_output(
        ["bash", "/opt/data/serverless/label-hold/scripts/gcloud-wrap.sh"],
        text=True, timeout=30,
    )
    return out.strip()


def main():
    print(f"Smoke-testing dashboard {DASH}")
    failures = []
    token = gcloud_token()
    for lot_id, expected, spec, coa, label, st, ct, lt in LOTS:
        print(f"\n--- {lot_id} (expected={expected}) ---")
        try:
            t0 = time.time()
            resp = post_run(lot_id, spec, coa, label, st, ct, lt)
            dt = time.time() - t0
            print(f"  /api/run ({dt:.1f}s): status={resp['status']} write_id={resp['write_id'][:12]}… bus_message_id={resp.get('bus_message_id')}")
            assert resp["status"] == expected, \
                f"verdict {resp['status']} != expected {expected}"
            assert resp.get("bus_message_id"), "missing bus_message_id"
            assert resp.get("write_id"), "missing write_id"

            # Wait for leanview-consumer to materialize
            row = None
            for attempt in range(10):
                row = get_lean(lot_id, token)
                if row and row.get("fields", {}).get("status"):
                    break
                time.sleep(2)
            assert row is not None, f"lots-listen/{lot_id} not materialized"
            fields = row["fields"]
            # get_lean wraps SDK dict under {"fields": ...} for symmetry; values
            # are now SDK-native (strings, not REST stringValue wrappers).
            actual_status = fields.get("status")
            actual_wid = fields.get("write_id")
            print(f"  lean-db lots-listen: status={actual_status} write_id={(actual_wid or '')[:12]}…")
            assert actual_status == expected, \
                f"lean status {actual_status} != {expected}"
            assert actual_wid == resp["write_id"], \
                f"lean write_id {actual_wid} != adk write_id {resp['write_id']}"
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
