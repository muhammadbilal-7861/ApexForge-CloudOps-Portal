#!/usr/bin/env bash
set -Eeuo pipefail
set +x

: "${WORKSPACE:?Jenkins WORKSPACE is required}"
: "${AWS_CLI_IMAGE:?AWS_CLI_IMAGE is required}"
: "${PYTHON_IMAGE:?PYTHON_IMAGE is required}"
: "${AWS_REGION:?AWS_REGION is required}"
: "${ECR_DEPLOY_IMAGE:?ECR_DEPLOY_IMAGE is required}"
: "${GIT_COMMIT_SHORT:?GIT_COMMIT_SHORT is required}"
: "${GIT_COMMIT_FULL:?GIT_COMMIT_FULL is required}"
: "${ASG_AMI_REVIEWED:?ASG_AMI_REVIEWED is required}"

[[ "$ASG_AMI_REVIEWED" == true ]] || { printf 'Set ASG_AMI_REVIEWED only after verifying the selected AMI is sanitized and contains no credentials or application state.\n' >&2; exit 1; }
bash deploy/assert-deploy-context.sh
bash deploy/collect-cloudops-preflight.sh asg

work_dir="$WORKSPACE/.deploy-work"
asg_name=asg-cloudops-app
launch_template_id=lt-028eb222c6fcfffc1
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
        "$PYTHON_IMAGE" python "$@"
}

read -r old_min old_desired old_max < <(aws_cli autoscaling describe-auto-scaling-groups \
    --auto-scaling-group-names "$asg_name" \
    --query 'AutoScalingGroups[0].[MinSize,DesiredCapacity,MaxSize]' \
    --output text)
old_version="$(aws_cli autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$asg_name" --query 'AutoScalingGroups[0].LaunchTemplate.Version' --output text)"
[[ "$old_version" =~ ^[0-9]+$ ]] || { printf 'Refusing ASG rollout: prior launch-template version is not numeric.\n' >&2; exit 1; }
if ((old_desired == 0)) && ! ((old_min == 0 && old_max == 0)); then
    printf 'ASG desired capacity is zero but min/max are not all zero; inspect capacity before deployment.\n' >&2
    exit 1
fi

target_group_arn="$(aws_cli elbv2 describe-target-groups --names tg-cloudops-app --query 'TargetGroups[0].TargetGroupArn' --output text)"
user_data_file="$work_dir/cloudops-user-data.sh"
userdata_b64_file="$work_dir/cloudops-user-data.b64"
overrides_file="$work_dir/launch-template-overrides.json"
chmod 700 "$work_dir"

python_cli deploy/render-cloudops-user-data.py \
    --template deploy/cloudops-user-data.sh.tmpl \
    --deploy-script deploy/cloudops-deploy.sh \
    --verify-script deploy/cloudops-verify.sh \
    --image "$ECR_DEPLOY_IMAGE" \
    --version "$GIT_COMMIT_SHORT" \
    --commit "$GIT_COMMIT_FULL" \
    --output "$user_data_file"
base64 -w0 "$user_data_file" > "$userdata_b64_file"
python_cli deploy/render-launch-template-data.py \
    --source-data "$work_dir/launch-template.json" \
    --user-data "$userdata_b64_file" \
    --version "$GIT_COMMIT_SHORT" \
    --output "$overrides_file"

new_version="$(aws_cli ec2 create-launch-template-version \
    --launch-template-id "$launch_template_id" \
    --source-version "$old_version" \
    --version-description "CloudOps $GIT_COMMIT_SHORT" \
    --launch-template-data "file://$overrides_file" \
    --query 'LaunchTemplateVersion.VersionNumber' \
    --output text)"
[[ "$new_version" =~ ^[0-9]+$ ]] || { printf 'EC2 did not return an explicit numeric launch-template version.\n' >&2; exit 1; }
printf 'Created launch-template version %s from prior explicit version %s.\n' "$new_version" "$old_version"

rollback_asg() {
    local reason="$1"
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

if ((old_min == 0 && old_desired == 0 && old_max == 0)); then
    # Initial capacity is raised only after source, IAM, RDS, target wiring, subnet, AMI-review, and SG preflights plus manual approval.
    if ! aws_cli autoscaling update-auto-scaling-group \
        --auto-scaling-group-name "$asg_name" \
        --launch-template "LaunchTemplateId=$launch_template_id,Version=$new_version" \
        --min-size 1 --desired-capacity 2 --max-size 2; then
        rollback_asg 'initial capacity update failed' || true
        exit 1
    fi
    expected_targets=2
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
fi

target_deadline=$((SECONDS + 1800))
while :; do
    healthy_count="$(aws_cli elbv2 describe-target-health --target-group-arn "$target_group_arn" \
        --query "length(TargetHealthDescriptions[?TargetHealth.State=='healthy'])" --output text)"
    if [[ "$healthy_count" =~ ^[0-9]+$ ]] && ((healthy_count >= expected_targets)); then
        break
    fi
    if ((SECONDS >= target_deadline)); then
        rollback_asg "only $healthy_count of $expected_targets targets became healthy" || true
        exit 1
    fi
    sleep 15
done

# JMESPath uses backticks inside its single-quoted query expression.
# shellcheck disable=SC2016
instance_ids="$(aws_cli autoscaling describe-auto-scaling-groups --auto-scaling-group-names "$asg_name" \
    --query 'AutoScalingGroups[0].Instances[?LifecycleState==`InService`].InstanceId' --output text)"
read -r -a in_service_ids <<< "$instance_ids"
if ((${#in_service_ids[@]} != expected_targets)); then
    rollback_asg "expected $expected_targets InService instances, found ${#in_service_ids[@]}" || true
    exit 1
fi

if ! bash deploy/cloudops-ssm-deploy.sh verify "${in_service_ids[@]}"; then
    rollback_asg 'per-instance immutable image/health/target verification failed' || true
    exit 1
fi

mkdir -p "$WORKSPACE/reports"
printf '{"target":"asg","launchTemplateId":"%s","launchTemplateVersion":"%s","image":"%s","healthyTargets":%s,"instances":[' \
    "$launch_template_id" "$new_version" "$ECR_DEPLOY_IMAGE" "$healthy_count" > "$WORKSPACE/reports/deployment-evidence.json"
separator=""
for instance_id in "${in_service_ids[@]}"; do
    printf '%s"%s"' "$separator" "$instance_id" >> "$WORKSPACE/reports/deployment-evidence.json"
    separator=,
done
printf ']}\n' >> "$WORKSPACE/reports/deployment-evidence.json"
printf 'ASG deployment verified: %s target(s) healthy on explicit launch-template version %s and image %s.\n' \
    "$healthy_count" "$new_version" "$ECR_DEPLOY_IMAGE"
