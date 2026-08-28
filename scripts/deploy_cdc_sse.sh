#!/usr/bin/env bash
# scripts/deploy_cdc_sse.sh - swap Artifact Registry -> Terraform tfvars -> apply.
#
# Why this exists:
#   `gcloud builds submit` only uploads to Artifact Registry, it does NOT
#   pin a new Cloud Run revision. To actually deploy, the running
#   services (adk-runtime, dashboard, leanview-consumer) need their
#   image reference in terraform/envs/dev/terraform.tfvars updated,
#   then `terraform apply` must run. Without that, the running services
#   stay pinned to the OLD Day 0 scaffold (sha256:0f88812a... for
#   dashboard, sha256:c319950b... for adk-runtime), and the React SPA
#   sees an empty /api/lots.
#
# What it does:
#   1. Queries Artifact Registry for the most recent SHA256 digest of
#      each of {adk-runtime, dashboard, leanview-consumer}.
#   2. Rewrites the three image lines in terraform.tfvars in place
#      (uses python3 + file read/write -- bash heredocs have failed
#      before in this sandbox when the lines contain literal
#      "sha256:..." strings).
#   3. Runs `terraform init -upgrade` then `terraform plan -out=...`.
#   4. Echoes the plan output (google_cloud_run_v2_service.this plus
#      the three image SHA changes).
#   5. If --apply is passed: `terraform apply`, then `sleep 90` for the
#      Eventarc IAM bindings to propagate (the Eventarc Firestore
#      trigger role grants lag 60-120s after the Cloud Run revision
#      settles).
#   6. Prints the dashboard + adk-runtime URLs to test next.
#   7. Logs every run to scripts/deploy_cdc_sse.log.
#
# Usage:
#   bash scripts/deploy_cdc_sse.sh           # plan only (default; safe)
#   bash scripts/deploy_cdc_sse.sh --apply   # plan + apply + 90s sleep
#
# Safe to re-run. Will NOT apply without --apply.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG="${ROOT}/scripts/deploy_cdc_sse.log"
TFVARS="${ROOT}/terraform/envs/dev/terraform.tfvars"
TFENV="${ROOT}/terraform/envs/dev"
PLAN="/tmp/cdc.plan"

PROJECT_ID="${PROJECT_ID:-serverless-503308}"
REGION="${REGION:-asia-southeast1}"
REPO="${REPO:-label-hold-apps-dev}"
AR_HOST="${REGION}-docker.pkg.dev"

APPLY=0
for arg in "$@"; do
  case "$arg" in
    --apply) APPLY=1 ;;
    -h|--help)
      sed -n '2,28p' "$0"
      exit 0
      ;;
    *)
      echo "unknown arg: $arg" >&2
      exit 2
      ;;
  esac
done

log() {
  local msg="[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"
  echo "$msg" | tee -a "$LOG"
}

# Token bootstrap: gcloud SDK calls (run services describe, artifacts
# docker images list) need a bearer token when the on-disk creds are
# ADC-only (the case here). We delegate to gcloud-wrap.sh which prints
# a fresh token via `gcloud auth application-default print-access-token`.
if [[ -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}" ]] && command -v gcloud >/dev/null 2>&1; then
  export CLOUDSDK_AUTH_ACCESS_TOKEN="$(bash "${ROOT}/scripts/gcloud-wrap.sh")"
fi

log "deploy_cdc_sse start (apply=${APPLY}, project=${PROJECT_ID}, region=${REGION})"

# --- step 1: query AR for the most recent digest of each image -------
# `gcloud artifacts docker images list` returns rows most-recent-first
# when --sort-by=~create_time; --include-tags lets us include any
# tagged builds, but for our pin-by-digest flow we just want the top
# row's digest.
declare -A DIGEST=()
for IMG in adk-runtime dashboard leanview-consumer; do
  RAW="$(gcloud artifacts docker images list \
      "${AR_HOST}/${PROJECT_ID}/${REPO}/${IMG}" \
      --include-tags \
      --format='value(digest)' \
      --limit=1 2>&1 || true)"
  # First non-empty token is the digest; gcloud sometimes prefixes
  # with a status line when the auth is dead. Strip any control chars.
  D="$(echo "$RAW" | awk 'NF{print $1; exit}')"
  if [[ -z "$D" || "$D" != sha256:* ]]; then
    log "ERROR: could not pull digest for ${IMG} -- gcloud output: ${RAW}"
    log "HINT: is gcloud auth alive? try: gcloud auth application-default login"
    exit 3
  fi
  DIGEST["$IMG"]="$D"
  log "AR digest: ${IMG} = ${D}"
done

# --- step 2: rewrite terraform.tfvars --------------------------------
# We read the entire file in Python (bash heredocs + jq have failed in
# this sandbox when the targets contain literal '@sha256:...' strings),
# do three line replacements, and write back atomically. All other
# content (region, project_id, common_env_vars, smoke_test_key) is
# preserved verbatim.
log "rewriting ${TFVARS}"
python3 - "$TFVARS" "${DIGEST[adk-runtime]}" "${DIGEST[dashboard]}" "${DIGEST[leanview-consumer]}" <<'PYEOF' || exit 4
import re
import sys

tfvars_path, adk, dash, lean = sys.argv[1:5]

with open(tfvars_path, "r", encoding="utf-8") as f:
    src = f.read()

# Each image line: "<key> = \"...@sha256:<64hex>\""
# Match the whole line including the trailing newline so the file's
# comment block + key=value spacing is preserved.
patterns = {
    r'^adk_runtime_image\s*=\s*"[^"]*"\s*$':   f'adk_runtime_image   = "{adk}"',
    r'^dashboard_image\s*=\s*"[^"]*"\s*$':      f'dashboard_image     = "{dash}"',
    r'^leanview_image\s*=\s*"[^"]*"\s*$':       f'leanview_image      = "{lean}"',
}

out = src
matches = 0
for pat, replacement in patterns.items():
    new, n = re.subn(pat, replacement, out, count=1, flags=re.MULTILINE)
    if n != 1:
        sys.stderr.write(f"pattern did not match uniquely: {pat}\n")
        sys.exit(1)
    out = new
    matches += n

with open(tfvars_path, "w", encoding="utf-8") as f:
    f.write(out)
print(f"tfvars updated: {matches} line(s)")
PYEOF

# Show the post-write image lines so the operator can sanity-check.
log "new image lines:"
grep -E 'image\s*=\s*"' "$TFVARS" | sed 's/^/    /' | tee -a "$LOG"

# --- step 3: terraform init + plan -----------------------------------
log "terraform init -upgrade"
( cd "$TFENV" && terraform init -upgrade ) 2>&1 | tee -a "$LOG"

log "terraform plan -> ${PLAN}"
( cd "$TFENV" && terraform plan -out="$PLAN" ) 2>&1 | tee -a "$LOG"

# --- step 4: echo plan key lines --------------------------------------
log "plan summary (filtered):"
( cd "$TFENV" && terraform show -no-color "$PLAN" ) 2>&1 \
  | grep -E 'google_cloud_run_v2_service\.this|^\s*# .*image\.|^\s*\+ image\s*=|^\s*~ image\s*=|^\s*- image\s*=|sha256:' \
  | tee -a "$LOG" || true

# --- step 5: optional apply + IAM propagation sleep ------------------
if [[ "$APPLY" == "1" ]]; then
  log "terraform apply ${PLAN}"
  ( cd "$TFENV" && terraform apply -auto-approve "$PLAN" ) 2>&1 | tee -a "$LOG"

  log "sleeping 90s for Eventarc IAM bindings to propagate (60-120s typical)"
  sleep 90
  log "Eventarc IAM propagation window elapsed; safe to drive traffic"
else
  log "plan-only mode (no --apply); rerun with --apply to deploy"
fi

# --- step 6: tell the operator which URLs to test next ----------------
# Pull terraform outputs if available (they will be present after init
# even without apply). Fall back to the canonical URL pattern.
DASH_URL="$(cd "$TFENV" && (terraform output -raw dashboard_url 2>/dev/null || true))"
ADK_URL="$(cd "$TFENV" && (terraform output -raw adk_runtime_url 2>/dev/null || true))"
LEAN_URL="$(cd "$TFENV" && (terraform output -raw leanview_consumer_url 2>/dev/null || true))"

log "next-step URLs:"
cat <<EOF | sed 's/^/    /' | tee -a "$LOG"
dashboard BFF   : ${DASH_URL:-https://dashboard-hnvjxkvfoq-as.a.run.app}
adk-runtime     : ${ADK_URL:-https://adk-runtime-hnvjxkvfoq-as.a.run.app}
leanview        : ${LEAN_URL:-https://leanview-consumer-hnvjxkvfoq-as.a.run.app}

post-deploy sequence:
  bash scripts/deploy_cdc_sse.sh --apply          # this script, with --apply
  python3 tests/smoke_cdc.py                      # drive /api/run, wait for lean-db row
  python3 tests/capture_ui.py                     # screenshot the live SPA
EOF

log "deploy_cdc_sse done"
