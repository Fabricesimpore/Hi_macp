#!/usr/bin/env bash
set -euo pipefail

# Configure these before running
: "${GOAL:=keep CI green}"
: "${ENV_NAME:=dev}"
: "${KUBE_CONTEXT:?set KUBE_CONTEXT}"
: "${KUBE_NAMESPACE:=staging}"
: "${GH_TOKEN:?set GH_TOKEN (PAT recommended for workflow triggers)}}"

export HI_MACP_ALLOW_EXECUTE=${HI_MACP_ALLOW_EXECUTE:-1}
export HI_MACP_REQUIRE_APPROVAL=${HI_MACP_REQUIRE_APPROVAL:-1}
export HI_MACP_APPROVED_BY=${HI_MACP_APPROVED_BY:-"operator"}
export HI_MACP_GH_OWNER=${HI_MACP_GH_OWNER:-""}
export HI_MACP_GH_REPO=${HI_MACP_GH_REPO:-""}
export HI_MACP_GH_BRANCH=${HI_MACP_GH_BRANCH:-"main"}

# Preflight: validate PAT/token works
if ! curl -sf -H "Authorization: token ${GH_TOKEN}" https://api.github.com/user > /dev/null; then
  echo "GH_TOKEN invalid or expired; aborting."
  exit 1
fi

python -m hi_macp.cli.hi doctor
python -m hi_macp.cli.hi run \
  --goal "${GOAL}" \
  --env-name "${ENV_NAME}" \
  --kube-context "${KUBE_CONTEXT}" \
  --namespace "${KUBE_NAMESPACE}" \
  --require-approval \
  --approved-by "${HI_MACP_APPROVED_BY}"
