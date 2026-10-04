#!/usr/bin/env python3
"""Validate generated overrides and emit only safe launch-template preview fields."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
from pathlib import Path

from cloudops_ami import REVIEW, validate_ami, validate_source


def preview(source: dict, ami: dict, parameter: dict, overrides: dict) -> dict:
    image = validate_ami(ami, parameter)
    if source.get("VersionNumber") != 5:
        raise ValueError("launch template source must be version 5")
    validate_source(source["LaunchTemplateData"])
    if source["LaunchTemplateData"].get("ImageId") != REVIEW["rejected_image_id"]:
        raise ValueError("source image does not match inventoried version 5")
    if overrides.get("ImageId") != REVIEW["image_id"]:
        raise ValueError("overrides do not select the reviewed AMI")
    body = base64.b64decode(overrides["UserData"], validate=True)
    if not (0 < len(body) <= 16384):
        raise ValueError("EC2 user data is empty or exceeds 16 KiB")
    script = body.decode("utf-8")
    if not script.startswith("#!/usr/bin/env bash\n") or "@@" in script:
        raise ValueError("unrendered or invalid bootstrap")
    checked = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True, check=False)
    if checked.returncode:
        raise ValueError("bootstrap Bash syntax validation failed")
    for required in ("CLOUDOPS_FIRST_BOOT=true", "--require-container-health", "nft delete table inet cloudops_bootstrap",
                     "/var/lib/cloudops/bootstrap-complete.json", "sha256sum --check --status", "set +x"):
        if required not in script:
            raise ValueError("bootstrap safety control is missing")
    if not re.search(r'IMAGE_URI="489502663059\.dkr\.ecr\.eu-north-1\.amazonaws\.com/apexforge-cloudops-portal@sha256:[0-9a-f]{64}"', script):
        raise ValueError("bootstrap does not pin an approved ECR digest")
    effective = dict(source["LaunchTemplateData"], **overrides)
    validate_source(dict(effective, BlockDeviceMappings=None))
    metadata = overrides.get("MetadataOptions", {})
    if metadata.get("HttpTokens") != "required" or metadata.get("HttpPutResponseHopLimit") != 1 or metadata.get("HttpEndpoint") != "enabled":
        raise ValueError("bootstrap metadata options must enforce IMDSv2")
    interfaces = overrides.get("NetworkInterfaces") or []
    if any(item.get("SubnetId") or item.get("AssociatePublicIpAddress") is not False for item in interfaces):
        raise ValueError("rendered network interface must be private and must not pin a subnet")
    return {
        "launchTemplateId": "lt-028eb222c6fcfffc1", "sourceVersion": 5,
        "sourceImageRejected": REVIEW["rejected_image_id"], "newImageId": image["ImageId"],
        "imageOwnerId": image["OwnerId"], "imageName": image["Name"], "architecture": image["Architecture"],
        "ssmParameter": REVIEW["ssm_parameter"], "ssmParameterVersion": REVIEW["ssm_parameter_version"],
        "instanceProfile": overrides["IamInstanceProfile"], "networkInterfaces": interfaces,
        "metadataOptions": metadata, "capacityChanged": False,
        "previewOnly": True,
        "applicationImage": re.search(r'IMAGE_URI="([^"]+)"', script).group(1),
        "userDataValidation": {"bashSyntax": "passed", "sha256": hashlib.sha256(body).hexdigest(),
                               "bytes": len(body), "immutableImage": True, "firstBootGate": True},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-data", "ami", "parameter", "overrides", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    try:
        result = preview(*(json.loads(path.read_text()) for path in (args.source_data, args.ami, args.parameter, args.overrides)))
    except (ValueError, KeyError, OSError) as exc:
        parser.error(str(exc))
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, separators=(",", ":")))


if __name__ == "__main__":
    main()
