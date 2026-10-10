#!/usr/bin/env bash
set -Eeuo pipefail
set +x
# Validated non-secret inventory is required even for read-only AWS operations.
source "${WORKSPACE:?}/deploy/load-config.sh"

if (($# != 1)) || [[ "$1" != canary && "$1" != asg ]]; then
    printf 'Usage: %s canary|asg\n' "$0" >&2
    exit 2
fi
mode="$1"
: "${WORKSPACE:?Jenkins WORKSPACE is required}"
: "${AWS_CLI_IMAGE:?AWS_CLI_IMAGE is required}"
: "${PYTHON_IMAGE:?PYTHON_IMAGE is required}"
: "${AWS_REGION:?AWS_REGION is required}"

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

aws_cli elbv2 describe-load-balancers --names "${ALB_NAME}" --output json > "$work_dir/alb.json"
alb_arn="$(aws_cli elbv2 describe-load-balancers --names "${ALB_NAME}" --query 'LoadBalancers[0].LoadBalancerArn' --output text)"
aws_cli elbv2 describe-target-groups --names "${TARGET_GROUP_NAME}" --output json > "$work_dir/target-group.json"
aws_cli elbv2 describe-listeners --load-balancer-arn "$alb_arn" --output json > "$work_dir/listeners.json"
listener_arns="$(aws_cli elbv2 describe-listeners --load-balancer-arn "$alb_arn" --query 'Listeners[].ListenerArn' --output text)"
read -r -a listener_array <<< "$listener_arns"
rule_index=0
for listener_arn in "${listener_array[@]}"; do
    ((rule_index += 1))
    aws_cli elbv2 describe-rules --listener-arn "$listener_arn" --output json > "$work_dir/rules-${rule_index}.json"
done

aws_cli ec2 describe-instances --instance-ids "${OBSERVABILITY_INSTANCE_ID}" --output json > "$work_dir/observability-instance.json"
aws_cli rds describe-db-instances --db-instance-identifier "${RDS_INSTANCE_ID}" --output json > "$work_dir/rds.json"
aws_cli logs describe-log-groups --log-group-name-prefix "${LOG_GROUP_NAME}" --output json > "$work_dir/logs.json"
obs_sgs="$(aws_cli ec2 describe-instances --instance-ids "${OBSERVABILITY_INSTANCE_ID}" --query 'Reservations[].Instances[].SecurityGroups[].GroupId' --output text)"
alb_sgs="$(aws_cli elbv2 describe-load-balancers --names "${ALB_NAME}" --query 'LoadBalancers[0].SecurityGroups' --output text)"

if [[ "$mode" == canary ]]; then
    aws_cli ec2 describe-instances --instance-ids "${CANARY_INSTANCE_ID}" --output json > "$work_dir/canary-instance.json"
    aws_cli ssm describe-instance-information --filters "Key=InstanceIds,Values=${CANARY_INSTANCE_ID}" --output json > "$work_dir/canary-ssm.json"
    app_sgs="$(aws_cli ec2 describe-instances --instance-ids "${CANARY_INSTANCE_ID}" --query 'Reservations[].Instances[].SecurityGroups[].GroupId' --output text)"
else
    aws_cli autoscaling describe-auto-scaling-groups --auto-scaling-group-names "${ASG_NAME}" --output json > "$work_dir/asg.json"
    launch_version="$(aws_cli autoscaling describe-auto-scaling-groups --auto-scaling-group-names "${ASG_NAME}" --query 'AutoScalingGroups[0].LaunchTemplate.Version' --output text)"
    if [[ ! "$launch_version" =~ ^[0-9]+$ ]]; then
        printf 'ASG does not select an explicit numeric launch-template version.\n' >&2
        exit 1
    fi
    # Persist only reviewed, non-secret LT attributes. AWS UserData can contain sensitive material.
    aws_cli ec2 describe-launch-template-versions \
        --launch-template-id "${LAUNCH_TEMPLATE_ID}" \
        --versions "$launch_version" \
        --query 'LaunchTemplateVersions[0].{VersionNumber:VersionNumber,LaunchTemplateData:LaunchTemplateData.{ImageId:ImageId,InstanceType:InstanceType,IamInstanceProfile:IamInstanceProfile,SecurityGroupIds:SecurityGroupIds,NetworkInterfaces:NetworkInterfaces,MetadataOptions:MetadataOptions,TagSpecifications:TagSpecifications,BlockDeviceMappings:BlockDeviceMappings,Placement:Placement}}' \
        --output json > "$work_dir/launch-template.json"
    # v5 is an immutable configuration source only; never reuse its AMI/UserData.
    aws_cli ec2 describe-launch-template-versions --launch-template-id "${LAUNCH_TEMPLATE_ID}" --versions 5 \
        --query 'LaunchTemplateVersions[0].{VersionNumber:VersionNumber,LaunchTemplateData:LaunchTemplateData.{ImageId:ImageId,InstanceType:InstanceType,IamInstanceProfile:IamInstanceProfile,SecurityGroupIds:SecurityGroupIds,NetworkInterfaces:NetworkInterfaces,MetadataOptions:MetadataOptions,TagSpecifications:TagSpecifications,BlockDeviceMappings:BlockDeviceMappings,Placement:Placement}}' \
        --output json > "$work_dir/launch-template-source.json"
    reviewed_ami="$REVIEWED_AMI_ID"
    reviewed_parameter="$REVIEWED_SSM_PARAMETER"
    aws_cli ec2 describe-images --image-ids "$reviewed_ami" --owners 099720109477 --output json > "$work_dir/reviewed-ami.json"
    aws_cli ssm get-parameter --name "$reviewed_parameter" --output json > "$work_dir/reviewed-ami-parameter.json"
    app_sgs="$(aws_cli ec2 describe-launch-template-versions --launch-template-id "${LAUNCH_TEMPLATE_ID}" --versions "$launch_version" --query 'LaunchTemplateVersions[0].LaunchTemplateData.SecurityGroupIds' --output text)"
    if [[ "$app_sgs" == None ]]; then
        app_sgs="$(aws_cli ec2 describe-launch-template-versions --launch-template-id "${LAUNCH_TEMPLATE_ID}" --versions "$launch_version" --query 'LaunchTemplateVersions[0].LaunchTemplateData.NetworkInterfaces[].Groups[]' --output text)"
    fi
fi

read -r -a group_array <<< "$alb_sgs $obs_sgs $app_sgs"
aws_cli ec2 describe-security-groups --group-ids "${group_array[@]}" --output json > "$work_dir/security-groups.json"

# Read and validate DB secret JSON through a pipe; never echo or persist the secret string.
aws_cli secretsmanager get-secret-value --secret-id "${DB_SECRET_NAME}" --query SecretString --output text |
    docker run --rm -i "$PYTHON_IMAGE" python -c 'import json,sys; d=json.load(sys.stdin); ok=isinstance(d.get("host"),str) and isinstance(d.get("username"),str) and isinstance(d.get("password"),str) and isinstance(d.get("dbname",d.get("database")),str); port=int(d.get("port",3306)); sys.exit(0 if ok and 0 < port < 65536 else 1)'
aws_cli secretsmanager describe-secret --secret-id "${SESSION_SECRET_NAME}" --query ARN --output text > /dev/null

docker run --rm \
    --volumes-from jenkins \
    --workdir "$WORKSPACE" \
    --env CLOUDOPS_CONFIG_FILE "$PYTHON_IMAGE" python deploy/cloudops-aws-preflight.py --mode "$mode" --input-dir "$work_dir"

if [[ "$mode" == canary ]]; then
    # SSM Run Command performs secret-safe checks using the target instance role before approval.
    bash deploy/cloudops-ssm-deploy.sh preflight "${CANARY_INSTANCE_ID}"
fi
if [[ "$mode" == asg ]]; then
    if [[ -n "${ASG_VALIDATED_VERSION:-}" ]]; then
        # After approval, refresh discovery without creating another candidate.
        bash deploy/validate-launch-permissions.sh "$ASG_VALIDATED_VERSION"
    else
        bash deploy/prepare-cloudops-asg.sh
    fi
fi
