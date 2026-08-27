#!/usr/bin/env bash
# scripts/record_demo.sh - Day 4 demo recorder skeleton.
#
# Day 4 deliverable is an unedited demo video showing:
#   1. the .run.app URL in the address bar
#   2. terminal output of the pipeline running live
#   3. the dashboard UI updating
#   4. the proof-of-action harness panes filling in
#
# Actual screen capture tooling (ffmpeg / OBS / browser headless) depends on
# the host environment. This script is the orchestration shell that
# (a) opens the dashboard in a browser with the URL visible,
# (b) runs smoke_all.sh in one terminal pane,
# (c) polls /api/lots and prints status into a second terminal pane.
#
# Usage:
#   DASHBOARD_URL=https://dashboard-hnvjxkvfoq-as.a.run.app \
#     bash scripts/record_demo.sh
#
# Env vars:
#   DASHBOARD_URL         base URL of the deployed dashboard (required)
#   BROWSER_CMD           command that opens a URL (default: xdg-open)
#   TERMINAL_CMD          command that opens a new terminal window
#                         (default: x-terminal-emulator)
#   RECORDER_CMD          ffmpeg/obs/etc — left to the operator
#
# This script is intentionally a skeleton: Day 4 capture needs actual tooling
# installed on the recording host. Re-run with real RECORDER_CMD once the host
# is set up.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

DASHBOARD_URL="${DASHBOARD_URL:-}"
BROWSER_CMD="${BROWSER_CMD:-xdg-open}"
TERMINAL_CMD="${TERMINAL_CMD:-x-terminal-emulator}"

if [[ -z "${DASHBOARD_URL}" ]]; then
  echo "[record_demo] DASHBOARD_URL is required" >&2
  exit 2
fi

echo "[record_demo] target: ${DASHBOARD_URL}"
echo "[record_demo] step 1: open browser at dashboard root"
"${BROWSER_CMD}" "${DASHBOARD_URL}/" || true
echo "[record_demo] step 2: open proof-of-action harness in a second tab"
"${BROWSER_CMD}" "${DASHBOARD_URL}/proof-of-action.html" || true
echo "[record_demo] step 3: open terminal pane and run smoke_all.sh"
"${TERMINAL_CMD}" -e "DASHBOARD_URL=${DASHBOARD_URL} bash ${ROOT}/scripts/smoke_all.sh" || true
echo "[record_demo] skeleton complete — start screen recorder manually"
echo "[record_demo] RECORDER_CMD not set; install ffmpeg and re-run with RECORDER_CMD=ffmpeg"
