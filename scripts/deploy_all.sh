#!/usr/bin/env bash
# scripts/deploy_all.sh - single-source deploy for the label-hold stack.
#
# Deploys the three Cloud Run services in order: adk-runtime, leanview-consumer,
# dashboard. Each app is rebuilt via Cloud Build (using terraform/scripts/cloudbuild.yaml)
# and pinned to a fresh revision. Each step is idempotent: re-running on an unchanged
# tree just re-pins the same digest.
#
# Usage:
#   PROJECT_ID=serverless-503308 REGION=asia-southeast1 REPO=label-hold-apps-dev \
#     bash scripts/deploy_all.sh
#
# Env vars:
#   PROJECT_ID   GCP project                (default: serverless-503308)
#   REGION       Artifact Registry region   (default: asia-southeast1)
#   REPO         Artifact Registry repo     (default: label-hold-apps-dev)
#   SKIP_PIN     if set, build only — do not pin Cloud Run revision
#
# Notes:
#   - ADK_RUNTIME_URL must already be set on the dashboard service before the
#     dashboard is deployed (it is read at request time, not at deploy time).
#   - The bearer token issue documented in scripts/gcloud-wrap.sh applies: inside
#     this container, gcloud SDK calls need CLOUDSDK_AUTH_ACCESS_TOKEN populated.
#     We delegate to deploy-image.sh, which already handles that.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# Token bootstrap: gcloud-wrap.sh prints a fresh bearer token. Export via
# command substitution so the long token never appears as a literal in the
# script (the token-redaction filter would otherwise corrupt it).
if [[ -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}" ]] && command -v gcloud >/dev/null 2>&1; then
  CLOUDSDK_AUTH_ACCESS_TOKEN="$(bash "${ROOT}/scripts/gcloud-wrap.sh")"
  export CLOUDSDK_AUTH_ACCESS_TOKEN
fi

PROJECT_ID="${PROJECT_ID:-serverless-503308}"
REGION="${REGION:-asia-southeast1}"
REPO="${REPO:-label-hold-apps-dev}"
export PROJECT_ID REGION REPO

if [[ -n "${SKIP_PIN:-}" ]]; then
  echo "[deploy_all] SKIP_PIN set — building images only, no Cloud Run pin"
fi

echo "[deploy_all] 1/3 adk-runtime"
bash "${ROOT}/scripts/deploy-image.sh" adk-runtime .

echo "[deploy_all] 2/3 leanview-consumer"
bash "${ROOT}/scripts/deploy-image.sh" leanview-consumer .

echo "[deploy_all] 3/3 dashboard"
bash "${ROOT}/scripts/deploy-image.sh" dashboard .

echo "[deploy_all] all three services rebuilt"
if [[ -z "${SKIP_PIN:-}" ]]; then
  echo "[deploy_all] Cloud Run revisions pinned"
fi
