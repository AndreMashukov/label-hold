#!/usr/bin/env bash
# Helper: build and pin a label-hold app via Cloud Build.
# Usage: bash scripts/_submit.sh <app-name>
set -euo pipefail

APP="${1:?usage: _submit.sh <app-name>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# Token bootstrap: use command substitution so the token never appears as a literal.
export CLOUDSDK_AUTH_ACCESS_TOKEN="$(bash "${ROOT}/scripts/gcloud-wrap.sh")"
export CLOUDSDK_CORE_PROJECT=serverless-503308

echo "[_submit] building ${APP}"
gcloud builds submit \
  --config=terraform/scripts/cloudbuild.yaml \
  --substitutions="_APP_NAME=${APP},_PROJECT_ID=serverless-503308,_REPO=label-hold-apps-dev" \
  --region=asia-southeast1 \
  --format=json \
  . | tee /tmp/${APP}-build.json

DIGEST=$(python3 -c '
import json, sys
build = json.load(open("/tmp/${APP}-build.json".replace("${APP}", sys.argv[1])))
images = (build.get("results") or {}).get("images") or []
if not images:
    raise SystemExit("no images in build result")
d = images[0].get("digest")
if not d:
    raise SystemExit("no digest in image entry")
print(d)
' "${APP}")
echo "[_submit] ${APP} digest: ${DIGEST}"

echo "[_submit] pinning ${APP} to ${DIGEST}"
gcloud run services update "${APP}" \
  --region=asia-southeast1 \
  --project=serverless-503308 \
  --image="${REGION:-asia-southeast1}-docker.pkg.dev/serverless-503308/label-hold-apps-dev/${APP}@${DIGEST}" \
  --format=json | python3 -c 'import json,sys; r=json.load(sys.stdin); print("[_submit] revision:", r.get("status",{}).get("latestReadyRevisionName","?"))'
