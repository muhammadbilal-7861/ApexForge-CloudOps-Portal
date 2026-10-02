#!/usr/bin/env python3
"""Publish or safely reuse this build's immutable commit-SHA ECR tag."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping
from typing import Any


class PublishError(RuntimeError):
    """Raised when the image cannot be published or verified safely."""


Runner = Callable[..., subprocess.CompletedProcess[str]]
DIGEST_PATTERN = re.compile(r"sha256:[0-9a-f]{64}\Z")
IMAGE_NOT_FOUND_PATTERN = re.compile(r"\(ImageNotFoundException\)")


def _checked(runner: Runner, command: list[str], *, env: Mapping[str, str] | None = None,
             input_text: str | None = None) -> str:
    result = runner(command, env=env, input=input_text, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise PublishError(f"Command failed ({' '.join(command)}): {detail}")
    return result.stdout.strip()


def _aws_cli(runner: Runner, config: Mapping[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    command = [
        "docker", "run", "--rm", "--network", "host", "--volumes-from", "jenkins",
        "--env", f"AWS_REGION={config['AWS_REGION']}",
        "--env", f"AWS_DEFAULT_REGION={config['AWS_REGION']}",
        config["AWS_CLI_IMAGE"], *args,
    ]
    return runner(command, capture_output=True, text=True, check=False)


def _aws_text(runner: Runner, config: Mapping[str, str], *args: str) -> str:
    result = _aws_cli(runner, config, *args)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise PublishError(f"AWS CLI {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def _describe_tag(runner: Runner, config: Mapping[str, str]) -> str | None:
    command = (
        "ecr", "describe-images", "--repository-name", config["ECR_REPOSITORY"],
        "--image-ids", f"imageTag={config['GIT_COMMIT_SHORT']}",
        "--query", "imageDetails[0].imageDigest", "--output", "text",
    )
    result = _aws_cli(runner, config, *command)
    if result.returncode:
        detail = f"{result.stdout}\n{result.stderr}"
        if IMAGE_NOT_FOUND_PATTERN.search(detail):
            return None
        message = result.stderr.strip() or result.stdout.strip() or f"exit status {result.returncode}"
        raise PublishError(f"AWS ECR DescribeImages failed: {message}")
    digest = result.stdout.strip()
    if not DIGEST_PATTERN.fullmatch(digest):
        raise PublishError(f"ECR returned an invalid digest for tag {config['GIT_COMMIT_SHORT']}.")
    return digest


def _image_metadata(runner: Runner, image: str, env: Mapping[str, str]) -> tuple[str, str]:
    metadata = _checked(
        runner,
        ["docker", "image", "inspect", "--format", "{{.Id}}|{{.Os}}/{{.Architecture}}", image],
        env=env,
    )
    image_id, separator, platform = metadata.partition("|")
    if not separator or not image_id.startswith("sha256:") or not platform or "/" not in platform:
        raise PublishError(f"Docker returned incomplete image metadata for {image}.")
    return image_id, platform


def _verify_digest_image(runner: Runner, config: Mapping[str, str], docker_env: Mapping[str, str],
                         image_id: str, platform: str, digest: str) -> None:
    digest_ref = f"{config['ECR_URI']}@{digest}"
    _checked(runner, ["docker", "pull", "--platform", platform, digest_ref], env=docker_env)
    pulled_id, pulled_platform = _image_metadata(runner, digest_ref, docker_env)
    if pulled_platform != platform:
        raise PublishError(
            f"ECR image platform mismatch: built {platform}, published {pulled_platform}."
        )
    if pulled_id != image_id:
        raise PublishError(
            "ECR commit tag resolves to an image that differs from the image built and scanned by this pipeline."
        )


def publish(config: Mapping[str, str], runner: Runner = subprocess.run) -> str:
    """Enforce main/role identity, then push or reuse the exact built image."""
    required = (
        "AWS_REGION", "AWS_ACCOUNT_ID", "AWS_EXPECTED_ROLE", "AWS_CLI_IMAGE", "ECR_REGISTRY",
        "ECR_REPOSITORY", "ECR_URI", "APP_IMAGE_REF", "GIT_COMMIT_SHORT",
    )
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise PublishError(f"Required publication configuration is missing: {', '.join(missing)}")

    caller_arn = _aws_text(runner, config, "sts", "get-caller-identity", "--query", "Arn", "--output", "text")
    caller_account = _aws_text(runner, config, "sts", "get-caller-identity", "--query", "Account", "--output", "text")
    expected_prefix = f"arn:aws:sts::{config['AWS_ACCOUNT_ID']}:assumed-role/{config['AWS_EXPECTED_ROLE']}/"
    if not caller_arn.startswith(expected_prefix):
        raise PublishError("Refusing ECR publication: EC2 instance role does not match the expected role.")
    if caller_account != config["AWS_ACCOUNT_ID"]:
        raise PublishError("Refusing ECR publication: AWS caller account does not match the configured account.")

    main_ref = "refs/remotes/origin/main^{commit}"
    checked_main = runner(["git", "rev-parse", "--verify", "--quiet", main_ref], capture_output=True,
                         text=True, check=False)
    if checked_main.returncode:
        raise PublishError("Refusing ECR publication: origin/main is not available in the checkout.")
    head_sha = _checked(runner, ["git", "rev-parse", "HEAD"])
    main_sha = _checked(runner, ["git", "rev-parse", main_ref])
    if head_sha != main_sha:
        raise PublishError("Refusing ECR publication: checked-out HEAD does not match origin/main.")

    mutability = _aws_text(
        runner, config, "ecr", "describe-repositories", "--repository-names", config["ECR_REPOSITORY"],
        "--query", "repositories[0].imageTagMutability", "--output", "text",
    )
    if mutability != "IMMUTABLE":
        raise PublishError(f"Refusing ECR publication: repository tag mutability is {mutability}, expected IMMUTABLE.")

    with tempfile.TemporaryDirectory(prefix="apexforge-docker-config-") as docker_config:
        docker_env = os.environ.copy()
        docker_env["DOCKER_CONFIG"] = docker_config
        token = _aws_text(runner, config, "ecr", "get-login-password", "--region", config["AWS_REGION"])
        _checked(
            runner,
            ["docker", "login", "--username", "AWS", "--password-stdin", config["ECR_REGISTRY"]],
            env=docker_env,
            input_text=token,
        )

        built_id, platform = _image_metadata(runner, config["APP_IMAGE_REF"], docker_env)
        digest = _describe_tag(runner, config)
        tag_ref = f"{config['ECR_URI']}:{config['GIT_COMMIT_SHORT']}"

        if digest is None:
            print(f"Commit tag {config['GIT_COMMIT_SHORT']} is absent; publishing the scanned image.")
            _checked(runner, ["docker", "tag", config["APP_IMAGE_REF"], tag_ref], env=docker_env)
            _checked(runner, ["docker", "push", tag_ref], env=docker_env)
            digest = _describe_tag(runner, config)
            if digest is None:
                raise PublishError("ECR push completed but DescribeImages could not find the published commit tag.")
            _verify_digest_image(runner, config, docker_env, built_id, platform, digest)
            print(f"Published and verified {tag_ref} at {digest}.")
        else:
            _verify_digest_image(runner, config, docker_env, built_id, platform, digest)
            print(f"Existing immutable tag {tag_ref} matches this build; reusing digest {digest} without pushing.")

    return digest


def main() -> int:
    config = {key: value for key, value in os.environ.items()}
    try:
        publish(config)
    except PublishError as exc:
        print(f"ECR publication failed safely: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
