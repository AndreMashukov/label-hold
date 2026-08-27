#!/usr/bin/env bash
# scripts/smoke_all.sh - end-to-end smoke driving two lots through the live pipeline.
#
# Drives one held lot (hk-multi-allergen: wheat declared, milk+eggs undeclared)
# and one released lot (hk-multi-allergen-released: wheat+milk+eggs all declared)
# through the dashboard's /api/run endpoint, waits for the lean-db materializer
# to land the row, and prints PASS/FAIL per lot.
#
# Usage:
#   DASHBOARD_URL=https://dashboard-hnvjxkvfoq-as.a.run.app \
#     bash scripts/smoke_all.sh
#
# Env vars:
#   DASHBOARD_URL  Base URL of the deployed dashboard service
#   POLL_TIMEOUT   Seconds to wait for lean-db row to land (default: 60)
#   POLL_INTERVAL  Seconds between polls                     (default: 5)
#
# Exit codes:
#   0  all lots PASS
#   1  at least one lot FAIL
#   2  precondition failed (dashboard unreachable)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIX="${ROOT}/fixtures"

DASHBOARD_URL="${DASHBOARD_URL:-}"
POLL_TIMEOUT="${POLL_TIMEOUT:-60}"
POLL_INTERVAL="${POLL_INTERVAL:-5}"

if [[ -z "${DASHBOARD_URL}" ]]; then
  echo "[smoke_all] DASHBOARD_URL is required" >&2
  exit 2
fi

if ! curl -fsS -o /dev/null "${DASHBOARD_URL}/health"; then
  echo "[smoke_all] dashboard /health unreachable at ${DASHBOARD_URL}" >&2
  exit 2
fi
echo "[smoke_all] dashboard reachable at ${DASHBOARD_URL}"

# --- lot 1: HELD (wheat+milk+eggs in spec/CoA, only Wheat on label) ---
LOT1="HK-SMOKE-MULTI-$(date +%s)"
echo "[smoke_all] uploading HELD lot ${LOT1}"
HELD_RESP="$(curl -fsS \
  -F "lot_id=${LOT1}" \
  -F "spec=@${FIX}/hk-multi-allergen/spec.png;type=image/png" \
  -F "coa=@${FIX}/hk-multi-allergen/coa.png;type=image/png" \
  -F "label=@${FIX}/hk-multi-allergen/label.jpg;type=image/jpeg" \
  "${DASHBOARD_URL}/api/run")"
printf '[smoke_all] HELD response: %s\n' "${HELD_RESP}"

# --- lot 2: RELEASED (same recipe, full allergen declaration) ---
LOT2="HK-SMOKE-REL-$(date +%s)"
echo "[smoke_all] uploading RELEASED lot ${LOT2}"
REL_RESP="$(curl -fsS \
  -F "lot_id=${LOT2}" \
  -F "spec=@${FIX}/hk-multi-allergen-released/spec.png;type=image/png" \
  -F "coa=@${FIX}/hk-multi-allergen-released/coa.png;type=image/png" \
  -F "label=@${FIX}/hk-multi-allergen-released/label.jpg;type=image/jpeg" \
  "${DASHBOARD_URL}/api/run")"
printf '[smoke_all] RELEASED response: %s\n' "${REL_RESP}"

# --- wait for lean-db rows to materialize, then assert ---
HELD_OK="FAIL"
REL_OK="FAIL"
DEADLINE=$(( $(date +%s) + POLL_TIMEOUT ))
while [[ $(date +%s) -lt ${DEADLINE} ]]; do
  HELD_DOC="$(curl -fsS "${DASHBOARD_URL}/api/lots/${LOT1}" || true)"
  REL_DOC="$(curl -fsS "${DASHBOARD_URL}/api/lots/${LOT2}" || true)"
  if printf '%s' "${HELD_DOC}" | grep -q '"status":"held"'; then HELD_OK="PASS"; fi
  if printf '%s' "${REL_DOC}" | grep -q '"status":"released"'; then REL_OK="PASS"; fi
  if [[ "${HELD_OK}" == "PASS" && "${REL_OK}" == "PASS" ]]; then break; fi
  sleep "${POLL_INTERVAL}"
done

printf '[smoke_all] HELD    %s (lot=%s)\n' "${HELD_OK}" "${LOT1}"
printf '[smoke_all] RELEASED %s (lot=%s)\n' "${REL_OK}" "${LOT2}"

if [[ "${HELD_OK}" == "PASS" && "${REL_OK}" == "PASS" ]]; then
  echo "[smoke_all] PASS 2/2"
  exit 0
fi
echo "[smoke_all] FAIL"
exit 1
