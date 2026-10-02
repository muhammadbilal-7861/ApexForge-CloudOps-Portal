"""Regression checks for opt-in CloudOps deployment helpers."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_helper(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy" / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_deployment_is_opt_in_main_only_and_archives_reports() -> None:
    pipeline = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    assert "choices: ['none', 'canary', 'asg']" in pipeline
    assert "expression { params.DEPLOY_TARGET in ['canary', 'asg'] }" in pipeline
    assert "expression { params.DEPLOY_TARGET == 'canary' }" in pipeline
    assert "expression { params.DEPLOY_TARGET == 'asg' }" in pipeline
    assert "archiveArtifacts artifacts: 'reports/**'" in pipeline
    assert "TRIVY_SEVERITY" in pipeline and "TRIVY_EXIT_CODE" in pipeline
    assert "waitForQualityGate abortPipeline: true" in pipeline
    context = (ROOT / "deploy" / "assert-deploy-context.sh").read_text(encoding="utf-8")
    assert '[[ "$SCM_BRANCH" == main ]]' in context
    assert "origin/main^{commit}" in context


def test_launch_template_override_preserves_tags_and_omits_user_data() -> None:
    helper = load_helper("render-launch-template-data")
    source = {
        "VersionNumber": 5,
        "LaunchTemplateData": {
            "TagSpecifications": [
                {"ResourceType": "instance", "Tags": [{"Key": "Owner", "Value": "cloudops"}]},
                {"ResourceType": "volume", "Tags": [{"Key": "Data", "Value": "keep"}]},
            ]
        },
    }
    output = helper.render(source["LaunchTemplateData"], "dXNlci1kYXRh", "a" * 40)
    assert output["UserData"] == "dXNlci1kYXRh"
    assert "UserData" not in source["LaunchTemplateData"]
    tags = {entry["ResourceType"]: entry["Tags"] for entry in output["TagSpecifications"]}
    assert {tag["Key"]: tag["Value"] for tag in tags["instance"]}["Owner"] == "cloudops"
    assert {tag["Key"]: tag["Value"] for tag in tags["instance"]}["Version"] == "a" * 40
    assert tags["volume"] == [{"Key": "Data", "Value": "keep"}]
    assert output["MetadataOptions"]["HttpTokens"] == "required"


def test_ssm_renderer_quotes_command_arguments_and_checks_immutable_digest() -> None:
    helper = load_helper("render-ssm-command")
    digest = "sha256:" + "b" * 64
    args = argparse.Namespace(
        mode="deploy",
        image=f"489502663059.dkr.ecr.eu-north-1.amazonaws.com/apexforge-cloudops-portal@{digest}",
        version="c" * 12,
        commit="c" * 40,
        deploy_sha256="d" * 64,
        verify_sha256="e" * 64,
        instance_ids=["i-02777a62f2a65bc1e"],
        target_group_arn="arn:aws:elasticloadbalancing:eu-north-1:489502663059:targetgroup/test/abc'$(touch /tmp/nope)",
        require_target_healthy=False,
    )
    payload = helper.render(args)
    commands = payload["Parameters"]["commands"]
    assert any("cloudops-deploy.sh" in command for command in commands)
    assert commands[-1].startswith("/usr/local/sbin/cloudops-verify.sh ")
    assert "'\"'\"'" in commands[-1]
    import shlex

    assert shlex.split(commands[-1])[-1] == args.target_group_arn
    args.image = "489502663059.dkr.ecr.eu-north-1.amazonaws.com/apexforge-cloudops-portal:latest"
    try:
        helper.render(args)
    except ValueError:
        pass
    else:
        raise AssertionError("mutable image tags must be rejected")


def test_deploy_helper_never_deletes_volumes_or_prunes_images() -> None:
    script = (ROOT / "deploy" / "cloudops-deploy.sh").read_text(encoding="utf-8")
    assert 'docker rm "$old_id"' in script
    assert "docker rm -v" not in script
    assert "docker volume rm" not in script
    assert "docker system prune" not in script
    assert "OLD_CONTAINER_ALLOWLIST" in script
    assert "restore_previous" in script
    assert "health/version verification failed" in script


def test_secret_handling_is_non_echoing_and_runtime_file_is_private() -> None:
    bootstrap = (ROOT / "deploy" / "cloudops-user-data.sh.tmpl").read_text(encoding="utf-8")
    deploy = (ROOT / "deploy" / "cloudops-deploy.sh").read_text(encoding="utf-8")
    assert "set +x" in bootstrap and "set +x" in deploy
    assert "chmod 0600 \"$runtime_tmp\"" in bootstrap
    assert 'printf \'SECRET_KEY=%s\\n\' "$session_secret"' in bootstrap
    assert 'cat "$RUNTIME_ENV"' not in deploy
    assert "SECRET_KEY" not in " ".join(line for line in deploy.splitlines() if "printf" in line)


def test_aws_iam_policy_documents_are_valid_json_and_separate_roles() -> None:
    jenkins = json.loads((ROOT / "deploy/iam/jenkins-cloudops-deploy-policy.json").read_text(encoding="utf-8"))
    app = json.loads((ROOT / "deploy/iam/cloudops-ec2-instance-policy.json").read_text(encoding="utf-8"))
    assert any("ec2:CreateLaunchTemplateVersion" in statement.get("Action", []) for statement in jenkins["Statement"])
    assert any(statement.get("Action") == "ssm:GetCommandInvocation" and statement["Resource"] == "*" for statement in jenkins["Statement"])
    assert not any("ec2:CreateLaunchTemplateVersion" in statement.get("Action", []) for statement in app["Statement"])
    assert any(statement.get("Action") == "elasticloadbalancing:DescribeTargetHealth" for statement in app["Statement"])


def test_preflight_rejects_public_application_port_sources() -> None:
    helper = load_helper("cloudops-aws-preflight")
    secure_groups = [
        {
            "IpPermissions": [
                {
                    "IpProtocol": "tcp",
                    "FromPort": 5000,
                    "ToPort": 5000,
                    "UserIdGroupPairs": [{"GroupId": "sg-alb"}],
                }
            ]
        }
    ]
    assert helper.port_5000_sources(secure_groups) == {"sg-alb"}
    exposed_groups = [
        {
            "IpPermissions": [
                {
                    "IpProtocol": "tcp",
                    "FromPort": 5000,
                    "ToPort": 5000,
                    "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
                }
            ]
        }
    ]
    try:
        helper.port_5000_sources(exposed_groups)
    except helper.PreflightError:
        pass
    else:
        raise AssertionError("public or CIDR app ingress must be rejected")
