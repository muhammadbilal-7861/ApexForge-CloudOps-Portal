"""Explicit launch allowlist shared by rendering, preview and AWS readback."""
from __future__ import annotations

import copy
import json
import re

from cloudops_ami import REVIEW, CONFIG

LAUNCH_TEMPLATE_ID = CONFIG["LAUNCH_TEMPLATE_ID"]
FIELDS = {"ImageId", "InstanceType", "IamInstanceProfile", "NetworkInterfaces",
          "UserData", "TagSpecifications", "MetadataOptions"}


def approved_settings(user_data: str, version: str) -> dict:
    tags = dict(REVIEW["asg_propagated_tags"], Version=version)
    return {
        "ImageId": REVIEW["image_id"], "InstanceType": "t3.micro",
        "IamInstanceProfile": {"Arn": REVIEW["instance_profile_arn"]},
        "NetworkInterfaces": [{"DeviceIndex": 0, "Groups": [REVIEW["application_security_group"]],
                               "AssociatePublicIpAddress": False, "DeleteOnTermination": True}],
        "UserData": user_data,
        "TagSpecifications": [{"ResourceType": "instance", "Tags": [
            {"Key": key, "Value": value} for key, value in sorted(tags.items())]}],
        "MetadataOptions": {"HttpEndpoint": "enabled", "HttpTokens": "required", "HttpPutResponseHopLimit": 1},
    }


def normalized(data: dict) -> dict:
    result = copy.deepcopy(data)
    # EC2 can materialize these disabled defaults on readback. No enabled value
    # or other extra field is accepted; in particular Placement never qualifies.
    metadata = result.get("MetadataOptions", {})
    for key in ("HttpProtocolIpv6", "InstanceMetadataTags"):
        if metadata.get(key) == "disabled":
            metadata.pop(key)
    for item in result.get("TagSpecifications", []):
        item["Tags"] = sorted(item.get("Tags", []), key=lambda tag: (tag["Key"], tag["Value"]))
    return result


def validate_settings(data: dict) -> None:
    if set(data) != FIELDS:
        raise ValueError("launch settings differ from explicit allowlist; Placement/SubnetId are forbidden")
    specs = data.get("TagSpecifications", [])
    if len(specs) != 1 or specs[0].get("ResourceType") != "instance":
        raise ValueError("unapproved launch tags")
    tags = specs[0].get("Tags", [])
    versions = [tag.get("Value") for tag in tags if tag.get("Key") == "Version"]
    if len(versions) != 1:
        raise ValueError("launch settings require exactly one release version tag")
    if not isinstance(versions[0], str) or not re.fullmatch(r"[0-9a-f]{12,40}", versions[0]):
        raise ValueError("invalid release version tag")
    if normalized(data) != approved_settings(data.get("UserData"), versions[0]):
        raise ValueError("launch settings contain unapproved profile, network, metadata, tags or instance type")


def fingerprint(data: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(normalized(data), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
