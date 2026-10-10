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
    "CANARY_INSTANCE_ID": r"i-[0-9a-f]{17}", "OBSERVABILITY_INSTANCE_ID": r"i-[0-9a-f]{17}",
    "SOURCE_REPOSITORY": r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
    "SESSION_COOKIE_SECURE": r"true|false",
    "ALLOW_INITIAL_INSTALL": r"true|false",
    "LEGACY_CONTAINER_NAME": r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}",
    "LEGACY_IMAGE": r"[A-Za-z0-9_./:-]+",
}
NAMES = ("TARGET_GROUP_NAME", "ALB_NAME", "RDS_INSTANCE_ID",
         "DB_SECRET_NAME", "SESSION_SECRET_NAME", "LOG_GROUP_NAME")


def validate(document: dict, *, allow_example: bool = False) -> dict:
    required = set(PATTERNS) | set(NAMES) | {
        "example_only", "INSTANCE_PROFILE_ARN", "TARGET_GROUP_ARN", "PRIVATE_SUBNET_IDS"}
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
    result["AWS_EXPECTED_ROLE"] = document["AWS_ROLE_NAME"]
    result["ECR_REGISTRY"] = f"{document['AWS_ACCOUNT_ID']}.dkr.ecr.{document['AWS_REGION']}.amazonaws.com"
    result["ECR_URI"] = result["ECR_REGISTRY"] + "/" + document["ECR_REPOSITORY"]
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
