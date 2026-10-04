#!/usr/bin/env python3
"""Render clean AMI/bootstrap/network overrides for the immutable source LT v5."""

from __future__ import annotations

import argparse
import json
import re
import base64
from pathlib import Path

from cloudops_ami import REVIEW, validate_source


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
    tags_by_resource = {
        item.get("ResourceType"): list(item.get("Tags", []))
        for item in (source_data.get("TagSpecifications") or [])
    }
    instance_tags = {item.get("Key"): item for item in tags_by_resource.get("instance", [])}
    for key, value in (("Role", "app"), ("Monitoring", "enabled"), ("Version", version)):
        instance_tags[key] = {"Key": key, "Value": value}
    tags_by_resource["instance"] = list(instance_tags.values())
    tag_specs = [
        {"ResourceType": resource_type, "Tags": tags}
        for resource_type, tags in sorted(tags_by_resource.items())
        if resource_type and tags
    ]
    overrides = {"ImageId": REVIEW["image_id"], "UserData": user_data, "TagSpecifications": tag_specs,
                 "IamInstanceProfile": dict(source_data["IamInstanceProfile"])}
    if source_data.get("NetworkInterfaces"):
        overrides["NetworkInterfaces"] = [{"DeviceIndex": 0, "Groups": [REVIEW["application_security_group"]],
                                           "AssociatePublicIpAddress": False, "DeleteOnTermination": True}]
    else:
        overrides["SecurityGroupIds"] = [REVIEW["application_security_group"]]
    metadata = dict(source_data.get("MetadataOptions") or {})
    metadata.update({"HttpEndpoint": "enabled", "HttpTokens": "required", "HttpPutResponseHopLimit": 1})
    overrides["MetadataOptions"] = metadata
    return overrides


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
