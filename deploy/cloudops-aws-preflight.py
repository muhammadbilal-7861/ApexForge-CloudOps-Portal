#!/usr/bin/env python3
"""Validate read-only AWS snapshots before an approved CloudOps deployment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


EXPECTED = {
    "region": "eu-north-1",
    "account": "489502663059",
    "vpc": "vpc-080d46671dbdcacab",
    "app_subnets": {"subnet-08469c4e69b5c4d65", "subnet-05788ba98ca1096e6"},
    "alb_name": "alb-load",
    "target_group_name": "tg-cloudops-app",
    "target_group_port": 5000,
    "target_group_path": "/ready",
    "asg_name": "asg-cloudops-app",
    "launch_template_id": "lt-028eb222c6fcfffc1",
    "ami_id": "ami-09b67ca726bea7328",
    "canary_instance_id": "i-02777a62f2a65bc1e",
    "observability_instance_id": "i-0c550edbaa5ecbdff",
    "instance_profile": "CloudOpsEC2Role",
    "log_group": "/cloudops/app",
}


class PreflightError(ValueError):
    pass


def read_json(directory: Path, name: str) -> dict:
    try:
        return json.loads((directory / name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PreflightError(f"missing or invalid preflight snapshot {name}") from exc


def one(items: list[dict], label: str) -> dict:
    if len(items) != 1:
        raise PreflightError(f"expected exactly one {label}; found {len(items)}")
    return items[0]


def instance_from(payload: dict, instance_id: str) -> dict:
    matches = [
        item
        for reservation in payload.get("Reservations", [])
        for item in reservation.get("Instances", [])
        if item.get("InstanceId") == instance_id
    ]
    return one(matches, f"EC2 instance {instance_id}")


def profile_name(instance: dict) -> str:
    arn = instance.get("IamInstanceProfile", {}).get("Arn", "")
    return arn.rsplit("/", 1)[-1]


def port_5000_sources(groups: list[dict]) -> set[str]:
    sources: set[str] = set()
    for group in groups:
        for permission in group.get("IpPermissions", []):
            protocol = permission.get("IpProtocol")
            start = permission.get("FromPort")
            end = permission.get("ToPort")
            includes_port = protocol == "-1" or (
                protocol in ("tcp", "6") and start is not None and end is not None and start <= 5000 <= end
            )
            if not includes_port:
                continue
            sources.update(pair.get("GroupId", "") for pair in permission.get("UserIdGroupPairs", []))
            if permission.get("IpRanges") or permission.get("Ipv6Ranges") or permission.get("PrefixListIds"):
                raise PreflightError("application port 5000 is exposed through a CIDR/prefix-list ingress rule")
    sources.discard("")
    return sources


def forward_target_arns(listeners: dict, rule_documents: list[dict]) -> set[str]:
    arns: set[str] = set()
    all_rules = [rule for document in rule_documents for rule in document.get("Rules", [])]
    for listener in listeners.get("Listeners", []):
        actions = list(listener.get("DefaultActions", []))
        for rule in all_rules:
            actions.extend(rule.get("Actions", []))
        for action in actions:
            arn = action.get("TargetGroupArn")
            if arn:
                arns.add(arn)
            arns.update(
                target.get("TargetGroupArn", "")
                for target in action.get("ForwardConfig", {}).get("TargetGroups", [])
            )
    return {arn for arn in arns if arn}


def validate_common(directory: Path, mode: str) -> tuple[str, list[str]]:
    alb = one(read_json(directory, "alb.json").get("LoadBalancers", []), "ALB")
    if alb.get("LoadBalancerName") != EXPECTED["alb_name"] or alb.get("VpcId") != EXPECTED["vpc"]:
        raise PreflightError("alb-load is missing or is outside the expected VPC")
    target_group = one(read_json(directory, "target-group.json").get("TargetGroups", []), "target group")
    if (
        target_group.get("TargetGroupName") != EXPECTED["target_group_name"]
        or target_group.get("Port") != EXPECTED["target_group_port"]
        or target_group.get("HealthCheckPath") != EXPECTED["target_group_path"]
        or target_group.get("VpcId") != EXPECTED["vpc"]
        or alb.get("LoadBalancerArn") not in target_group.get("LoadBalancerArns", [])
    ):
        raise PreflightError("existing target group name, VPC, port, health path, or ALB association differs from the approved architecture")
    reachable = forward_target_arns(
        read_json(directory, "listeners.json"),
        [read_json(directory, path.name) for path in sorted(directory.glob("rules-*.json"))],
    )
    if target_group.get("TargetGroupArn") not in reachable:
        raise PreflightError("alb-load listeners/rules do not forward to tg-cloudops-app")
    db_status = read_json(directory, "rds.json").get("DBInstances", [{}])[0].get("DBInstanceStatus")
    if db_status != "available":
        raise PreflightError("database-1 is not available; deployment/readiness checks are blocked until RDS is healthy")
    log_groups = read_json(directory, "logs.json").get("logGroups", [])
    if not any(item.get("logGroupName") == EXPECTED["log_group"] for item in log_groups):
        raise PreflightError("pre-created /cloudops/app CloudWatch log group is missing")

    obs = instance_from(read_json(directory, "observability-instance.json"), EXPECTED["observability_instance_id"])
    obs_sgs = {item["GroupId"] for item in obs.get("SecurityGroups", [])}
    alb_sgs = set(alb.get("SecurityGroups", []))
    if not obs_sgs or not alb_sgs:
        raise PreflightError("could not determine ALB and observability security groups")
    permitted_sources = alb_sgs | obs_sgs

    group_map = {
        group.get("GroupId"): group
        for group in read_json(directory, "security-groups.json").get("SecurityGroups", [])
    }
    if mode == "canary":
        app_instance = instance_from(read_json(directory, "canary-instance.json"), EXPECTED["canary_instance_id"])
        if app_instance.get("State", {}).get("Name") != "running":
            raise PreflightError("canary EC2 is not running; start and prepare the existing instance, then wait for SSM Online. Pipeline will not start it automatically.")
        if app_instance.get("VpcId") != EXPECTED["vpc"] or app_instance.get("SubnetId") not in EXPECTED["app_subnets"]:
            raise PreflightError("canary EC2 is outside the approved private application subnets")
        if app_instance.get("MetadataOptions", {}).get("HttpTokens") != "required":
            raise PreflightError("canary EC2 must require IMDSv2 before using its instance role from the host-network container")
        if profile_name(app_instance) != EXPECTED["instance_profile"]:
            raise PreflightError("canary EC2 is not using the expected CloudOpsEC2Role instance profile")
        ping = read_json(directory, "canary-ssm.json").get("InstanceInformationList", [])
        if len(ping) != 1 or ping[0].get("PingStatus") != "Online":
            raise PreflightError("canary SSM agent is not Online; prepare/repair SSM before requesting deployment")
        app_sgs = {item["GroupId"] for item in app_instance.get("SecurityGroups", [])}
    else:
        asg = one(read_json(directory, "asg.json").get("AutoScalingGroups", []), "Auto Scaling group")
        if asg.get("AutoScalingGroupName") != EXPECTED["asg_name"]:
            raise PreflightError("unexpected Auto Scaling group")
        subnet_ids = set((asg.get("VPCZoneIdentifier") or "").split(","))
        if subnet_ids != EXPECTED["app_subnets"]:
            raise PreflightError("ASG subnets differ from the two approved private subnets")
        if target_group.get("TargetGroupArn") not in asg.get("TargetGroupARNs", []):
            raise PreflightError("ASG is not attached to the existing tg-cloudops-app")
        launch_template = asg.get("LaunchTemplate", {})
        if launch_template.get("LaunchTemplateId") != EXPECTED["launch_template_id"]:
            raise PreflightError("ASG does not use the expected launch template")
        version = str(launch_template.get("Version", ""))
        if not version.isdigit():
            raise PreflightError("ASG launch template version must be an explicit numeric version")
        template = read_json(directory, "launch-template.json")
        data = template.get("LaunchTemplateData", {})
        if str(template.get("VersionNumber")) != version:
            raise PreflightError("launch-template snapshot is not the version currently used by the ASG")
        if data.get("ImageId") != EXPECTED["ami_id"] or data.get("InstanceType") != "t3.micro":
            raise PreflightError("ASG AMI or instance type differs from the reviewed architecture")
        if data.get("IamInstanceProfile", {}).get("Name") != EXPECTED["instance_profile"]:
            raise PreflightError("launch template does not use CloudOpsEC2Role")
        app_sgs = set(data.get("SecurityGroupIds", []))
        for interface in data.get("NetworkInterfaces", []):
            app_sgs.update(interface.get("Groups", []))
        if not app_sgs:
            raise PreflightError("launch template has no identifiable application security group")
        capacities = (asg.get("MinSize"), asg.get("DesiredCapacity"), asg.get("MaxSize"))
        if capacities not in ((0, 0, 0),) and not (0 <= capacities[0] <= capacities[1] <= capacities[2]):
            raise PreflightError("ASG capacity values are inconsistent")

    if not app_sgs.issubset(group_map):
        raise PreflightError("one or more application security groups were not inventoried")
    observed_sources = port_5000_sources([group_map[group_id] for group_id in app_sgs])
    if observed_sources != permitted_sources:
        raise PreflightError(
            "port 5000 security-group sources must be exactly the alb-load and authorized observability SGs; "
            f"observed {sorted(observed_sources)}"
        )
    return target_group["TargetGroupArn"], sorted(app_sgs)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("canary", "asg"), required=True)
    parser.add_argument("--input-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        target_group_arn, security_groups = validate_common(args.input_dir, args.mode)
    except PreflightError as exc:
        print(f"Deployment preflight failed: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps({"target_group_arn": target_group_arn, "app_security_groups": security_groups}, separators=(",", ":")))


if __name__ == "__main__":
    main()
