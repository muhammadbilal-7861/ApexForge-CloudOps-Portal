#!/usr/bin/env bash
set -Eeuo pipefail
set +x
# Validated non-secret inventory is required even for read-only AWS operations.
source "${WORKSPACE:?}/deploy/load-config.sh"

: "${WORKSPACE:?Jenkins WORKSPACE is required}"
: "${AWS_CLI_IMAGE:?AWS_CLI_IMAGE is required}"
: "${PYTHON_IMAGE:?PYTHON_IMAGE is required}"
: "${AWS_REGION:?AWS_REGION is required}"
: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID is required}"
: "${AWS_EXPECTED_ROLE:?AWS_EXPECTED_ROLE is required}"
: "${SCM_BRANCH:?SCM_BRANCH is required}"
: "${GIT_COMMIT_FULL:?GIT_COMMIT_FULL is required}"

[[ "$SCM_BRANCH" == main ]] || { printf 'Deployment requires SCM_BRANCH=main (found %s).\n' "$SCM_BRANCH" >&2; exit 1; }
[[ "$GIT_COMMIT_FULL" =~ ^[0-9a-f]{40}$ ]] || { printf 'Invalid checked-out Git commit SHA.\n' >&2; exit 1; }
head_sha="$(git rev-parse HEAD)"
main_sha="$(git rev-parse 'refs/remotes/origin/main^{commit}')"
[[ "$head_sha" == "$main_sha" && "$head_sha" == "$GIT_COMMIT_FULL" ]] || { printf 'Deployment requires HEAD to equal the checked-out origin/main commit.\n' >&2; exit 1; }

identity="$(docker run --rm \
    --network host \
    --volumes-from jenkins \
    --env AWS_REGION="$AWS_REGION" \
    --env AWS_DEFAULT_REGION="$AWS_REGION" \
    "$AWS_CLI_IMAGE" sts get-caller-identity --output json)"
caller_account="$(printf '%s' "$identity" | docker run --rm -i "$PYTHON_IMAGE" python -c 'import json,sys; print(json.load(sys.stdin)["Account"])')"
caller_arn="$(printf '%s' "$identity" | docker run --rm -i "$PYTHON_IMAGE" python -c 'import json,sys; print(json.load(sys.stdin)["Arn"])')"
[[ "$caller_account" == "$AWS_ACCOUNT_ID" ]] || { printf 'Deployment refused: expected AWS account %s.\n' "$AWS_ACCOUNT_ID" >&2; exit 1; }
case "$caller_arn" in
    "arn:aws:sts::${AWS_ACCOUNT_ID}:assumed-role/${AWS_EXPECTED_ROLE}/"*) ;;
    *) printf 'Deployment refused: expected instance role %s.\n' "$AWS_EXPECTED_ROLE" >&2; exit 1 ;;
esac
printf 'Deployment source and IAM identity verified for commit %s.\n' "$GIT_COMMIT_FULL"
