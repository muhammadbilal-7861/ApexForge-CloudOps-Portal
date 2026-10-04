#!/usr/bin/env python3
"""Render the reviewed cloud-init shell template for one immutable application build."""

from __future__ import annotations

import argparse
import hashlib
import re
from pathlib import Path


IMAGE_RE = re.compile(
    r"^489502663059\.dkr\.ecr\.eu-north-1\.amazonaws\.com/"
    r"apexforge-cloudops-portal@sha256:[0-9a-f]{64}$"
)
SHA_RE = re.compile(r"^[0-9a-f]{40}$")
VERSION_RE = re.compile(r"^[0-9a-f]{12,40}$")


def render(template: str, *, image: str, version: str, commit: str, deploy_script: bytes, verify_script: bytes) -> str:
    if not IMAGE_RE.fullmatch(image):
        raise ValueError("image must be pinned to a digest in the configured ECR repository")
    if not VERSION_RE.fullmatch(version) or not SHA_RE.fullmatch(commit):
        raise ValueError("invalid version or full Git commit")
    if not commit.startswith(version):
        raise ValueError("version must identify the source commit")
    values = {
        "@@AWS_REGION@@": "eu-north-1",
        "@@IMAGE_URI@@": image,
        "@@APP_VERSION@@": version,
        "@@SOURCE_COMMIT@@": commit,
        "@@DEPLOY_SCRIPT_SHA256@@": hashlib.sha256(deploy_script).hexdigest(),
        "@@VERIFY_SCRIPT_SHA256@@": hashlib.sha256(verify_script).hexdigest(),
    }
    for token, value in values.items():
        template = template.replace(token, value)
    if "@@" in template:
        raise ValueError("unrendered template token remains")
    return template


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--deploy-script", type=Path, required=True)
    parser.add_argument("--verify-script", type=Path, required=True)
    parser.add_argument("--image", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        rendered = render(
            args.template.read_text(encoding="utf-8"),
            image=args.image,
            version=args.version,
            commit=args.commit,
            deploy_script=args.deploy_script.read_bytes(),
            verify_script=args.verify_script.read_bytes(),
        )
    except ValueError as exc:
        parser.error(str(exc))
    args.output.write_text(rendered, encoding="utf-8")
    args.output.chmod(0o600)


if __name__ == "__main__":
    main()
