#!/usr/bin/env python3
"""Render JSON for an SSM Run Command without shell/JSON quoting ambiguity."""

from __future__ import annotations

import argparse
import json
import re
import shlex


IMAGE_RE = re.compile(
    r"^489502663059\.dkr\.ecr\.eu-north-1\.amazonaws\.com/"
    r"apexforge-cloudops-portal@sha256:[0-9a-f]{64}$"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^[0-9a-f]{12,40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
RAW_ROOT = "https://raw.githubusercontent.com/muhammadbilal-7861/ApexForge-CloudOps-Portal"


def render(args: argparse.Namespace) -> dict:
    if not IMAGE_RE.fullmatch(args.image):
        raise ValueError("image must be the approved ECR repository pinned by digest")
    if not SHA_RE.fullmatch(args.commit) or not VERSION_RE.fullmatch(args.version):
        raise ValueError("invalid source commit or app version")
    if not all(
        SHA256_RE.fullmatch(checksum)
        for checksum in (args.deploy_sha256, args.verify_sha256, args.preflight_sha256)
    ):
        raise ValueError("invalid deployment script checksum")
    if not args.instance_ids or any(not re.fullmatch(r"i-[0-9a-f]{8,17}", item) for item in args.instance_ids):
        raise ValueError("invalid or missing SSM instance IDs")
    if args.mode == "deploy" and len(args.instance_ids) != 1:
        raise ValueError("canary deploy must target exactly one instance")
    if args.mode == "preflight" and len(args.instance_ids) != 1:
        raise ValueError("canary preflight must target exactly one instance")

    base = f"{RAW_ROOT}/{args.commit}/deploy"
    commands = ["set -Eeuo pipefail"]
    if args.mode == "preflight":
        scripts = (("cloudops-canary-preflight.sh", args.preflight_sha256),)
    else:
        scripts = (
            ("cloudops-deploy.sh", args.deploy_sha256),
            ("cloudops-verify.sh", args.verify_sha256),
        )
    for name, checksum in scripts:
        path = (
            f"/tmp/apexforge-{args.version}-{name}"
            if args.mode == "preflight"
            else f"/usr/local/sbin/{name}"
        )
        url = f"{base}/{name}"
        commands.extend(
            [
                f"curl --fail --silent --show-error --location {shlex.quote(url)} -o {shlex.quote(path)}",
                f"printf '%s  %s\\n' {shlex.quote(checksum)} {shlex.quote(path)} | sha256sum --check --status",
                f"chmod {'0700' if args.mode == 'preflight' else '0750'} {shlex.quote(path)}",
            ]
        )

    if args.mode == "preflight":
        path = f"/tmp/apexforge-{args.version}-cloudops-canary-preflight.sh"
        commands.insert(1, f"trap 'rm -f -- {shlex.quote(path)}' EXIT")
        commands.append(f"{shlex.quote(path)} {shlex.quote(args.image)} {shlex.quote(args.target_group_arn)}")
        return {
            "DocumentName": "AWS-RunShellScript",
            "InstanceIds": args.instance_ids,
            "Comment": f"ApexForge CloudOps canary preflight {args.version}",
            "Parameters": {"commands": commands},
        }

    commands.insert(1, "install -d -o root -g root -m 0750 /usr/local/sbin")
    metadata_token = "$(curl --fail --silent --show-error --max-time 2 -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token)"
    instance_id = "$(curl --fail --silent --show-error --max-time 2 -H \"X-aws-ec2-metadata-token: ${metadata_token}\" http://169.254.169.254/latest/meta-data/instance-id)"
    commands.extend([f"metadata_token={metadata_token}", f"instance_id={instance_id}"])

    if args.mode == "deploy":
        commands.append(
            f"/usr/local/sbin/cloudops-deploy.sh {shlex.quote(args.image)} {shlex.quote(args.version)} "
            f"--target-group-arn {shlex.quote(args.target_group_arn)} --instance-id \"${{instance_id}}\""
        )

    verify = (
        f"/usr/local/sbin/cloudops-verify.sh --expected-image {shlex.quote(args.image)} "
        "--require-container-health --wait-seconds 180 "
        f"--expected-version {shlex.quote(args.version)} --instance-id \"${{instance_id}}\" "
        f"--target-group-arn {shlex.quote(args.target_group_arn)}"
    )
    if args.require_target_healthy:
        verify += " --require-target-healthy"
    if getattr(args, "require_bootstrap_complete", False):
        verify += " --require-bootstrap-complete"
    commands.append(verify)

    return {
        "DocumentName": "AWS-RunShellScript",
        "InstanceIds": args.instance_ids,
        "Comment": f"ApexForge CloudOps {args.mode} verification {args.version}",
        "Parameters": {"commands": commands},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("preflight", "deploy", "verify"), required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--deploy-sha256", required=True)
    parser.add_argument("--verify-sha256", required=True)
    parser.add_argument("--preflight-sha256", required=True)
    parser.add_argument("--require-bootstrap-complete", action="store_true")
    parser.add_argument("--target-group-arn", required=True)
    parser.add_argument("--instance-ids", nargs="+", required=True)
    parser.add_argument("--require-target-healthy", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(render(args), separators=(",", ":")))
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
