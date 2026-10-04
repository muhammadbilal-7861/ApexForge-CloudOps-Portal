#!/usr/bin/env bash
# Preapproval: create one clean candidate version, read it back, DryRun both subnets.
# This writes a launch-template version only; it never changes capacity/defaults or launches instances.
set -Eeuo pipefail
set +x
: "${WORKSPACE:?}" "${PYTHON_IMAGE:?}" "${ECR_DEPLOY_IMAGE:?}" "${GIT_COMMIT_FULL:?}" "${GIT_COMMIT_SHORT:?}" "${AWS_CLI_IMAGE:?}" "${AWS_REGION:?}"
umask 077
bash deploy/assert-deploy-context.sh
[[ -z "${ASG_VALIDATED_VERSION:-}" ]] || { printf 'Refusing to replace an approved candidate.\n' >&2; exit 1; }
work_dir="$WORKSPACE/.deploy-work"
# Discard only these generated reports; a failed check must not reuse evidence
# from a previous build. AWS candidate versions are retained for inspection.
rm -f "$WORKSPACE/reports/asg-launch-candidate.json" "$WORKSPACE/reports/asg-launch-permissions.json"
python_cli() {
    docker run --rm --volumes-from jenkins --user "$(id -u):$(id -g)" --workdir "$WORKSPACE" \
        "$PYTHON_IMAGE" python "$@"
}
python_cli deploy/render-cloudops-user-data.py --template deploy/cloudops-user-data.sh.tmpl \
    --deploy-script deploy/cloudops-deploy.sh --verify-script deploy/cloudops-verify.sh \
    --image "$ECR_DEPLOY_IMAGE" --version "$GIT_COMMIT_SHORT" --commit "$GIT_COMMIT_FULL" \
    --output "$work_dir/cloudops-user-data.sh"
base64 -w0 "$work_dir/cloudops-user-data.sh" > "$work_dir/cloudops-user-data.b64"
chmod 600 "$work_dir/cloudops-user-data.b64"
python_cli deploy/render-launch-template-data.py --source-data "$work_dir/launch-template-source.json" \
    --user-data "$work_dir/cloudops-user-data.b64" --version "$GIT_COMMIT_SHORT" \
    --output "$work_dir/launch-template-overrides.json"
mkdir -p "$WORKSPACE/reports"
python_cli deploy/preview-cloudops-asg.py --source-data "$work_dir/launch-template-source.json" \
    --ami "$work_dir/reviewed-ami.json" --parameter "$work_dir/reviewed-ami-parameter.json" \
    --overrides "$work_dir/launch-template-overrides.json" --output "$WORKSPACE/reports/asg-launch-template-preview.json"

aws_cli() {
    docker run --rm --network host --volumes-from jenkins --workdir "$WORKSPACE" \
        --env AWS_REGION="$AWS_REGION" --env AWS_DEFAULT_REGION="$AWS_REGION" "$AWS_CLI_IMAGE" "$@"
}
# Omitting SourceVersion is intentional: AWS then stores only the allowlisted
# parameters. Inheriting v5 would retain its AvailabilityZoneId restriction.
status=0
aws_cli ec2 create-launch-template-version --dry-run --launch-template-id lt-028eb222c6fcfffc1 \
    --launch-template-data "file://$work_dir/launch-template-overrides.json" \
    > /dev/null 2> "$work_dir/launch-permission-error" || status=$?
code="$(sed -n 's/^.*An error occurred (\([A-Za-z0-9]*\)) when calling .*$/\1/p' "$work_dir/launch-permission-error")"
if ((status == 0)) || [[ "$code" != DryRunOperation ]]; then
    printf 'Candidate creation authorization failed: %s (status=%s).\n' "${code:-unexpected-response}" "$status" >&2
    exit 1
fi
candidate_version="$(aws_cli ec2 create-launch-template-version \
    --launch-template-id lt-028eb222c6fcfffc1 --version-description "CloudOps $GIT_COMMIT_SHORT clean candidate" \
    --launch-template-data "file://$work_dir/launch-template-overrides.json" \
    --query 'LaunchTemplateVersion.VersionNumber' --output text)"
[[ "$candidate_version" =~ ^[0-9]+$ && "$candidate_version" -gt 5 ]] || { printf 'Invalid candidate version from EC2.\n' >&2; exit 1; }
bash deploy/verify-asg-candidate.sh "$candidate_version" record
bash deploy/validate-launch-permissions.sh "$candidate_version"
printf 'Candidate %s read back and authorized in both subnets; capacity remains unchanged.\n' "$candidate_version"
