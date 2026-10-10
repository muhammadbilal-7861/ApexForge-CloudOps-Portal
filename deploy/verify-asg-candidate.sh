#!/usr/bin/env bash
# Read the complete immutable AWS version, privately; do not filter away Placement.
set -Eeuo pipefail
set +x
# Validated non-secret inventory is required even for read-only AWS operations.
source "${WORKSPACE:?}/deploy/load-config.sh"
umask 077
: "${WORKSPACE:?}" "${AWS_CLI_IMAGE:?}" "${PYTHON_IMAGE:?}" "${AWS_REGION:?}"
: "${ECR_DEPLOY_IMAGE:?}" "${GIT_COMMIT_FULL:?}"
version="${1:?An explicit candidate version is required}"
[[ "$version" =~ ^[0-9]+$ && "$version" -gt 5 ]] || { printf 'Invalid candidate version.\n' >&2; exit 1; }
work_dir="$WORKSPACE/.deploy-work"
docker run --rm --network host --volumes-from jenkins --workdir "$WORKSPACE" \
    --env AWS_REGION="$AWS_REGION" --env AWS_DEFAULT_REGION="$AWS_REGION" "$AWS_CLI_IMAGE" \
    ec2 describe-launch-template-versions --launch-template-id "${LAUNCH_TEMPLATE_ID}" --versions "$version" \
    --query 'LaunchTemplateVersions[0]' --output json > "$work_dir/launch-template-candidate.json"
action=--approved
preview_args=()
if [[ "${2:-}" == record ]]; then
    action=--output
    preview_args=(--preview "$WORKSPACE/reports/asg-launch-template-preview.json")
fi
docker run --rm --volumes-from jenkins --user "$(id -u):$(id -g)" --workdir "$WORKSPACE" \
    --env CLOUDOPS_CONFIG_FILE "$PYTHON_IMAGE" python deploy/validate-asg-candidate.py \
    --candidate "$work_dir/launch-template-candidate.json" --expected "$work_dir/launch-template-overrides.json" \
    --version "$version" --image "$ECR_DEPLOY_IMAGE" --commit "$GIT_COMMIT_FULL" \
    "$action" "$WORKSPACE/reports/asg-launch-candidate.json" "${preview_args[@]}"
