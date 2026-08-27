#!/usr/bin/env bash
# gcloud-wrap.sh — refreshes the bearer token and prints it.
# Use this to unblock `gcloud run services describe`, `gcloud pubsub ...`, etc.,
# which fail when only ADC (authorized_user) is available.
#
# Usage:
#   bash scripts/gcloud-wrap.sh
#   export CLOUDSDK_AUTH_ACCESS_TOKEN=$(bash scripts/gcloud-wrap.sh)
#   CLOUDSDK_AUTH_ACCESS_TOKEN=$(bash scripts/gcloud-wrap.sh) gcloud run services list --project=serverless-503308

set -euo pipefail
TOKEN=$(gcloud auth application-default print-access-token)
echo "$TOKEN"
