#!/usr/bin/env bash
set -Eeuo pipefail
set +x

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

aws_cli elbv2 describe-load-balancers --names alb-load --output json > "$work_dir/alb.json"
alb_arn="$(aws_cli elbv2 describe-load-balancers --names alb-load --query 'LoadBalancers[0].LoadBalancerArn' --output text)"
aws_cli elbv2 describe-target-groups --names tg-cloudops-app --output json > "$work_dir/target-group.json"
aws_cli elbv2 describe-listeners --load-balancer-arn "$alb_arn" --output json > "$work_dir/listeners.json"
listener_arns="$(aws_cli elbv2 describe-listeners --load-balancer-arn "$alb_arn" --query 'Listeners[].ListenerArn' --output text)"
read -r -a listener_array <<< "$listener_arns"
rule_index=0
for listener_arn in "${listener_array[@]}"; do
    ((rule_index += 1))
    aws_cli elbv2 describe-rules --listener-arn "$listener_arn" --output json > "$work_dir/rules-${rule_index}.json"
done

aws_cli ec2 describe-instances --instance-ids i-0c550edbaa5ecbdff --output json > "$work_dir/observability-instance.json"
aws_cli rds describe-db-instances --db-instance-identifier database-1 --output json > "$work_dir/rds.json"
aws_cli logs describe-log-groups --log-group-name-prefix /cloudops/app --output json > "$work_dir/logs.json"
obs_sgs="$(aws_cli ec2 describe-instances --instance-ids i-0c550edbaa5ecbdff --query 'Reservations[].Instances[].SecurityGroups[].GroupId' --output text)"
alb_sgs="$(aws_cli elbv2 describe-load-balancers --names alb-load --query 'LoadBalancers[0].SecurityGroups' --output text)"

if [[ "$mode" == canary ]]; then
    aws_cli ec2 describe-instances --instance-ids i-02777a62f2a65bc1e --output json > "$work_dir/canary-instance.json"
    aws_cli ssm describe-instance-information --filters Key=InstanceIds,Values=i-02777a62f2a65bc1e --output json > "$work_dir/canary-ssm.json"
    app_sgs="$(aws_cli ec2 describe-instances --instance-ids i-02777a62f2a65bc1e --query 'Reservations[].Instances[].SecurityGroups[].GroupId' --output text)"
else
    aws_cli autoscaling describe-auto-scaling-groups --auto-scaling-group-names asg-cloudops-app --output json > "$work_dir/asg.json"
    launch_version="$(aws_cli autoscaling describe-auto-scaling-groups --auto-scaling-group-names asg-cloudops-app --query 'AutoScalingGroups[0].LaunchTemplate.Version' --output text)"
    if [[ ! "$launch_version" =~ ^[0-9]+$ ]]; then
        printf 'ASG does not select an explicit numeric launch-template version.\n' >&2
        exit 1
    fi
    # Persist only reviewed, non-secret LT attributes. AWS UserData can contain sensitive material.
    aws_cli ec2 describe-launch-template-versions \
        --launch-template-id lt-028eb222c6fcfffc1 \
        --versions "$launch_version" \
        --query 'LaunchTemplateVersions[0].{VersionNumber:VersionNumber,LaunchTemplateData:{ImageId:LaunchTemplateData.ImageId,InstanceType:LaunchTemplateData.InstanceType,IamInstanceProfile:LaunchTemplateData.IamInstanceProfile,SecurityGroupIds:LaunchTemplateData.SecurityGroupIds,NetworkInterfaces:LaunchTemplateData.NetworkInterfaces,MetadataOptions:LaunchTemplateData.MetadataOptions,TagSpecifications:LaunchTemplateData.TagSpecifications}}' \
        --output json > "$work_dir/launch-template.json"
    app_sgs="$(aws_cli ec2 describe-launch-template-versions --launch-template-id lt-028eb222c6fcfffc1 --versions "$launch_version" --query 'LaunchTemplateVersions[0].LaunchTemplateData.SecurityGroupIds' --output text)"
    if [[ "$app_sgs" == None ]]; then
        app_sgs="$(aws_cli ec2 describe-launch-template-versions --launch-template-id lt-028eb222c6fcfffc1 --versions "$launch_version" --query 'LaunchTemplateVersions[0].LaunchTemplateData.NetworkInterfaces[].Groups[]' --output text)"
    fi
fi

read -r -a group_array <<< "$alb_sgs $obs_sgs $app_sgs"
aws_cli ec2 describe-security-groups --group-ids "${group_array[@]}" --output json > "$work_dir/security-groups.json"

# Read and validate DB secret JSON through a pipe; never echo or persist the secret string.
aws_cli secretsmanager get-secret-value --secret-id cloudops/prod/mariadb --query SecretString --output text |
    docker run --rm -i "$PYTHON_IMAGE" python -c 'import json,sys; d=json.load(sys.stdin); ok=isinstance(d.get("host"),str) and isinstance(d.get("username"),str) and isinstance(d.get("password"),str) and isinstance(d.get("dbname",d.get("database")),str); port=int(d.get("port",3306)); sys.exit(0 if ok and 0 < port < 65536 else 1)'
aws_cli secretsmanager describe-secret --secret-id cloudops/prod/flask-session-key --query ARN --output text > /dev/null

docker run --rm \
    --volumes-from jenkins \
    --workdir "$WORKSPACE" \
    "$PYTHON_IMAGE" python deploy/cloudops-aws-preflight.py --mode "$mode" --input-dir "$work_dir"

if [[ "$mode" == canary ]]; then
    # SSM Run Command performs secret-safe checks using the target instance role before approval.
    bash deploy/cloudops-ssm-deploy.sh preflight i-02777a62f2a65bc1e
fi
