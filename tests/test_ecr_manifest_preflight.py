"""Regression coverage for ECR manifest and image-index canary preflight."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PREFLIGHT = ROOT / "deploy" / "cloudops-canary-preflight.sh"
OCI_MANIFEST = "application/vnd.oci.image.manifest.v1+json"
OCI_INDEX = "application/vnd.oci.image.index.v1+json"
DOCKER_MANIFEST = "application/vnd.docker.distribution.manifest.v2+json"


def digest_for(raw: str) -> str:
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def image_manifest(layer_digest: str = "sha256:" + "1" * 64, media_type: str = OCI_MANIFEST) -> tuple[str, str]:
    document = {
        "schemaVersion": 2,
        "mediaType": media_type,
        "config": {"mediaType": "application/vnd.oci.image.config.v1+json", "digest": "sha256:" + "2" * 64, "size": 42},
        "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar+gzip", "digest": layer_digest, "size": 84}],
    }
    raw = json.dumps(document, separators=(",", ":"))
    return raw, digest_for(raw)


def api_image(digest: str, raw: str, media_type: str) -> dict[str, object]:
    return {
        "images": [{
            "imageId": {"imageDigest": digest},
            "imageManifest": raw,
            "imageManifestMediaType": media_type,
        }],
        "failures": [],
    }


@pytest.fixture
def preflight_harness(tmp_path: Path) -> dict[str, object]:
    if sys.platform != "linux" or getattr(os, "geteuid", lambda: 1)() != 0 or not shutil.which("bash"):
        pytest.skip("ECR preflight integration tests require a root Linux shell")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    runtime_env = tmp_path / "runtime.env"
    signing_secret = "test-stable-session-signing-key-0123456789abcdef"
    runtime_env.write_text(
        "FLASK_ENV=production\nSECRET_KEY=" + signing_secret + "\nSESSION_COOKIE_SECURE=false\n"
        "USE_AWS_SECRETS=true\nAWS_SECRET_NAME=cloudops/prod/mariadb\nAWS_REGION=eu-north-1\n"
        "ENABLE_LAB_FAILURE_ENDPOINTS=false\n",
        encoding="utf-8",
    )
    runtime_env.chmod(0o600)
    os.chown(runtime_env, 0, 0)

    aws_stub = r'''#!/usr/bin/env python3
import json, os, sys
a=sys.argv[1:]
with open(os.environ["AWS_CALL_LOG"], "a", encoding="utf-8") as log:
    log.write(json.dumps(a) + "\n")
if a[:2] == ["secretsmanager", "get-secret-value"]:
    secret_name=a[a.index("--secret-id")+1]
    if secret_name == "cloudops/prod/flask-session-key": print(os.environ["TEST_SIGNING_SECRET"])
    else: print(json.dumps({"host":"db.local","username":"app","password":"db-secret","dbname":"cloudops","port":3306}))
elif a[:2] == ["logs", "describe-log-groups"]:
    print("/cloudops/app")
elif a[:2] == ["ecr", "batch-get-image"]:
    if os.getenv("TEST_ECR_FAILURE") == "true":
        print("An error occurred (AccessDeniedException) while calling BatchGetImage", file=sys.stderr)
        sys.exit(254)
    requested=a[a.index("--image-ids")+1].split("=",1)[1]
    responses=json.load(open(os.environ["ECR_RESPONSES"], encoding="utf-8"))
    if requested not in responses:
        print(json.dumps({"images":[],"failures":[{"imageId":{"imageDigest":requested},"failureCode":"ImageNotFound"}]}))
        sys.exit(0)
    print(json.dumps(responses[requested]))
elif a[:2] == ["ecr", "batch-check-layer-availability"]:
    print("AVAILABLE")
elif a[:2] == ["ecr", "get-download-url-for-layer"]:
    print("https://example.invalid/layer")
else:
    sys.exit(2)
'''
    aws_path = bin_dir / "aws"
    aws_path.write_text(aws_stub, encoding="utf-8")
    aws_path.chmod(0o755)
    docker_path = bin_dir / "docker"
    docker_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    docker_path.chmod(0o755)
    return {
        "tmp": tmp_path,
        "runtime_env": runtime_env,
        "bin_dir": bin_dir,
        "secret": signing_secret,
        "aws_log": tmp_path / "aws-calls.jsonl",
    }


def run_preflight(
    harness: dict[str, object], responses: dict[str, object], image_digest: str, *, fail_ecr: bool = False
) -> subprocess.CompletedProcess[str]:
    tmp_path = harness["tmp"]
    assert isinstance(tmp_path, Path)
    response_path = tmp_path / "ecr-responses.json"
    response_path.write_text(json.dumps(responses), encoding="utf-8")
    env = os.environ.copy()
    env.update({
        "PATH": f"{harness['bin_dir']}:{env['PATH']}",
        "CLOUDOPS_RUNTIME_ENV": str(harness["runtime_env"]),
        "TEST_SIGNING_SECRET": str(harness["secret"]),
        "AWS_CALL_LOG": str(harness["aws_log"]),
        "ECR_RESPONSES": str(response_path),
        "TEST_ECR_FAILURE": "true" if fail_ecr else "false",
    })
    image = f"489502663059.dkr.ecr.eu-north-1.amazonaws.com/apexforge-cloudops-portal@{image_digest}"
    return subprocess.run(["bash", str(PREFLIGHT), image], env=env, capture_output=True, text=True, timeout=20)


def aws_calls(harness: dict[str, object]) -> list[list[str]]:
    log_path = harness["aws_log"]
    assert isinstance(log_path, Path)
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]


def test_canary_preflight_accepts_ordinary_oci_manifest_and_checks_its_layer(
    preflight_harness: dict[str, object],
) -> None:
    raw, digest = image_manifest()
    result = run_preflight(preflight_harness, {digest: api_image(digest, raw, OCI_MANIFEST)}, digest)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = aws_calls(preflight_harness)
    assert any(call[:2] == ["ecr", "batch-check-layer-availability"] and "sha256:" + "1" * 64 in call for call in calls)
    assert any(call[:2] == ["ecr", "get-download-url-for-layer"] for call in calls)


def test_canary_preflight_resolves_linux_amd64_child_and_ignores_attestations(
    preflight_harness: dict[str, object],
) -> None:
    layer_digest = "sha256:" + "3" * 64
    child_raw, child_digest = image_manifest(layer_digest)
    arm_raw, arm_digest = image_manifest("sha256:" + "4" * 64, DOCKER_MANIFEST)
    attestation_digest = "sha256:" + "5" * 64
    index = {
        "schemaVersion": 2,
        "mediaType": OCI_INDEX,
        "manifests": [
            {"mediaType": OCI_MANIFEST, "digest": attestation_digest, "size": 20,
             "platform": {"os": "unknown", "architecture": "unknown"}},
            {"mediaType": OCI_MANIFEST, "digest": arm_digest, "size": len(arm_raw),
             "platform": {"os": "linux", "architecture": "arm64"}},
            {"mediaType": OCI_MANIFEST, "digest": child_digest, "size": len(child_raw),
             "platform": {"os": "linux", "architecture": "amd64"}},
        ],
    }
    index_raw = json.dumps(index, separators=(",", ":"))
    index_digest = digest_for(index_raw)
    responses = {
        index_digest: api_image(index_digest, index_raw, OCI_INDEX),
        child_digest: api_image(child_digest, child_raw, OCI_MANIFEST),
    }
    result = run_preflight(preflight_harness, responses, index_digest)
    assert result.returncode == 0, result.stdout + result.stderr
    calls = aws_calls(preflight_harness)
    batch_get = [call for call in calls if call[:2] == ["ecr", "batch-get-image"]]
    assert len(batch_get) == 2
    assert f"imageDigest={child_digest}" in batch_get[1]
    assert any(call[:2] == ["ecr", "batch-check-layer-availability"] and layer_digest in call for call in calls)


def test_canary_preflight_rejects_index_without_linux_amd64_platform(
    preflight_harness: dict[str, object],
) -> None:
    _, arm_digest = image_manifest(media_type=DOCKER_MANIFEST)
    index = {
        "schemaVersion": 2,
        "mediaType": OCI_INDEX,
        "manifests": [{"mediaType": DOCKER_MANIFEST, "digest": arm_digest, "size": 80,
                       "platform": {"os": "linux", "architecture": "arm64"}}],
    }
    raw = json.dumps(index, separators=(",", ":"))
    digest = digest_for(raw)
    result = run_preflight(preflight_harness, {digest: api_image(digest, raw, OCI_INDEX)}, digest)
    assert result.returncode != 0
    assert "exactly one linux/amd64" in result.stderr
    assert not any(call[:2] == ["ecr", "batch-check-layer-availability"] for call in aws_calls(preflight_harness))


def test_canary_preflight_rejects_malformed_manifest_json(preflight_harness: dict[str, object]) -> None:
    malformed = "{"  # Hash matches the requested digest so the JSON parser is the failing check.
    digest = digest_for(malformed)
    result = run_preflight(preflight_harness, {digest: api_image(digest, malformed, OCI_MANIFEST)}, digest)
    assert result.returncode != 0
    assert "not valid JSON" in result.stderr


def test_canary_preflight_fails_closed_on_ecr_authorization_error(
    preflight_harness: dict[str, object],
) -> None:
    raw, digest = image_manifest()
    result = run_preflight(preflight_harness, {digest: api_image(digest, raw, OCI_MANIFEST)}, digest, fail_ecr=True)
    assert result.returncode != 0
    assert "AccessDeniedException" in result.stderr
    assert not any(call[:2] == ["ecr", "batch-check-layer-availability"] for call in aws_calls(preflight_harness))
