#!/usr/bin/env bash
# Every launch API invocation is hard-coded DryRun. Never launch an instance.
set -Eeuo pipefail
set +x
umask 077
: "${WORKSPACE:?}" "${AWS_CLI_IMAGE:?}" "${PYTHON_IMAGE:?}" "${AWS_REGION:?}"
version="${1:?An explicit validated candidate version is required}"
[[ "$version" =~ ^[0-9]+$ ]] || { printf 'Permission validation requires a numeric launch-template version.\n' >&2; exit 1; }
bash deploy/assert-deploy-context.sh
rm -f "$WORKSPACE/reports/asg-launch-permissions.json"
bash deploy/verify-asg-candidate.sh "$version"
work_dir="$WORKSPACE/.deploy-work"
aws_cli() {
    docker run --rm --network host --volumes-from jenkins --workdir "$WORKSPACE" \
        --env AWS_REGION="$AWS_REGION" --env AWS_DEFAULT_REGION="$AWS_REGION" "$AWS_CLI_IMAGE" "$@"
}
require_dry_run() {
    local status=0 error_file="$work_dir/launch-permission-error" code
    aws_cli "$@" > /dev/null 2> "$error_file" || status=$?
    # AWS deliberately returns a nonzero status for successful DryRun checks.
    # Never print raw API output: requests contain private bootstrap user data.
    code="$(sed -n 's/^.*An error occurred (\([A-Za-z0-9]*\)) when calling .*$/\1/p' "$error_file")"
    if ((status == 0)) || [[ "$code" != DryRunOperation ]]; then
        printf 'Non-launching EC2 permission validation failed: %s (status=%s).\n' "${code:-unexpected-response}" "$status" >&2
        return 1
    fi
}
docker run --rm --volumes-from jenkins --user "$(id -u):$(id -g)" --workdir "$WORKSPACE" \
    "$PYTHON_IMAGE" python deploy/render-launch-permission-check.py \
    --overrides "$work_dir/launch-template-overrides.json" --version "$version" \
    --asg-data "$work_dir/asg.json" --output-dir "$work_dir"
for subnet in subnet-08469c4e69b5c4d65 subnet-05788ba98ca1096e6; do
    require_dry_run ec2 run-instances --dry-run --cli-input-json "file://$work_dir/launch-permission-$subnet.json"
    printf 'DryRun permission validation passed: template=%s subnet=%s; no instance launched.\n' "$version" "$subnet"
done
mkdir -p "$WORKSPACE/reports"
printf '{"dryRun":true,"templateVersion":"%s","subnets":["subnet-08469c4e69b5c4d65","subnet-05788ba98ca1096e6"],"result":"passed"}\n' \
    "$version" > "$WORKSPACE/reports/asg-launch-permissions.json"
