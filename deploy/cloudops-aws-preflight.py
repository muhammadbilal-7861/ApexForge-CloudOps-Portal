#!/usr/bin/env python3
"""Validate read-only AWS snapshots before an approved CloudOps deployment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from cloudops_config import load

CONFIG = load()


EXPECTED = {
    "region": CONFIG["AWS_REGION"], "account": CONFIG["AWS_ACCOUNT_ID"],
    "vpc": CONFIG["VPC_ID"], "app_subnets": set(CONFIG["PRIVATE_SUBNET_IDS"]),
    "alb_name": CONFIG["ALB_NAME"], "target_group_name": CONFIG["TARGET_GROUP_NAME"],
    "target_group_port": 5000, "target_group_path": "/ready",
    "canary_instance_id": CONFIG["CANARY_INSTANCE_ID"],
    "observability_instance_id": CONFIG["OBSERVABILITY_INSTANCE_ID"],
    "instance_profile": CONFIG["INSTANCE_PROFILE_NAME"], "log_group": CONFIG["LOG_GROUP_NAME"],
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
        or target_group.get("TargetGroupArn") != CONFIG["TARGET_GROUP_ARN"]
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
    database = one(read_json(directory, "rds.json").get("DBInstances", []), "MariaDB instance")
    if (database.get("DBInstanceIdentifier") != CONFIG["RDS_INSTANCE_ID"] or
            database.get("DBInstanceStatus") != "available" or database.get("Engine") != "mariadb" or
            database.get("MultiAZ") is not False or database.get("PubliclyAccessible") is not False):
        raise PreflightError("expected one available private Single-AZ MariaDB instance")
    db_group = database.get("DBSubnetGroup", {})
    db_subnets = db_group.get("Subnets", [])
    if (db_group.get("VpcId") != EXPECTED["vpc"] or len(db_subnets) != 2 or
            len({item.get("SubnetIdentifier") for item in db_subnets}) != 2 or
            len({item.get("SubnetAvailabilityZone", {}).get("Name") for item in db_subnets}) != 2 or
            any(not item.get("SubnetIdentifier") or not item.get("SubnetAvailabilityZone", {}).get("Name") for item in db_subnets)):
        raise PreflightError("DB subnet group must contain two distinct subnets in two AZs in the approved VPC")
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
    app_instance = instance_from(read_json(directory, "canary-instance.json"), EXPECTED["canary_instance_id"])
    if app_instance.get("State", {}).get("Name") != "running":
        raise PreflightError("canary EC2 is not running; start and prepare the existing instance, then wait for SSM Online. Pipeline will not start it automatically.")
    if app_instance.get("VpcId") != EXPECTED["vpc"] or app_instance.get("SubnetId") not in EXPECTED["app_subnets"]:
        raise PreflightError("canary EC2 is outside the approved private application subnets")
    if app_instance.get("MetadataOptions", {}).get("HttpTokens") != "required":
        raise PreflightError("canary EC2 must require IMDSv2 before using its instance role from the host-network container")
    if profile_name(app_instance) != EXPECTED["instance_profile"]:
        raise PreflightError("canary EC2 is not using the expected ExampleAppRole instance profile")
    ping = read_json(directory, "canary-ssm.json").get("InstanceInformationList", [])
    if len(ping) != 1 or ping[0].get("PingStatus") != "Online":
        raise PreflightError("canary SSM agent is not Online; prepare/repair SSM before requesting deployment")
    app_sgs = {item["GroupId"] for item in app_instance.get("SecurityGroups", [])}

    if app_instance.get("PublicIpAddress") or app_instance.get("PublicDnsName"):
        raise PreflightError("application EC2 must not have a public address")
    if app_sgs != {CONFIG["APP_SECURITY_GROUP_ID"]}:
        raise PreflightError("application security group differs from the approved inventory")
    if app_instance.get("IamInstanceProfile", {}).get("Arn") != CONFIG["INSTANCE_PROFILE_ARN"]:
        raise PreflightError("application instance profile ARN differs from the approved inventory")
    for group in group_map.values():
        for permission in group.get("IpPermissions", []):
            if permission.get("IpProtocol") == "-1" or (
                permission.get("IpProtocol") in ("tcp", "6") and
                permission.get("FromPort", 65536) <= 22 <= permission.get("ToPort", -1)
            ):
                raise PreflightError("SSH ingress is forbidden; use SSM Session Manager")
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
    parser.add_argument("--mode", choices=("canary",), required=True)
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
