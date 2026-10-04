#!/usr/bin/env python3
"""Inventory v5, then render a complete launch allowlist without inheritance."""

from __future__ import annotations

import argparse
import json
import re
import base64
from pathlib import Path

from cloudops_ami import REVIEW, validate_source
from cloudops_launch import approved_settings, validate_settings


VERSION_RE = re.compile(r"^[0-9a-f]{12,40}$")


def render(source_data: dict, user_data: str, version: str) -> dict:
    validate_source(source_data)
    if not VERSION_RE.fullmatch(version):
        raise ValueError("version must be a Git commit SHA")
    if not user_data or any(char.isspace() for char in user_data):
        raise ValueError("user-data must be a non-empty base64 value")
    try:
        script = base64.b64decode(user_data, validate=True).decode("utf-8")
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid user-data encoding") from exc
    if not script.startswith("#!/usr/bin/env bash\n") or "@@" in script:
        raise ValueError("user-data must be a rendered bootstrap shell script")
    settings = approved_settings(user_data, version)
    validate_settings(settings)
    return settings


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-data", type=Path, required=True)
    parser.add_argument("--user-data", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        source_document = json.loads(args.source_data.read_text(encoding="utf-8"))
        # Preflight snapshots intentionally omit UserData; accept either that safe
        # wrapper or a direct LaunchTemplateData object from a trusted fixture.
        if source_document.get("VersionNumber") != REVIEW["source_template_version"]:
            raise ValueError("source snapshot must be immutable launch-template version 5")
        source = source_document["LaunchTemplateData"]
        if source.get("ImageId") != REVIEW["rejected_image_id"]:
            raise ValueError("source snapshot differs from the inventoried version 5 image")
        user_data = args.user_data.read_text(encoding="ascii").strip()
        output = render(source, user_data, args.version)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        parser.error(str(exc))
    args.output.write_text(json.dumps(output, separators=(",", ":")), encoding="utf-8")
    args.output.chmod(0o600)


if __name__ == "__main__":
    main()
