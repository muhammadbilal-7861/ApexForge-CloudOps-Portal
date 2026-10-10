"""Single-instance quota policy and secret-safe bootstrap failure diagnostics."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.test_clean_asg import COMMIT, bootstrap_host, read_fixture
from tests.test_deployment_foundation import ROOT, fake_canary_host, load_helper
from tests.test_ubuntu_asg import reviewed_overrides


def run_bootstrap(host):
    return subprocess.run(["bash", str(host["bootstrap"])], env=host["env"],
                          capture_output=True, text=True, timeout=30)


def test_preview_identifies_one_initial_instance_and_retained_canary():
    preview = load_helper("preview-cloudops-asg")
    result = preview.preview(read_fixture("cloudops-launch-template-v5.json"),
                             read_fixture("aws-reviewed-ubuntu24.json"),
                             read_fixture("aws-reviewed-ubuntu24-parameter.json"), reviewed_overrides())
    assert result["initialCapacity"] == {"min": 1, "desired": 1, "max": 1}
    assert result["canaryRetired"] is False
    assert result["newImageId"] == "ami-00000000000000001"
    assert result["availabilityZoneIndependent"] is True
    assert result["userDataValidation"]["bytes"] <= 16384
    assert result["userDataValidation"]["immutableImage"] is True


def test_cloudwatch_is_started_before_ssm_and_runtime_secrets(bootstrap_host):
    host = bootstrap_host
    result = run_bootstrap(host)
    assert result.returncode == 0, (host["root"] / "var/log/cloudops-bootstrap.log").read_text()
    tools = [json.loads(line) for line in (host["root"] / "tools.jsonl").read_text().splitlines()]
    agent_index = next(index for index, call in enumerate(tools) if call[0] == "amazon-cloudwatch-agent-ctl")
    snap_index = next(index for index, call in enumerate(tools) if call[0] == "snap")
    assert agent_index < snap_index
    calls = [json.loads(line) for line in (host["root"] / "aws-calls.jsonl").read_text().splitlines()]
    log_index = next(index for index, call in enumerate(calls) if call[:2] == ["logs", "describe-log-groups"])
    secret_index = next(index for index, call in enumerate(calls) if call[:2] == ["secretsmanager", "get-secret-value"])
    assert log_index < secret_index
    config = json.loads((host["root"] / "etc/cloudwatch-agent-cloudops.json").read_text())
    assert config["logs"]["force_flush_interval"] == 1
    assert (host["root"] / "var/log/cloudops-bootstrap.log").stat().st_mode & 0o777 == 0o600
    marker = json.loads((host["root"] / "var/lib/cloudops/bootstrap-complete.json").read_text())
    assert marker["commit"] == COMMIT


@pytest.mark.parametrize("logger_state", ["available", "existing-stream", "denied", "imds-unavailable"])
def test_agent_failure_uses_safe_fallback_without_masking_failure(bootstrap_host, logger_state):
    host = bootstrap_host
    host["env"]["TEST_CW_AGENT_FAIL"] = "true"
    options = {"existing-stream": "TEST_EXISTING_LOG_STREAM", "denied": "TEST_LOG_UPLOAD_FAIL",
               "imds-unavailable": "TEST_DIAGNOSTICS_IMDS_FAIL"}
    if logger_state in options:
        host["env"][options[logger_state]] = "true"
    result = run_bootstrap(host)
    assert result.returncode == 19
    log = (host["root"] / "var/log/cloudops-bootstrap.log").read_text()
    console = (host["root"] / "dev/console").read_text()
    assert "status=failed stage=cloudwatch_agent exit=19" in console
    assert host["env"]["TEST_SECRET"] not in log
    assert "private-error" not in log
    assert "fixture-imds-token" not in log
    assert not (host["root"] / "var/lib/cloudops/bootstrap-complete.json").exists()
    state = json.loads(Path(host["state"]).read_text())
    assert state["containers"] == {}
    payload = host["root"] / "cloudwatch-status.jsonl"
    if logger_state in ("available", "existing-stream"):
        batches = [json.loads(line) for line in payload.read_text().splitlines()]
        assert len(batches) == 1
        assert len(batches[0]) == 1
        event = batches[0][0]
        assert event["timestamp"] > 0
        assert "status=failed stage=cloudwatch_agent exit=19" in event["message"]
        assert "line=0" not in event["message"]
        assert host["env"]["TEST_SECRET"] not in json.dumps(batches)
    else:
        assert not payload.exists()
        assert "Bootstrap status upload unavailable" in log


def test_explicit_secret_rejection_gets_exit_cleanup_and_cloudwatch_evidence(bootstrap_host):
    host = bootstrap_host
    host["env"]["TEST_SECRET"] = "short-rejected-value"
    result = run_bootstrap(host)
    assert result.returncode == 1
    event = json.loads((host["root"] / "cloudwatch-status.jsonl").read_text())[0]
    assert "status=failed stage=runtime_secrets exit=1" in event["message"]
    assert "line=0" not in event["message"]
    assert "short-rejected-value" not in event["message"]
    assert not (host["root"] / "var/lib/cloudops/bootstrap-complete.json").exists()
    assert not (host["root"] / "etc/cloudops/runtime.env").exists()
    assert (host["root"] / "gate.json").read_text() == "true"


def test_early_failure_without_cli_remains_visible_and_cannot_serve(bootstrap_host):
    host = bootstrap_host
    host["env"]["TEST_INSTALL_FAIL"] = "true"
    bin_dir = Path(host["env"]["PATH"].split(":")[0])
    (bin_dir / "aws").rename(bin_dir / "aws-not-installed")
    assert shutil.which("aws", path=host["env"]["PATH"]) is None
    result = run_bootstrap(host)
    assert result.returncode == 1
    console = (host["root"] / "dev/console").read_text()
    assert "status=failed stage=ubuntu_packages exit=1" in console
    assert not (host["root"] / "var/lib/cloudops/bootstrap-complete.json").exists()
    state = json.loads(Path(host["state"]).read_text())
    assert state["containers"] == {}


def test_bootstrap_diagnostics_need_no_expanded_iam_or_canary_retirement():
    policy = load_helper("render-iam-policy").render(json.loads((ROOT / "deploy/iam/cloudops-ec2-instance-policy.json").read_text()), json.loads(Path(os.environ["CLOUDOPS_CONFIG_FILE"]).read_text()))
    statement = next(item for item in policy["Statement"] if item["Sid"] == "WriteToPrecreatedCloudOpsLogGroup")
    assert set(statement["Action"]) == {"logs:CreateLogStream", "logs:PutLogEvents"}
    assert statement["Resource"].endswith("log-group:/cloudops/app:log-stream:*")
    rollout = (ROOT / "deploy/cloudops-asg-rollout.sh").read_text()
    assert "terminate-instances" not in rollout
    assert "stop-instances" not in rollout
    assert "deregister-targets" not in rollout
