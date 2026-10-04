#!/usr/bin/env python3
"""Bind an AWS-readback clean candidate to the exact reviewed release; no secrets printed."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
from pathlib import Path

from cloudops_launch import LAUNCH_TEMPLATE_ID, fingerprint, normalized, validate_settings


def validate(candidate: dict, expected: dict, version: str, image: str, commit: str,
             approved: dict | None = None) -> dict:
    if not version.isdigit() or int(version) <= 5:
        raise ValueError("candidate must be a new explicit numeric version, never v5")
    if candidate.get("LaunchTemplateId") != LAUNCH_TEMPLATE_ID or candidate.get("VersionNumber") != int(version):
        raise ValueError("AWS candidate identity/version mismatch")
    actual = candidate.get("LaunchTemplateData", {})
    validate_settings(expected)
    validate_settings(actual)
    if normalized(actual) != normalized(expected):
        raise ValueError("AWS candidate differs from the reviewed launch settings")
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("invalid release commit")
    if not re.fullmatch(r"489502663059\.dkr\.ecr\.eu-north-1\.amazonaws\.com/apexforge-cloudops-portal@sha256:[0-9a-f]{64}", image):
        raise ValueError("invalid immutable release digest")
    script = base64.b64decode(actual["UserData"], validate=True).decode()
    if f'IMAGE_URI="{image}"' not in script or f'SOURCE_COMMIT="{commit}"' not in script:
        raise ValueError("candidate bootstrap does not match the approved release digest/commit")
    tags = actual["TagSpecifications"][0]["Tags"]
    if next(tag["Value"] for tag in tags if tag["Key"] == "Version") != commit[:12]:
        raise ValueError("candidate release tag differs from commit")
    record = {
        "launchTemplateId": LAUNCH_TEMPLATE_ID, "candidateVersion": version,
        "commit": commit, "applicationImage": image, "launchSettingsSha256": fingerprint(actual),
        "userDataSha256": hashlib.sha256(script.encode()).hexdigest(),
        "availabilityZoneIndependent": True, "capacityChanged": False,
    }
    if approved is not None and record != approved:
        raise ValueError("candidate version or release differs from the preapproval record")
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("candidate", "expected"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("version", "image", "commit"):
        parser.add_argument("--" + name, required=True)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--output", type=Path)
    action.add_argument("--approved", type=Path)
    parser.add_argument("--preview", type=Path)
    args = parser.parse_args()
    try:
        record = validate(json.loads(args.candidate.read_text()), json.loads(args.expected.read_text()),
                          args.version, args.image, args.commit,
                          json.loads(args.approved.read_text()) if args.approved else None)
        if args.output:
            args.output.write_text(json.dumps(record, indent=2) + "\n")
            if args.preview:
                preview = json.loads(args.preview.read_text())
                preview.update(candidateVersion=args.version,
                               launchSettingsSha256=record["launchSettingsSha256"], previewOnly=False,
                               candidateVersionCreated=True)
                args.preview.write_text(json.dumps(preview, indent=2) + "\n")
    except (OSError, ValueError, KeyError, StopIteration) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
