"""Pinned public-image provenance and launch configuration checks (no AWS writes)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

REVIEW = json.loads(Path(__file__).with_name("reviewed-al2023.json").read_text(encoding="utf-8"))


def validate_ami(payload: dict, parameter: dict | None = None) -> dict:
    images = payload.get("Images", [])
    if len(images) != 1:
        raise ValueError("expected exactly one reviewed Amazon Linux image")
    image = images[0]
    expected = {
        "ImageId": REVIEW["image_id"], "OwnerId": REVIEW["owner_id"],
        "ImageOwnerAlias": "amazon", "Name": REVIEW["name"],
        "ImageLocation": "amazon/" + REVIEW["name"], "Architecture": "x86_64",
        "State": "available", "ImageType": "machine", "RootDeviceType": "ebs",
        "VirtualizationType": "hvm", "Public": True, "ImdsSupport": "v2.0",
        "CreationDate": REVIEW["creation_date"], "SourceImageId": REVIEW["source_image_id"],
        "SourceImageRegion": REVIEW["source_image_region"],
        "PublicSsmParameterName": REVIEW["ssm_parameter"].lstrip("/"),
    }
    for key, value in expected.items():
        if image.get(key) != value:
            raise ValueError(f"reviewed AMI provenance mismatch: {key}")
    if image.get("DeprecationTime") and datetime.fromisoformat(image["DeprecationTime"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
        raise ValueError("reviewed AMI is deprecated; review a replacement before rollout")
    if parameter is not None:
        item = parameter.get("Parameter", {})
        if (item.get("Name") != REVIEW["ssm_parameter"] or item.get("Version") != REVIEW["ssm_parameter_version"]
                or item.get("Value") != REVIEW["image_id"]
                or item.get("ARN") != "arn:aws:ssm:eu-north-1::parameter" + REVIEW["ssm_parameter"]):
            raise ValueError("public SSM parameter version does not match reviewed image")
    return image


def validate_profile(profile: dict | None) -> dict:
    profile = profile or {}
    if not profile or (profile.get("Arn") and profile["Arn"] != REVIEW["instance_profile_arn"]):
        raise ValueError("launch template must use the approved CloudOpsEC2Role instance profile")
    if profile.get("Name") and profile["Name"] != "CloudOpsEC2Role":
        raise ValueError("launch template has an unexpected instance profile name")
    if not (profile.get("Arn") or profile.get("Name")):
        raise ValueError("instance profile has no name or ARN")
    return dict(profile)


def validate_source(data: dict) -> None:
    validate_profile(data.get("IamInstanceProfile"))
    if data.get("InstanceType") != "t3.micro":
        raise ValueError("unexpected application instance type")
    if data.get("BlockDeviceMappings"):
        raise ValueError("source contains explicit block devices; review snapshot provenance before copying")
    groups = set(data.get("SecurityGroupIds") or [])
    interfaces = data.get("NetworkInterfaces") or []
    if interfaces:
        if len(interfaces) != 1 or interfaces[0].get("DeviceIndex") != 0 or groups:
            raise ValueError("ambiguous source network interfaces/security groups")
        if set(interfaces[0]) - {"DeviceIndex", "Groups", "SubnetId", "AssociatePublicIpAddress", "DeleteOnTermination"}:
            raise ValueError("source network interface contains unreviewed address/interface settings")
        groups = set(interfaces[0].get("Groups") or [])
    if groups != {REVIEW["application_security_group"]}:
        raise ValueError("application security group differs from the reviewed group")
