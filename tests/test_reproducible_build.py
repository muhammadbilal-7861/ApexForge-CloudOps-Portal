"""Build input contract; real clean-build comparison is an explicit integration check."""
import json
from pathlib import Path
import re
import subprocess
import os

ROOT = Path(__file__).resolve().parents[1]


def test_python_wheel_lock_is_complete_and_hashed() -> None:
    rows = [line for line in (ROOT / "requirements.txt").read_text().splitlines() if line and not line.startswith("#")]
    assert len(rows) > 20
    for row in rows:
        assert re.fullmatch(r"[\w.-]+==[\w.+-]+ --hash=sha256:[0-9a-f]{64}", row), row


def test_image_inputs_are_pinned_and_no_package_upgrade_occurs() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert re.search(r"FROM python:3\.12-slim@sha256:[0-9a-f]{64}", dockerfile)
    assert "apt-get" not in dockerfile
    assert "sha256sum --check --status" in dockerfile
    assert "libpcre2-8-0_10.46-1~deb13u3_amd64.deb" in dockerfile
    assert "--require-hashes" in dockerfile
    assert "--no-compile" in dockerfile
    assert "USER app" in dockerfile
    assert "--uid 999" in dockerfile
    build = (ROOT / "deploy/build-image.sh").read_text()
    for required in ("git archive", "--format=%ct", "SOURCE_DATE_EPOCH=$epoch", "rewrite-timestamp=true",
                     "--platform linux/amd64", "--no-cache", "--provenance=false", "--sbom=false"):
        assert required in build
    assert len(re.findall(r"@sha256:[0-9a-f]{64}", build)) == 2


def test_app_role_target_health_permission_is_region_restricted() -> None:
    policy = json.loads((ROOT / "deploy/iam/cloudops-ec2-instance-policy.json").read_text())
    statement = next(item for item in policy["Statement"] if item["Action"] == "elasticloadbalancing:DescribeTargetHealth")
    assert statement["Resource"] == "*"
    assert statement["Condition"]["StringEquals"]["aws:RequestedRegion"] == "eu-north-1"


def test_build_command_uses_commit_time_and_archived_context(tmp_path) -> None:
    # Execute the wrapper with fake git/docker; prove no wall-clock/build-number args leak in.
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "calls"
    for name, text in {
        "git": '#!/bin/sh\nif [ "$1" = show ]; then echo 1234567890; else printf committed-context; fi\n',
        "docker": '#!/bin/sh\nprintf "%s\\n" "$*" >> "$CALL_LOG"\ncase "$*" in *"buildx build"*) cat > "$CONTEXT_LOG";; esac\n',
    }.items():
        path = bin_dir / name
        path.write_text(text)
        path.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", CALL_LOG=str(log), CONTEXT_LOG=str(tmp_path / "context"))
    subprocess.run(["bash", str(ROOT / "deploy/build-image.sh"), "test:commit"], env=env, check=True)
    command = log.read_text()
    assert 'SOURCE_DATE_EPOCH=$epoch' in command
    assert "1234567890 test:commit" in command
    assert (tmp_path / "context").read_text() == "committed-context"
