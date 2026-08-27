#!/usr/bin/env bash
# deploy-image.sh — Cloud Build image + (optionally) pin Cloud Run to that digest.
#
# Usage:
#   ./scripts/deploy-image.sh <app-name> [context-path]
#
# Env vars:
#   PROJECT_ID    GCP project (default: serverless-503308)
#   REGION        Artifact Registry region (default: asia-southeast1)
#   REPO          Artifact Registry repo (default: label-hold-apps-dev)
#   SKIP_RUN_PIN  if set, do not call `gcloud run services update` after build
#
# Side effect:
#   - submits a Cloud Build using terraform/scripts/cloudbuild.yaml
#   - prints DIGEST=<sha256:...>
#   - if SKIP_RUN_PIN is unset, updates the matching Cloud Run service to that digest
set -euo pipefail

APP_NAME="${1:?usage: deploy-image.sh <app-name> [context-path]}"
# Source must be repo root so cloudbuild.yaml can resolve apps/${APP_NAME}/Dockerfile
CONTEXT="${2:-.}"

PROJECT_ID="${PROJECT_ID:-serverless-503308}"
REGION="${REGION:-asia-southeast1}"
REPO="${REPO:-label-hold-apps-dev}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/${APP_NAME}"

case "${APP_NAME}" in
  adk-runtime|leanview-consumer|dashboard|frontend) ;;
  *)
    echo "unsupported app name: ${APP_NAME}" >&2
    echo "allowed: adk-runtime | leanview-consumer | dashboard | frontend" >&2
    exit 2
    ;;
esac

# Container gcloud auth unblock — see terraform/scripts/plan-gate.sh header for the why.
# In this container, `gcloud` CLI does not have a usable keyring. ADC is an
# authorized_user blob. Cloud Build accepts a bearer token; SDK clients accept it
# via CLOUDSDK_AUTH_ACCESS_TOKEN. Stamping this once at the top of every gcloud
# invocation is the supported pattern.
if [[ -z "${CLOUDSDK_AUTH_ACCESS_TOKEN:-}" ]] && command -v gcloud >/dev/null 2>&1; then
  if gcloud auth application-default print-access-token >/dev/null 2>&1; then
    # capture stdout into the env var; bash handles this in the next statement
    CLOUDSDK_AUTH_ACCESS_TOKEN="$(gcloud auth application-default print-access-token)"
    export CLOUDSDK_AUTH_ACCESS_TOKEN
  fi
fi
export CLOUDSDK_CORE_PROJECT="${PROJECT_ID}"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "${ROOT}"

DOCKERFILE_PATH="${CONTEXT}/apps/${APP_NAME}/Dockerfile"
if [[ "${CONTEXT}" == "." ]]; then
  DOCKERFILE_PATH="apps/${APP_NAME}/Dockerfile"
fi
if [[ ! -f "${DOCKERFILE_PATH}" ]]; then
  echo "ERROR: no Dockerfile at ${DOCKERFILE_PATH}" >&2
  exit 1
fi

echo "== Cloud Build ${APP_NAME} (context=${CONTEXT}) =="
BUILD_JSON="$(gcloud builds submit \
  --config=terraform/scripts/cloudbuild.yaml \
  --substitutions="_APP_NAME=${APP_NAME},_PROJECT_ID=${PROJECT_ID},_REPO=${REPO}" \
  --region="${REGION}" \
  --format=json \
  "${CONTEXT}")"

DIGEST="$(printf '%s' "${BUILD_JSON}" | python3 -c '
import json, sys
build = json.load(sys.stdin)
images = (build.get("results") or {}).get("images") or []
if not images:
    raise SystemExit("Cloud Build results.images is empty - cannot resolve digest")
digest = images[0].get("digest")
if not digest:
    raise SystemExit("Cloud Build image entry has no digest")
print(digest)
')"
echo "DIGEST=${DIGEST} (from this Cloud Build)"

if [[ -n "${SKIP_RUN_PIN:-}" ]]; then
  echo "SKIP_RUN_PIN set - not updating Cloud Run"
  echo "${DIGEST}"
  exit 0
fi

echo "== Cloud Run update ${APP_NAME} =="
gcloud run services update "${APP_NAME}" \
  --region="${REGION}" \
  --image="${IMAGE}@${DIGEST}" \
  --quiet

gcloud run services describe "${APP_NAME}" \
  --region="${REGION}" \
  --format='value(status.latestReadyRevisionName,status.url)'
echo "OK deployed ${APP_NAME}"
echo "${DIGEST}"
