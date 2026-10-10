#!/usr/bin/env bash
set -Eeuo pipefail
set +x
# Validated non-secret inventory is required even for read-only AWS operations.
source "${WORKSPACE:?}/deploy/load-config.sh"

: "${WORKSPACE:?Jenkins WORKSPACE is required}"
: "${AWS_CLI_IMAGE:?AWS_CLI_IMAGE is required}"
: "${PYTHON_IMAGE:?PYTHON_IMAGE is required}"
: "${AWS_REGION:?AWS_REGION is required}"
: "${ECR_DEPLOY_IMAGE:?ECR_DEPLOY_IMAGE is required}"
: "${GIT_COMMIT_SHORT:?GIT_COMMIT_SHORT is required}"
: "${GIT_COMMIT_FULL:?GIT_COMMIT_FULL is required}"
: "${ASG_AMI_REVIEWED:?ASG_AMI_REVIEWED is required}"
: "${ASG_VALIDATED_VERSION:?The exact preapproved candidate version is required}"

[[ "$ASG_AMI_REVIEWED" == true ]] || { printf 'Review the pinned official Ubuntu 24.04 LTS provenance and launch-template preview before approving rollout.\n' >&2; exit 1; }
bash deploy/assert-deploy-context.sh
bash deploy/collect-cloudops-preflight.sh asg

work_dir="$WORKSPACE/.deploy-work"
: "${ASG_NAME:?}" "${LAUNCH_TEMPLATE_ID:?}"
asg_name="$ASG_NAME"
launch_template_id="$LAUNCH_TEMPLATE_ID"
aws_cli() {
    docker run --rm \
        --network host \
        --volumes-from jenkins \
        --workdir "$WORKSPACE" \
        --env AWS_REGION="$AWS_REGION" \
        --env AWS_DEFAULT_REGION="$AWS_REGION" \
        "$AWS_CLI_IMAGE" "$@"
}
python_cli() {
    docker run --rm \
        --volumes-from jenkins \
        --user "$(id -u):$(id -g)" \
        --workdir "$WORKSPACE" \
        --env CLOUDOPS_CONFIG_FILE "$PYTHON_IMAGE" python "$@"
}

capacity_snapshot="$(aws_cli autoscaling describe-auto-scaling-groups \
    --auto-scaling-group-names "$asg_name" \
    --query 'AutoScalingGroups[0].[MinSize,DesiredCapacity,MaxSize]' \
    --output text)"
read -r old_min old_desired old_max <<< "$capacity_snapshot"
[[ "$old_min" =~ ^[0-9]+$ && "$old_desired" =~ ^[0-9]+$ && "$old_max" =~ ^[0-9]+$ ]] || { printf 'Invalid ASG capacity snapshot.\n' >&2; exit 1; }
old_version="$(aws_cli autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$asg_name" --query 'AutoScalingGroups[0].LaunchTemplate.Version' --output text)"
[[ "$old_version" =~ ^[0-9]+$ ]] || { printf 'Refusing ASG rollout: prior launch-template version is not numeric.\n' >&2; exit 1; }
if ((old_desired == 0)) && ! ((old_min == 0 && old_max == 0)); then
    printf 'ASG desired capacity is zero but min/max are not all zero; inspect capacity before deployment.\n' >&2
    exit 1
fi

target_group_arn="$(aws_cli elbv2 describe-target-groups --names "${TARGET_GROUP_NAME}" --query 'TargetGroups[0].TargetGroupArn' --output text)"
overrides_file="$work_dir/launch-template-overrides.json"
chmod 700 "$work_dir"

# collect-cloudops-preflight already rendered and validated these overrides.
[[ -s "$overrides_file" && -s "$WORKSPACE/reports/asg-launch-template-preview.json" ]] || { printf 'Missing reviewed launch-template preview.\n' >&2; exit 1; }

# Reuse the version bound to the preapproval record and release, never create
# a replacement after input. collect-cloudops-preflight re-read and validated it.
new_version="$ASG_VALIDATED_VERSION"
[[ "$new_version" =~ ^[0-9]+$ && "$new_version" -gt 5 ]] || { printf 'Invalid approved candidate version.\n' >&2; exit 1; }
printf 'Reusing approved clean launch-template version %s; prior ASG version is %s.\n' "$new_version" "$old_version"

rollback_asg() {
    local reason="$1"
    if ((old_min == 0 && old_desired == 0 && old_max == 0)); then
        local current_snapshot current_min current_desired current_max current_version
        current_snapshot="$(aws_cli autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$asg_name" \
            --query 'AutoScalingGroups[0].[MinSize,DesiredCapacity,MaxSize,LaunchTemplate.Version]' --output text)" || return 1
        read -r current_min current_desired current_max current_version <<< "$current_snapshot"
        if [[ "$current_min $current_desired $current_max $current_version" == "0 0 0 $old_version" ]]; then
            printf 'ASG rollout failed: %s. Original 0/0/0 configuration is unchanged; no rollback update needed.\n' "$reason" >&2
            return 0
        fi
        # The restricted RunInstances policy intentionally excludes the unsafe v5
        # AMI. Keep the current clean template when draining an initial rollout;
        # restoring v5 could fail authorization even with desired capacity zero.
        [[ "$current_version" =~ ^[0-9]+$ && "$current_version" == "$new_version" ]] || {
            printf 'Initial rollback found an unexpected launch version; refusing to overwrite concurrent configuration.\n' >&2
            return 1
        }
        printf 'ASG rollout failed: %s. Restoring capacity 0/0/0 while retaining clean template version %s.\n' "$reason" "$current_version" >&2
        aws_cli autoscaling update-auto-scaling-group --auto-scaling-group-name "$asg_name" \
            --min-size 0 --desired-capacity 0 --max-size 0 || return 1
        return 0
    fi
    printf 'ASG rollout failed: %s. Restoring prior numeric launch-template version %s and capacity %s/%s/%s.\n' \
        "$reason" "$old_version" "$old_min" "$old_desired" "$old_max" >&2
    aws_cli autoscaling update-auto-scaling-group \
        --auto-scaling-group-name "$asg_name" \
        --launch-template "LaunchTemplateId=$launch_template_id,Version=$old_version" \
        --min-size "$old_min" --desired-capacity "$old_desired" --max-size "$old_max" || return 1
    if ((old_desired > 0)); then
        local old_desired_json="$work_dir/rollback-desired.json"
        printf '{"LaunchTemplate":{"LaunchTemplateId":"%s","Version":"%s"}}\n' \
            "$launch_template_id" "$old_version" > "$old_desired_json"
        aws_cli autoscaling start-instance-refresh \
            --auto-scaling-group-name "$asg_name" \
            --strategy Rolling \
            --desired-configuration "file://$old_desired_json" \
            --preferences '{"MinHealthyPercentage":50,"InstanceWarmup":300,"AutoRollback":true}' >/dev/null || {
                printf 'Automatic instance refresh rollback could not be started; inspect ASG and target health immediately.\n' >&2
                return 1
            }
    fi
    printf 'Prior ASG launch version and capacity have been restored; verify healthy targets before further changes.\n' >&2
}

# Recheck RunInstances/CreateTags/PassRole authorization for both subnets with
# the new numeric version before any capacity or Instance Refresh operation.
bash deploy/validate-launch-permissions.sh "$new_version"

if ((old_min == 0 && old_desired == 0 && old_max == 0)); then
    # With the healthy canary retained, the reviewed 8-vCPU regional budget
    # allows one additional t3.micro (2 vCPUs). Never retire the canary here.
    # Raise initial capacity only after security preflight and manual approval.
    if ! aws_cli autoscaling update-auto-scaling-group \
        --auto-scaling-group-name "$asg_name" \
        --launch-template "LaunchTemplateId=$launch_template_id,Version=$new_version" \
        --min-size 1 --desired-capacity 1 --max-size 1; then
        rollback_asg 'initial capacity update failed' || true
        exit 1
    fi
    expected_targets=1
    target_min=1 target_desired=1 target_max=1
else
    ((old_desired > 0)) || { printf 'Only the initial ASG state 0/0/0 or an already-running group can be deployed.\n' >&2; exit 1; }
    desired_file="$work_dir/desired-configuration.json"
    printf '{"LaunchTemplate":{"LaunchTemplateId":"%s","Version":"%s"}}\n' \
        "$launch_template_id" "$new_version" > "$desired_file"
    if ! aws_cli autoscaling start-instance-refresh \
        --auto-scaling-group-name "$asg_name" \
        --strategy Rolling \
        --desired-configuration "file://$desired_file" \
        --preferences '{"MinHealthyPercentage":50,"InstanceWarmup":300,"AutoRollback":true}' \
        --query InstanceRefreshId --output text > "$work_dir/instance-refresh-id"; then
        rollback_asg 'StartInstanceRefresh failed' || true
        exit 1
    fi
    refresh_id="$(<"$work_dir/instance-refresh-id")"
    [[ -n "$refresh_id" && "$refresh_id" != None ]] || { rollback_asg 'no instance refresh ID returned' || true; exit 1; }
    refresh_deadline=$((SECONDS + 2400))
    while :; do
        refresh_status="$(aws_cli autoscaling describe-instance-refreshes \
            --auto-scaling-group-name "$asg_name" \
            --instance-refresh-ids "$refresh_id" \
            --query 'InstanceRefreshes[0].Status' --output text)"
        case "$refresh_status" in
            Successful) break ;;
            Failed|Cancelled|RollbackFailed|RollbackSuccessful)
                rollback_asg "instance refresh status $refresh_status" || true
                exit 1
                ;;
            Pending|InProgress|Cancelling|RollbackInProgress) ;;
            *) rollback_asg "unexpected instance refresh status $refresh_status" || true; exit 1 ;;
        esac
        if ((SECONDS >= refresh_deadline)); then
            rollback_asg 'instance refresh timed out' || true
            exit 1
        fi
        sleep 15
    done
    expected_targets="$old_desired"
    target_min="$old_min" target_desired="$old_desired" target_max="$old_max"
fi

target_deadline=$((SECONDS + 1800))
while :; do
    # Count only this ASG's instances, never the healthy independent canary.
    # shellcheck disable=SC2016
    instance_ids="$(aws_cli autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$asg_name" \
        --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService`].InstanceId' --output text)" || { rollback_asg 'ASG discovery failed' || true; exit 1; }
    read -r -a in_service_ids <<< "$instance_ids"
    healthy_count=0
    for instance_id in "${in_service_ids[@]}"; do
        [[ "$instance_id" =~ ^i-[0-9a-f]{8,17}$ ]] || { rollback_asg 'invalid ASG instance identity' || true; exit 1; }
        target_state="$(aws_cli elbv2 describe-target-health --target-group-arn "$target_group_arn" \
            --targets "Id=$instance_id,Port=5000" --query 'TargetHealthDescriptions[0].TargetHealth.State' --output text)" || { rollback_asg 'target health API failed' || true; exit 1; }
        if [[ "$target_state" == healthy ]]; then ((healthy_count += 1)); fi
    done
    if ((${#in_service_ids[@]} == expected_targets && healthy_count == expected_targets)); then
        break
    fi
    if ((SECONDS >= target_deadline)); then
        rollback_asg "only $healthy_count of $expected_targets targets became healthy" || true
        exit 1
    fi
    sleep 15
done

if ! CLOUDOPS_REQUIRE_BOOTSTRAP_COMPLETE=true bash deploy/cloudops-ssm-deploy.sh verify "${in_service_ids[@]}"; then
    rollback_asg 'per-instance immutable image/health/target verification failed' || true
    exit 1
fi

mkdir -p "$WORKSPACE/reports"
printf '{"target":"asg","launchTemplateId":"%s","launchTemplateVersion":"%s","image":"%s","expectedTargets":%s,"healthyTargets":%s,"capacity":{"min":%s,"desired":%s,"max":%s},"perInstanceVerification":"passed","canaryRetired":false,"instances":[' \
    "$launch_template_id" "$new_version" "$ECR_DEPLOY_IMAGE" "$expected_targets" "$healthy_count" \
    "$target_min" "$target_desired" "$target_max" > "$WORKSPACE/reports/deployment-evidence.json"
separator=""
for instance_id in "${in_service_ids[@]}"; do
    printf '%s"%s"' "$separator" "$instance_id" >> "$WORKSPACE/reports/deployment-evidence.json"
    separator=,
done
printf ']}\n' >> "$WORKSPACE/reports/deployment-evidence.json"
printf 'ASG deployment verified: %s target(s) healthy on explicit launch-template version %s and image %s.\n' \
    "$healthy_count" "$new_version" "$ECR_DEPLOY_IMAGE"
