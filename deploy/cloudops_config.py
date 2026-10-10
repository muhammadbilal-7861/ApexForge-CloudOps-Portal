"""Fail-closed, non-secret deployment inventory shared by scripts and renderers."""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
from pathlib import Path

PATTERNS = {
    "AWS_ACCOUNT_ID": r"[0-9]{12}", "AWS_REGION": r"[a-z]{2}-[a-z]+-[0-9]",
    "AWS_ROLE_NAME": r"[A-Za-z0-9+=,.@_-]{1,64}",
    "APP_ROLE_NAME": r"[A-Za-z0-9+=,.@_-]{1,64}",
    "INSTANCE_PROFILE_NAME": r"[A-Za-z0-9+=,.@_-]{1,64}",
    "ECR_REPOSITORY": r"[a-z0-9]+(?:[._/-][a-z0-9]+)*",
    "VPC_ID": r"vpc-[0-9a-f]{17}", "APP_SECURITY_GROUP_ID": r"sg-[0-9a-f]{17}",
    "LAUNCH_TEMPLATE_ID": r"lt-[0-9a-f]{17}",
    "CANARY_INSTANCE_ID": r"i-[0-9a-f]{17}", "OBSERVABILITY_INSTANCE_ID": r"i-[0-9a-f]{17}",
    "SOURCE_REPOSITORY": r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
    "SESSION_COOKIE_SECURE": r"true|false",
    "LEGACY_CONTAINER_NAME": r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}",
    "LEGACY_IMAGE": r"[A-Za-z0-9_./:-]+",
}
NAMES = ("ASG_NAME", "TARGET_GROUP_NAME", "ALB_NAME", "RDS_INSTANCE_ID",
         "DB_SECRET_NAME", "SESSION_SECRET_NAME", "LOG_GROUP_NAME")
REVIEW_KEYS = {
    "region", "image_id", "owner_id", "owner_alias", "name", "architecture", "creation_date",
    "ssm_parameter", "ssm_parameter_version", "review_date", "source_template_version",
    "rejected_image_id", "application_security_group", "instance_profile_arn", "image_location",
    "image_ssm_parameter", "root_snapshot_id", "private_subnets", "asg_propagated_tags",
}


def validate(document: dict, *, allow_example: bool = False) -> dict:
    required = set(PATTERNS) | set(NAMES) | {
        "example_only", "INSTANCE_PROFILE_ARN", "TARGET_GROUP_ARN", "PRIVATE_SUBNET_IDS", "reviewed_ami"}
    if not isinstance(document, dict) or set(document) != required:
        raise ValueError("deployment configuration has missing or unknown fields")
    if not isinstance(document["example_only"], bool):
        raise ValueError("example_only must be boolean")
    if document["example_only"] and not allow_example:
        raise ValueError("example inventory cannot authorize AWS operations; supply a reviewed private inventory")
    for key, pattern in PATTERNS.items():
        if not isinstance(document[key], str) or not re.fullmatch(pattern, document[key]):
            raise ValueError(f"invalid configuration field: {key}")
    for key in NAMES:
        if not isinstance(document[key], str) or not re.fullmatch(r"[A-Za-z0-9_./-]{1,128}", document[key]):
            raise ValueError(f"invalid configuration field: {key}")
    account, region = document["AWS_ACCOUNT_ID"], document["AWS_REGION"]
    profile = f"arn:aws:iam::{account}:instance-profile/{document['INSTANCE_PROFILE_NAME']}"
    if document["INSTANCE_PROFILE_ARN"] != profile:
        raise ValueError("instance profile account/name mismatch")
    target = f"arn:aws:elasticloadbalancing:{region}:{account}:targetgroup/{document['TARGET_GROUP_NAME']}/"
    if not re.fullmatch(re.escape(target) + r"[0-9a-f]{16}", document["TARGET_GROUP_ARN"]):
        raise ValueError("target group account/region/name mismatch")
    subnets = document["PRIVATE_SUBNET_IDS"]
    if not isinstance(subnets, list) or len(subnets) != 2:
        raise ValueError("exactly two distinct approved private subnets are required")
    if any(not isinstance(s, str) or not re.fullmatch(r"subnet-[0-9a-f]{17}", s) for s in subnets):
        raise ValueError("invalid private subnet ID")
    if len(set(subnets)) != 2:
        raise ValueError("private subnet IDs must be distinct")
    review = document["reviewed_ami"]
    if not isinstance(review, dict) or set(review) != REVIEW_KEYS:
        raise ValueError("reviewed AMI provenance is incomplete")
    for key, expected in {"region": region, "owner_id": "099720109477", "architecture": "x86_64",
                          "application_security_group": document["APP_SECURITY_GROUP_ID"],
                          "instance_profile_arn": profile, "private_subnets": subnets,
                          "source_template_version": 5}.items():
        if review[key] != expected:
            raise ValueError(f"AMI inventory mismatch: {key}")
    for key, prefix in (("image_id", "ami"), ("rejected_image_id", "ami"), ("root_snapshot_id", "snap")):
        if not isinstance(review[key], str) or not re.fullmatch(prefix + r"-[0-9a-f]{17}", review[key]):
            raise ValueError(f"invalid reviewed provenance: {key}")
    if review["image_id"] == review["rejected_image_id"]:
        raise ValueError("reviewed image must differ from the rejected source image")
    parameter = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
    if review["ssm_parameter"] != parameter or type(review["ssm_parameter_version"]) is not int or review["ssm_parameter_version"] < 1:
        raise ValueError("Canonical parameter provenance is invalid")
    if review["owner_alias"] != "amazon" or not isinstance(review["name"], str) or not re.fullmatch(
            r"ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24\.04-amd64-server-[0-9]{8}", review["name"]):
        raise ValueError("review must select official Ubuntu 24.04 AMD64 gp3 provenance")
    if review["image_location"] != "amazon/" + review["name"]:
        raise ValueError("image location differs from reviewed public provenance")
    if review["image_ssm_parameter"] != "aws/service/canonical/ubuntu/server/noble/stable/current/amd64/hvm/ebs-gp3/ami-id":
        raise ValueError("image public parameter provenance is invalid")
    from datetime import datetime
    for key in ("creation_date", "review_date"):
        if not isinstance(review[key], str):
            raise ValueError("invalid provenance date")
        try:
            datetime.fromisoformat(review[key].replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("invalid provenance date") from exc
    tags = review["asg_propagated_tags"]
    if not isinstance(tags, dict) or set(tags) != {"Environment", "Monitoring", "Name", "Project", "Role"}:
        raise ValueError("approved ASG tags are incomplete")
    if tags["Role"] != "app" or tags["Monitoring"] != "enabled":
        raise ValueError("ASG security/monitoring tags must be retained")
    if any(not isinstance(v, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", v) for v in tags.values()):
        raise ValueError("invalid ASG tag")
    return document


def load(path: str | Path | None = None) -> dict:
    source = Path(path or os.environ.get("CLOUDOPS_CONFIG_FILE", Path(__file__).with_name("environment.json")))
    try:
        document = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError("missing or invalid private CloudOps configuration (CLOUDOPS_CONFIG_FILE)") from exc
    return validate(document)


def environment(document: dict) -> dict[str, str]:
    result = {k: v for k, v in document.items() if k.isupper() and isinstance(v, str)}
    result["PRIVATE_SUBNET_IDS"] = " ".join(document["PRIVATE_SUBNET_IDS"])
    result["SUBNET_ONE"], result["SUBNET_TWO"] = document["PRIVATE_SUBNET_IDS"]
    result["AWS_EXPECTED_ROLE"] = document["AWS_ROLE_NAME"]
    result["ECR_REGISTRY"] = f"{document['AWS_ACCOUNT_ID']}.dkr.ecr.{document['AWS_REGION']}.amazonaws.com"
    result["ECR_URI"] = result["ECR_REGISTRY"] + "/" + document["ECR_REPOSITORY"]
    result["REVIEWED_AMI_ID"] = document["reviewed_ami"]["image_id"]
    result["REVIEWED_SSM_PARAMETER"] = document["reviewed_ami"]["ssm_parameter"]
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path)
    parser.add_argument("--format", choices=("shell", "json", "check"), default="check")
    args = parser.parse_args()
    try:
        document = load(args.file)
    except ValueError as exc:
        parser.error(str(exc))
    values = environment(document)
    if args.format == "shell":
        print("\n".join(f"export {k}={shlex.quote(v)}" for k, v in sorted(values.items())))
    elif args.format == "json":
        print(json.dumps(values))
    else:
        print("Private deployment inventory validated; no AWS API invoked.")


if __name__ == "__main__":
    main()
