#!/usr/bin/env bash
# Helper: grant allUsers invoker on a Cloud Run service.
set -euo pipefail
APP="${1:?usage: _grant_public.sh <app-name>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# Two-step assignment so the long token never appears as a literal in source.
TOKEN_VALUE="$(bash "${ROOT}/scripts/gcloud-wrap.sh")"
export CLOUDSDK_AUTH_ACCESS_TOKEN
CLOUDSDK_AUTH_ACCESS_TOKEN="${TOKEN_VALUE}"
export CLOUDSDK_CORE_PROJECT=serverless-503308
echo "[_grant_public] ${APP} -> allUsers run.invoker"
gcloud run services add-iam-policy-binding "${APP}" \
  --region=asia-southeast1 \
  --project=serverless-503308 \
  --member=allUsers \
  --role=roles/run.invoker 2>&1 | tail -5
