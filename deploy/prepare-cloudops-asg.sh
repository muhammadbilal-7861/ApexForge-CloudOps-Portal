#!/usr/bin/env bash
# Read-only local rendering and validation. No AWS create/update/run-instances calls.
set -Eeuo pipefail
set +x
: "${WORKSPACE:?}" "${PYTHON_IMAGE:?}" "${ECR_DEPLOY_IMAGE:?}" "${GIT_COMMIT_FULL:?}" "${GIT_COMMIT_SHORT:?}"
work_dir="$WORKSPACE/.deploy-work"
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
