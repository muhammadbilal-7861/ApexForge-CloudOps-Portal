#!/usr/bin/env python3
"""Render private RunInstances DryRun requests for both approved ASG subnets."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from cloudops_ami import REVIEW
from cloudops_launch import validate_settings


def render(overrides: dict, version: str, subnet: str, propagated_tags: dict | None = None) -> dict:
    if not version.isdigit() or int(version) <= 5 or subnet not in REVIEW["private_subnets"]:
        raise ValueError("permission check requires a numeric version and approved subnet")
    if overrides.get("ImageId") != REVIEW["image_id"]:
        raise ValueError("permission check requires the pinned reviewed Ubuntu image")
    validate_settings(overrides)
    interfaces = overrides["NetworkInterfaces"]
    # Probe the actual clean candidate. Do not override image/profile/metadata/
    # user data: such overrides could hide defects in the approved AWS version.
    request = {
        "DryRun": True, "MinCount": 1, "MaxCount": 1,
        "LaunchTemplate": {"LaunchTemplateId": "lt-028eb222c6fcfffc1", "Version": version},
        "NetworkInterfaces": [dict(interfaces[0], SubnetId=subnet)],
    }
    tags = REVIEW["asg_propagated_tags"] if propagated_tags is None else propagated_tags
    if tags != REVIEW["asg_propagated_tags"]:
        raise ValueError("ASG propagated tags differ from the reviewed configuration")
    # Auto Scaling propagates its tags, overriding matching instance tags from
    # the template. Model the real user-defined tags in the permission probe.
    request["TagSpecifications"] = [dict(item, Tags=list(item["Tags"])) for item in overrides["TagSpecifications"]]
    for item in request["TagSpecifications"]:
        if item["ResourceType"] == "instance":
            values = {tag["Key"]: tag["Value"] for tag in item["Tags"]}
            values.update(tags)
            item["Tags"] = [{"Key": key, "Value": value} for key, value in values.items()]
    return request


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--overrides", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--asg-data", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        overrides = json.loads(args.overrides.read_text())
        groups = json.loads(args.asg_data.read_text()).get("AutoScalingGroups", [])
        if len(groups) != 1 or groups[0].get("AutoScalingGroupName") != "asg-cloudops-app":
            raise ValueError("permission check requires the reviewed ASG snapshot")
        if set(groups[0].get("VPCZoneIdentifier", "").split(",")) != set(REVIEW["private_subnets"]):
            raise ValueError("ASG subnets differ from the reviewed private subnets")
        tags = {tag["Key"]: tag["Value"] for tag in groups[0].get("Tags", []) if tag.get("PropagateAtLaunch")}
        for subnet in REVIEW["private_subnets"]:
            path = args.output_dir / f"launch-permission-{subnet}.json"
            path.write_text(json.dumps(render(overrides, args.version, subnet, tags)))
            path.chmod(0o600)
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
