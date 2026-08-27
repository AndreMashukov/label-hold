#!/usr/bin/env bash
# scripts/smoke.sh - end-to-end smoke test for the scaffold.
# Verifies all three Cloud Run services return 200 on /health.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Pull service URLs from Terraform outputs.
cd terraform/envs/dev
ADK_URL=$(terraform output -raw adk_runtime_url)
LEANVIEW_URL=$(terraform output -raw leanview_consumer_url)
DASH_URL=$(terraform output -raw dashboard_url)
cd "${OLDPWD}"

echo "==> adk-runtime /health"
curl -fsS "${ADK_URL}/health" | jq
echo
echo "==> leanview-consumer /health"
curl -fsS "${LEAN_URL}/health" | jq
echo
echo "==> dashboard /"
curl -fsS -o /tmp/dashboard.html -w "HTTP %{http_code}\n" "${DASH_URL}/"
echo "First 200 bytes:"
head -c 200 /tmp/dashboard.html
echo
echo "All three services OK."