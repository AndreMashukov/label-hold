#!/usr/bin/env bash
# terraform/scripts/plan-gate.sh
# fmt + validate + plan without apply. Mirrors the gcp-terraform-cloud-run skill.
set -euo pipefail
ENV="${1:-dev}"
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../envs/${ENV}" && pwd)"
cd "${DIR}"

echo "==> terraform fmt -check"
terraform fmt -check -recursive

echo "==> terraform init"
terraform init -input=false

echo "==> terraform validate"
terraform validate

echo "==> terraform plan"
terraform plan -input=false -out="./${ENV}.tfplan"
echo "Plan written to ./${ENV}.tfplan"
echo "STOP. Ask for approval before 'terraform apply ./${ENV}.tfplan'."