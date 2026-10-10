#!/usr/bin/env bash
set -Eeuo pipefail
set +x
# Validated non-secret inventory is required even for read-only AWS operations.
source "${WORKSPACE:?}/deploy/load-config.sh"

if (($# < 2)); then
    printf 'Usage: %s preflight|deploy|verify INSTANCE_ID [INSTANCE_ID ...]\n' "$0" >&2
    exit 2
fi
mode="$1"
shift
[[ "$mode" == preflight || "$mode" == deploy || "$mode" == verify ]] || { printf 'Mode must be preflight, deploy, or verify.\n' >&2; exit 2; }
instance_ids=("$@")
if [[ "$mode" == deploy && ${#instance_ids[@]} -ne 1 ]]; then
    printf 'A canary deployment must target exactly one EC2 instance.\n' >&2
    exit 2
fi
if [[ "$mode" == preflight && ${#instance_ids[@]} -ne 1 ]]; then
    printf 'Canary preflight must target exactly one EC2 instance.\n' >&2
    exit 2
fi

: "${WORKSPACE:?Jenkins WORKSPACE is required}"
: "${AWS_CLI_IMAGE:?AWS_CLI_IMAGE is required}"
: "${PYTHON_IMAGE:?PYTHON_IMAGE is required}"
: "${AWS_REGION:?AWS_REGION is required}"
: "${AWS_ACCOUNT_ID:?AWS_ACCOUNT_ID is required}"
: "${AWS_EXPECTED_ROLE:?AWS_EXPECTED_ROLE is required}"
: "${SCM_BRANCH:?SCM_BRANCH is required}"
: "${GIT_COMMIT_FULL:?GIT_COMMIT_FULL is required}"
: "${GIT_COMMIT_SHORT:?GIT_COMMIT_SHORT is required}"
: "${ECR_DEPLOY_IMAGE:?ECR_DEPLOY_IMAGE is required}"
: "${ECR_IMAGE_DIGEST:?ECR_IMAGE_DIGEST is required}"

: "${TARGET_GROUP_ARN:?}"
readonly EXPECTED_TARGET_GROUP="$TARGET_GROUP_ARN"
work_dir="$WORKSPACE/.deploy-work"
mkdir -p "$work_dir"
chmod 700 "$work_dir"

aws_cli() {
    docker run --rm \
        --network host \
        --volumes-from jenkins \
        --workdir "$WORKSPACE" \
        --env AWS_REGION="$AWS_REGION" \
        --env AWS_DEFAULT_REGION="$AWS_REGION" \
        "$AWS_CLI_IMAGE" "$@"
}

[[ "$SCM_BRANCH" == main ]] || { printf 'Refusing SSM deployment: SCM_BRANCH is not main.\n' >&2; exit 1; }
[[ "$GIT_COMMIT_FULL" =~ ^[0-9a-f]{40}$ && "$GIT_COMMIT_SHORT" == "${GIT_COMMIT_FULL:0:12}" ]] || { printf 'Invalid checked-out Git SHA.\n' >&2; exit 1; }
[[ "$ECR_DEPLOY_IMAGE" == "$ECR_URI@$ECR_IMAGE_DIGEST" && "$ECR_IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || { printf 'Invalid ECR image digest.\n' >&2; exit 1; }

head_sha="$(git rev-parse HEAD)"
main_sha="$(git rev-parse 'refs/remotes/origin/main^{commit}')"
[[ "$head_sha" == "$main_sha" && "$head_sha" == "$GIT_COMMIT_FULL" ]] || { printf 'Refusing SSM deployment: HEAD is not the exact origin/main commit.\n' >&2; exit 1; }

caller_arn="$(aws_cli sts get-caller-identity --query Arn --output text)"
caller_account="$(aws_cli sts get-caller-identity --query Account --output text)"
[[ "$caller_account" == "$AWS_ACCOUNT_ID" ]] || { printf 'Refusing SSM deployment: wrong AWS account.\n' >&2; exit 1; }
case "$caller_arn" in
    "arn:aws:sts::${AWS_ACCOUNT_ID}:assumed-role/${AWS_EXPECTED_ROLE}/"*) ;;
    *) printf 'Refusing SSM deployment: expected EC2 role %s.\n' "$AWS_EXPECTED_ROLE" >&2; exit 1 ;;
esac

target_group_arn="$(aws_cli elbv2 describe-target-groups --names "${TARGET_GROUP_NAME}" --query 'TargetGroups[0].TargetGroupArn' --output text)"
[[ "$target_group_arn" == "$EXPECTED_TARGET_GROUP" ]] || { printf 'Unexpected target group ARN.\n' >&2; exit 1; }

deadline=$((SECONDS + 900))
for instance_id in "${instance_ids[@]}"; do
    [[ "$instance_id" =~ ^i-[0-9a-f]{8,17}$ ]] || { printf 'Invalid EC2 instance ID.\n' >&2; exit 2; }
    if [[ "$mode" == deploy && "$instance_id" == "${CANARY_INSTANCE_ID}" ]]; then
        instance_state="$(aws_cli ec2 describe-instances --instance-ids "$instance_id" --query 'Reservations[0].Instances[0].State.Name' --output text)"
        [[ "$instance_state" == running ]] || { printf 'Canary EC2 %s is %s; start and prepare it manually, then retry. Pipeline will not start it.\n' "$instance_id" "$instance_state" >&2; exit 1; }
    fi
    while :; do
        ping_status="$(aws_cli ssm describe-instance-information --filters "Key=InstanceIds,Values=$instance_id" --query 'InstanceInformationList[0].PingStatus' --output text)"
        [[ "$ping_status" == Online ]] && break
        if ((SECONDS >= deadline)); then
            printf 'SSM did not report %s Online within 15 minutes; prepare the instance and SSM agent manually.\n' "$instance_id" >&2
            exit 1
        fi
        sleep 10
    done
done

deploy_sha="$(sha256sum deploy/cloudops-deploy.sh | awk '{print $1}')"
verify_sha="$(sha256sum deploy/cloudops-verify.sh | awk '{print $1}')"
preflight_sha="$(sha256sum deploy/cloudops-canary-preflight.sh | awk '{print $1}')"
ssm_json="$work_dir/ssm-${mode}-command.json"
render_args=(
    --mode "$mode"
    --image "$ECR_DEPLOY_IMAGE"
    --version "$GIT_COMMIT_SHORT"
    --commit "$GIT_COMMIT_FULL"
    --deploy-sha256 "$deploy_sha"
    --verify-sha256 "$verify_sha"
    --preflight-sha256 "$preflight_sha"
    --target-group-arn "$target_group_arn"
    --instance-ids "${instance_ids[@]}"
)
if [[ "$mode" == verify || "$mode" == deploy ]]; then
    render_args+=(--require-target-healthy)
fi
if [[ "${CLOUDOPS_REQUIRE_BOOTSTRAP_COMPLETE:-false}" == true ]]; then
    render_args+=(--require-bootstrap-complete)
fi
docker run --rm \
    --volumes-from jenkins \
    --user "$(id -u):$(id -g)" \
    --workdir "$WORKSPACE" \
    --env CLOUDOPS_CONFIG_FILE "$PYTHON_IMAGE" python deploy/render-ssm-command.py "${render_args[@]}" > "$ssm_json"
chmod 600 "$ssm_json"

command_id="$(aws_cli ssm send-command --cli-input-json "file://$ssm_json" --query 'Command.CommandId' --output text)"
printf 'SSM command submitted: %s (mode=%s, instance-count=%s).\n' "$command_id" "$mode" "${#instance_ids[@]}"
deadline=$((SECONDS + 1500))
while :; do
    statuses="$(aws_cli ssm list-command-invocations --command-id "$command_id" --details --query 'CommandInvocations[].Status' --output text)"
    if [[ -n "$statuses" ]] && ! grep -Eq 'Pending|InProgress|Delayed|Cancelling' <<< "$statuses"; then
        if grep -qv Success <<< "$(tr '\t' '\n' <<< "$statuses")"; then
            for instance_id in "${instance_ids[@]}"; do
                aws_cli ssm get-command-invocation --command-id "$command_id" --instance-id "$instance_id" --query '{Status:Status,Output:StandardOutputContent,Error:StandardErrorContent}' --output json >&2 || true
            done
            printf 'SSM command did not succeed on every requested instance.\n' >&2
            exit 1
        fi
        break
    fi
    if ((SECONDS >= deadline)); then
        printf 'SSM command %s did not finish before the 25-minute timeout.\n' "$command_id" >&2
        exit 1
    fi
    sleep 10
done

for instance_id in "${instance_ids[@]}"; do
    aws_cli ssm get-command-invocation --command-id "$command_id" --instance-id "$instance_id" --query 'StandardOutputContent' --output text
done
