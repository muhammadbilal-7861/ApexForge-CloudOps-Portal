"""Regression checks for opt-in CloudOps deployment helpers."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_helper(name: str):
    # Standalone deploy helpers import their reviewed sibling module.
    if str(ROOT / "deploy") not in sys.path:
        sys.path.insert(0, str(ROOT / "deploy"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "deploy" / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
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
    assert "TRIVY_SEVERITY" in pipeline
    assert "TRIVY_EXIT_CODE" in pipeline
    assert "waitForQualityGate abortPipeline: true" in pipeline
    assert "CLOUDOPS_DEPLOY_APPROVERS" in pipeline
    assert "submitter: env.CLOUDOPS_DEPLOY_APPROVERS" in pipeline
    context = (ROOT / "deploy" / "assert-deploy-context.sh").read_text(encoding="utf-8")
    assert '[[ "$SCM_BRANCH" == main ]]' in context
    assert "origin/main^{commit}" in context


def test_ecr_push_stage_uses_idempotent_immutable_publication_helper() -> None:
    pipeline = (ROOT / "Jenkinsfile").read_text(encoding="utf-8")
    assert "python3 deploy/publish-ecr-image.py" in pipeline
    helper = load_helper("publish-ecr-image")
    assert "ImageNotFoundException" in helper.IMAGE_NOT_FOUND_PATTERN.pattern
    assert "IMMUTABLE" in (ROOT / "deploy" / "publish-ecr-image.py").read_text(encoding="utf-8")


class FakeEcrPublisher:
    """Model only the Docker and AWS CLI calls used by the ECR publisher."""

    digest = "sha256:" + "a" * 64
    built_id = "sha256:" + "b" * 64

    def __init__(self, *, tag_exists: bool = False, pulled_id: str | None = None,
                 describe_error: str | None = None) -> None:
        self.tag_exists = tag_exists
        self.pulled_id = pulled_id or self.built_id
        self.describe_error = describe_error
        self.pushed = False
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        stdout = ""
        stderr = ""
        returncode = 0
        if command[0] == "git":
            stdout = "main-commit\n"
        elif command[:2] == ["docker", "run"]:
            aws_args = command[command.index("public.ecr.aws/aws-cli/aws-cli:2.37.5") + 1:]
            if aws_args[:2] == ["sts", "get-caller-identity"]:
                stdout = ("arn:aws:sts::489502663059:assumed-role/DevSecOpsToolsRole/jenkins\n"
                          if "Arn" in aws_args else "489502663059\n")
            elif aws_args[:2] == ["ecr", "describe-repositories"]:
                stdout = "IMMUTABLE\n"
            elif aws_args[:2] == ["ecr", "get-login-password"]:
                stdout = "test-ecr-password\n"
            elif aws_args[:2] == ["ecr", "describe-images"]:
                if self.describe_error:
                    returncode = 1
                    stderr = f"An error occurred ({self.describe_error}) when calling DescribeImages"
                elif self.tag_exists or self.pushed:
                    stdout = self.digest + "\n"
                else:
                    returncode = 1
                    stderr = "An error occurred (ImageNotFoundException) when calling DescribeImages"
            else:
                returncode, stderr = 2, "unexpected AWS CLI call"
        elif command[:3] == ["docker", "image", "inspect"]:
            image = command[-1]
            image_id = self.built_id if "@sha256:" not in image else self.pulled_id
            stdout = f"{image_id}|linux/amd64\n"
        elif command[:2] == ["docker", "push"]:
            self.pushed = True
        elif command[:2] not in (["docker", "login"], ["docker", "pull"]):
            if command[:2] != ["docker", "tag"]:
                returncode, stderr = 2, "unexpected Docker call"
        return subprocess.CompletedProcess(command, returncode, stdout, stderr)


@pytest.fixture
def ecr_publisher_config() -> dict[str, str]:
    return {
        "AWS_REGION": "eu-north-1",
        "AWS_ACCOUNT_ID": "489502663059",
        "AWS_EXPECTED_ROLE": "DevSecOpsToolsRole",
        "AWS_CLI_IMAGE": "public.ecr.aws/aws-cli/aws-cli:2.37.5",
        "ECR_REGISTRY": "489502663059.dkr.ecr.eu-north-1.amazonaws.com",
        "ECR_REPOSITORY": "apexforge-cloudops-portal",
        "ECR_URI": "489502663059.dkr.ecr.eu-north-1.amazonaws.com/apexforge-cloudops-portal",
        "APP_IMAGE_REF": "apexforge-cloudops:build-123",
        "GIT_COMMIT_SHORT": "0123456789ab",
    }


def test_ecr_publisher_pushes_new_commit_tag_and_verifies_published_digest(
    ecr_publisher_config: dict[str, str],
) -> None:
    helper = load_helper("publish-ecr-image")
    fake = FakeEcrPublisher()
    digest = helper.publish(ecr_publisher_config, fake)
    assert digest == fake.digest
    assert fake.pushed is True
    assert ["docker", "push", ecr_publisher_config["ECR_URI"] + ":0123456789ab"] in fake.commands
    assert ["docker", "pull", "--platform", "linux/amd64",
            ecr_publisher_config["ECR_URI"] + "@" + fake.digest] in fake.commands


def test_ecr_publisher_reuses_existing_matching_commit_tag_without_push(
    ecr_publisher_config: dict[str, str],
) -> None:
    helper = load_helper("publish-ecr-image")
    fake = FakeEcrPublisher(tag_exists=True)
    assert helper.publish(ecr_publisher_config, fake) == fake.digest
    assert fake.pushed is False
    assert not any(command[:2] == ["docker", "push"] for command in fake.commands)


@pytest.mark.parametrize("repeat_matches", [True, False])
def test_repeat_build_keeps_immutable_tag_and_compares_actual_image_id(ecr_publisher_config, repeat_matches) -> None:
    helper = load_helper("publish-ecr-image")
    fake = FakeEcrPublisher()
    published_digest = helper.publish(ecr_publisher_config, fake)
    fake.commands.clear()
    if repeat_matches:
        assert helper.publish(ecr_publisher_config, fake) == published_digest
    else:
        fake.built_id = "sha256:" + "d" * 64
        with pytest.raises(helper.PublishError, match="differs from the image built and scanned"):
            helper.publish(ecr_publisher_config, fake)
    assert not any(command[:2] == ["docker", "push"] for command in fake.commands)
    aws_operations = [command for command in fake.commands if "batch-delete-image" in command]
    assert aws_operations == []


def test_ecr_publisher_rejects_existing_commit_tag_with_different_image(
    ecr_publisher_config: dict[str, str],
) -> None:
    helper = load_helper("publish-ecr-image")
    fake = FakeEcrPublisher(tag_exists=True, pulled_id="sha256:" + "c" * 64)
    with pytest.raises(helper.PublishError, match="differs from the image built and scanned"):
        helper.publish(ecr_publisher_config, fake)
    assert fake.pushed is False


def test_ecr_publisher_fails_closed_on_describe_images_authorization_error(
    ecr_publisher_config: dict[str, str],
) -> None:
    helper = load_helper("publish-ecr-image")
    fake = FakeEcrPublisher(describe_error="AccessDeniedException")
    with pytest.raises(helper.PublishError, match="AccessDeniedException"):
        helper.publish(ecr_publisher_config, fake)
    assert fake.pushed is False


def test_launch_template_override_preserves_tags_and_omits_user_data() -> None:
    helper = load_helper("render-launch-template-data")
    source = {
        "VersionNumber": 5,
        "LaunchTemplateData": {
            "InstanceType": "t3.micro", "IamInstanceProfile": {"Arn": "arn:aws:iam::489502663059:instance-profile/CloudOpsEC2Role"},
            "NetworkInterfaces": [{"DeviceIndex": 0, "Groups": ["sg-0f9613afd389c288d"], "SubnetId": "subnet-08469c4e69b5c4d65"}],
            "TagSpecifications": [
                {"ResourceType": "instance", "Tags": [{"Key": "Owner", "Value": "cloudops"}]},
                {"ResourceType": "volume", "Tags": [{"Key": "Data", "Value": "keep"}]},
            ]
        },
    }
    import base64
    encoded = base64.b64encode(b"#!/usr/bin/env bash\necho safe\n").decode()
    output = helper.render(source["LaunchTemplateData"], encoded, "a" * 40)
    assert output["UserData"] == encoded
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
        preflight_sha256="f" * 64,
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


def test_ssm_canary_preflight_renderer_is_checksum_pinned_and_secret_safe() -> None:
    helper = load_helper("render-ssm-command")
    digest = "sha256:" + "b" * 64
    args = argparse.Namespace(
        mode="preflight",
        image=f"489502663059.dkr.ecr.eu-north-1.amazonaws.com/apexforge-cloudops-portal@{digest}",
        version="c" * 12,
        commit="c" * 40,
        deploy_sha256="d" * 64,
        verify_sha256="e" * 64,
        preflight_sha256="f" * 64,
        instance_ids=["i-02777a62f2a65bc1e"],
        target_group_arn="arn:aws:elasticloadbalancing:eu-north-1:489502663059:targetgroup/tg-cloudops-app/example",
        require_target_healthy=False,
    )
    commands = helper.render(args)["Parameters"]["commands"]
    assert any("cloudops-canary-preflight.sh" in command for command in commands)
    assert commands[-1].endswith(args.target_group_arn)
    assert any("sha256sum --check" in command for command in commands)
    assert any("cloudops-canary-preflight.sh" in command and "@sha256:" in command for command in commands)
    assert any(command.startswith("trap ") and "rm -f" in command for command in commands)
    assert not any("cloudops-deploy.sh" in command for command in commands)


def test_deploy_helper_never_deletes_volumes_or_prunes_images() -> None:
    script = (ROOT / "deploy" / "cloudops-deploy.sh").read_text(encoding="utf-8")
    assert "docker rm" not in script
    assert "docker volume rm" not in script
    assert "docker system prune" not in script
    assert "OLD_CONTAINER_ALLOWLIST" not in script
    assert "restore_previous" in script
    assert "localhost port 5001 is already in use" in script
    assert "--port 5001" in script
    assert "--target-group-arn" in script


def test_secret_handling_is_non_echoing_and_runtime_file_is_private() -> None:
    bootstrap = (ROOT / "deploy" / "cloudops-user-data.sh.tmpl").read_text(encoding="utf-8")
    deploy = (ROOT / "deploy" / "cloudops-deploy.sh").read_text(encoding="utf-8")
    assert "set +x" in bootstrap
    assert "set +x" in deploy
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


def write_asg_preflight_snapshots(tmp_path: Path):
    helper = load_helper("cloudops-aws-preflight")
    alb_arn = "arn:aws:elasticloadbalancing:eu-north-1:489502663059:loadbalancer/app/alb-load/abc"
    target_arn = "arn:aws:elasticloadbalancing:eu-north-1:489502663059:targetgroup/tg-cloudops-app/def"
    snapshots = {
        "alb.json": {"LoadBalancers": [{"LoadBalancerName": "alb-load", "VpcId": "vpc-080d46671dbdcacab", "LoadBalancerArn": alb_arn, "SecurityGroups": ["sg-alb"]}]},
        "target-group.json": {"TargetGroups": [{"TargetGroupName": "tg-cloudops-app", "TargetGroupArn": target_arn, "Port": 5000, "HealthCheckPath": "/ready", "VpcId": "vpc-080d46671dbdcacab", "LoadBalancerArns": [alb_arn]}]},
        "listeners.json": {"Listeners": [{"DefaultActions": [{"TargetGroupArn": target_arn}]}]},
        "observability-instance.json": {"Reservations": [{"Instances": [{"InstanceId": "i-0c550edbaa5ecbdff", "SecurityGroups": [{"GroupId": "sg-obs"}]}]}]},
        "rds.json": {"DBInstances": [{"DBInstanceStatus": "available"}]},
        "logs.json": {"logGroups": [{"logGroupName": "/cloudops/app"}]},
        "asg.json": {"AutoScalingGroups": [{
            "AutoScalingGroupName": "asg-cloudops-app",
            "VPCZoneIdentifier": "subnet-08469c4e69b5c4d65,subnet-05788ba98ca1096e6",
            "TargetGroupARNs": [target_arn],
            "LaunchTemplate": {"LaunchTemplateId": "lt-028eb222c6fcfffc1", "Version": "5"},
            "MinSize": 0, "DesiredCapacity": 0, "MaxSize": 0,
            "HealthCheckType": "ELB",
        }]},
        "launch-template.json": {"VersionNumber": 5, "LaunchTemplateData": {
            "ImageId": "ami-09b67ca726bea7328", "InstanceType": "t3.micro",
            "IamInstanceProfile": {"Arn": "arn:aws:iam::489502663059:instance-profile/CloudOpsEC2Role"}, "SecurityGroupIds": ["sg-0f9613afd389c288d"],
        }},
        "security-groups.json": {"SecurityGroups": [{"GroupId": "sg-0f9613afd389c288d", "IpPermissions": [{
            "IpProtocol": "tcp", "FromPort": 5000, "ToPort": 5000,
            "UserIdGroupPairs": [{"GroupId": "sg-alb"}, {"GroupId": "sg-obs"}],
        }]}]},
    }
    snapshots["launch-template-source.json"] = snapshots["launch-template.json"]
    snapshots["reviewed-ami.json"] = json.loads((ROOT / "tests/fixtures/aws-reviewed-al2023.json").read_text())
    snapshots["reviewed-ami-parameter.json"] = json.loads((ROOT / "tests/fixtures/aws-reviewed-al2023-parameter.json").read_text())
    for name, document in snapshots.items():
        (tmp_path / name).write_text(json.dumps(document), encoding="utf-8")

    return helper, target_arn


def test_asg_preflight_uses_elb_target_group_arn_key(tmp_path: Path) -> None:
    helper, target_arn = write_asg_preflight_snapshots(tmp_path)

    found_arn, security_groups = helper.validate_common(tmp_path, "asg")
    assert found_arn == target_arn
    assert security_groups == ["sg-0f9613afd389c288d"]


@pytest.fixture
def fake_canary_host(tmp_path: Path) -> dict[str, str]:
    if sys.platform != "linux" or os.geteuid() != 0 or not shutil.which("bash"):
        pytest.skip("canary cutover integration tests require a root Linux shell")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_path = tmp_path / "docker-state.json"
    secret = "test-stable-session-signing-key-0123456789abcdef"
    state_path.write_text(json.dumps({"containers": {
        "legacy-1": {"id": hashlib.sha256(b"legacy-1").hexdigest(), "name": "cloudops-flask", "image": "cloudops-flask:1.1", "running": True, "network": "host", "labels": {}, "health": "none", "mounts": []}
    }, "next": 0}), encoding="utf-8")

    docker_stub = r'''#!/usr/bin/env python3
import hashlib, json, os, sys
p=os.environ["DOCKER_STATE"]
state=json.load(open(p)); cs=state["containers"]; a=sys.argv[1:]
with open(os.environ["DOCKER_CALLS"],"a") as log: log.write(json.dumps(a)+"\n")
def save(): json.dump(state,open(p,"w"))
def get(x):
    named=next((c for c in cs.values() if c["name"]==x.lstrip("/")),None)
    if named: return named
    matches=[c for c in cs.values() if x and c["id"].startswith(x)]
    return matches[0] if len(matches)==1 else None
def fmt(t,c):
    if t=="{{.Id}}": return c["id"]
    if t=="{{.Config.Image}}": return c["image"]
    if t=="{{.Image}}": return c.get("runtime_image_id","sha256:"+hashlib.sha256(c["image"].encode()).hexdigest())
    if t=="{{.State.Running}}": return str(c["running"]).lower()
    if t=="{{.HostConfig.NetworkMode}}": return c["network"]
    if t=="{{json .HostConfig.PortBindings}}": return json.dumps(c.get("port_bindings"))
    if t=="{{json .NetworkSettings.Ports}}": return json.dumps(c.get("active_ports",c.get("port_bindings")))
    if t=="{{.Name}}": return "/"+c["name"]
    if t=="{{json .Mounts}}": return json.dumps(c["mounts"])
    if t=='{{index .Config.Labels "service"}}': return c["labels"].get("service","<no value>")
    if t=='{{index .Config.Labels "org.apexforge.version"}}': return c["labels"].get("org.apexforge.version","<no value>")
    if t=='{{index .Config.Labels "org.apexforge.deployment"}}': return c["labels"].get("org.apexforge.deployment","<no value>")
    if t=="{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}": return c["health"]
    return ""
if a[0]=="ps":
    selected=[c for c in cs.values() if c["running"] or "-a" in a or "--all" in a]
    for i,arg in enumerate(a[:-1]):
        if arg=="--filter":
            key,value=a[i+1].split("=",1)
            if key=="ancestor": selected=[c for c in selected if c["image"]==value]
            elif key=="status": selected=[c for c in selected if c["running"]==(value=="running")]
            else: sys.exit(2)
    if "--quiet" in a or "-q" in a:
        if os.getenv("TEST_PS_FAIL")=="true": sys.exit(1)
        print("\n".join(c["id"] if "--no-trunc" in a else c["id"][:12] for c in selected))
    else:
        for c in selected: print(f'container name={c["name"]} image={c["image"]}')
elif a[:2]==["image","inspect"]:
    print("sha256:"+hashlib.sha256(a[-1].encode()).hexdigest())
elif a[0]=="inspect":
    target=a[-1]; c=get(target)
    if c is None: sys.exit(1)
    if "--format" in a: print(fmt(a[a.index("--format")+1],c))
elif a[0]=="port":
    c=get(a[1]); bindings=c.get("active_ports",c.get("port_bindings")) or {}
    if "publications" in c: print(c["publications"])
    else:
        for port,entries in bindings.items():
            for entry in entries or []:
                host=entry.get("HostIp") or "0.0.0.0"
                if host=="::": host="[::]"
                print(f'{port} -> {host}:{entry["HostPort"]}')
elif a[0]=="pull": sys.exit(1) if os.getenv("TEST_ECR_PULL_FAIL")=="true" else None
elif a[0]=="login": sys.stdin.read()
elif a[0]=="run":
    name=a[a.index("--name")+1]; image=next(x for x in a if x.startswith("489502663059.dkr.ecr.") and "@sha256:" in x)
    if get(name): sys.exit(125)
    labels={}
    for i,x in enumerate(a[:-1]):
        if x=="--label":
            k,v=a[i+1].split("=",1); labels[k]=v
    state["next"]+=1; ident=hashlib.sha256(f'new-{state["next"]}'.encode()).hexdigest()
    candidate="candidate" in name
    c={"id":ident,"name":name,"image":image,"running":True,"network":a[a.index("--network")+1],"port_bindings":None,"labels":labels,"health":os.getenv("TEST_CANDIDATE_DOCKER_HEALTH" if candidate else "TEST_PRODUCTION_DOCKER_HEALTH","healthy"),"mounts":[]}
    if not candidate and os.getenv("TEST_PRODUCTION_NAME_RACE")=="true":
        c["labels"].pop("org.apexforge.deployment",None)
        cs[ident]=c; save(); sys.exit(125)
    if not candidate and os.getenv("TEST_PRODUCTION_START_FAIL")=="true":
        c["running"]=False
        cs[ident]=c; save(); sys.exit(125)
    cs[ident]=c; save(); print(ident)
elif a[0]=="stop":
    c=get(a[-1])
    if c is None: sys.exit(1)
    c["running"]=False
    if c["name"].startswith("cloudops-candidate-") and os.getenv("TEST_PRIOR_CHANGE"):
        cs["legacy-1"].update(json.loads(os.environ["TEST_PRIOR_CHANGE"]))
    save(); print(a[-1])
elif a[0]=="start":
    c=get(a[-1])
    if c is None: sys.exit(1)
    c["running"]=True
    c["restarted"]=True
    c["warmup_remaining"]=int(os.getenv("TEST_RESTORE_WARMUP_POLLS","0"))
    save(); print(a[-1])
elif a[0]=="rename":
    c=get(a[-2])
    if c is None or get(a[-1]): sys.exit(1)
    c["name"]=a[-1]; save()
elif a[0]=="update":
    c=get(a[-1])
    if c is None: sys.exit(1)
    c["restart"]="no"; save()
else: sys.exit(2)
'''
    aws_stub = r'''#!/usr/bin/env python3
import json, os, sys
a=sys.argv[1:]
if a[:2]==["ecr","get-login-password"]: print("test-password")
elif a[:2]==["logs","describe-log-groups"]: print("None" if os.getenv("TEST_LOG_GROUP_MISSING")=="true" else "/cloudops/app")
elif a[:2]==["secretsmanager","get-secret-value"]:
    if "flask-session-key" in " ".join(a): print(os.environ["TEST_SECRET"])
    else: print(json.dumps({"host":"db.local","username":"app","password":"private","dbname":"cloudops","port":3306}))
elif a[:2]==["elbv2","describe-target-health"]:
    if os.getenv("TEST_ALB_DENIED")=="true":
        print("AccessDenied: DescribeTargetHealth", file=sys.stderr)
        sys.exit(254)
    s=json.load(open(os.environ["DOCKER_STATE"])); prod=next((c for c in s["containers"].values() if c["name"]=="cloudops-app" and c["running"]),None)
    old=any(c["running"] and c["image"]=="cloudops-flask:1.1" for c in s["containers"].values())
    print("healthy" if (old or (prod and os.getenv("TEST_ALB_HEALTHY","true")=="true")) else "initial")
else: sys.exit(2)
'''
    curl_stub = r'''#!/usr/bin/env python3
import json, os, sys, urllib.parse
a=sys.argv[1:]; url=next(x for x in a if x.startswith("http://")); u=urllib.parse.urlparse(url); s=json.load(open(os.environ["DOCKER_STATE"])); cs=list(s["containers"].values())
candidate=next((c for c in cs if c["running"] and "candidate" in c["name"]),None)
prod=next((c for c in cs if c["running"] and c["name"]=="cloudops-app"),None)
legacy=any(c["running"] and c["image"]=="cloudops-flask:1.1" for c in cs)
if u.port==5001:
    key="TEST_CANDIDATE_"+("READY" if u.path=="/ready" else "HTTP")
    status=os.getenv(key,"200") if candidate else "000"
else:
    key="TEST_PRODUCTION_"+("READY" if u.path=="/ready" else "HTTP")
    status=os.getenv(key,"200") if prod else ("200" if legacy else "000")
    old=next((c for c in cs if c["running"] and c["image"]=="cloudops-flask:1.1"),None)
    if old and old.get("restarted"):
        status=os.getenv("TEST_RESTORE_HTTP","200")
        if old.get("warmup_remaining",0)>0:
            status="000"; old["warmup_remaining"]-=1
            json.dump(s,open(os.environ["DOCKER_STATE"],"w"))
sys.stdout.write(status)
'''
    for name, content in (("docker", docker_stub), ("aws", aws_stub), ("curl", curl_stub)):
        path = bin_dir / name
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        path.chmod(0o755)

    runtime_env = tmp_path / "runtime.env"
    runtime_env.write_text(
        "FLASK_ENV=production\nSECRET_KEY=" + secret + "\nSESSION_COOKIE_SECURE=false\n"
        "USE_AWS_SECRETS=true\nAWS_SECRET_NAME=cloudops/prod/mariadb\nAWS_REGION=eu-north-1\n"
        "ENABLE_LAB_FAILURE_ENDPOINTS=false\n",
        encoding="utf-8",
    )
    runtime_env.chmod(0o600)
    verify = tmp_path / "cloudops-verify.sh"
    shutil.copyfile(ROOT / "deploy/cloudops-verify.sh", verify)
    verify.chmod(0o755)

    image = "489502663059.dkr.ecr.eu-north-1.amazonaws.com/apexforge-cloudops-portal@sha256:" + "a" * 64
    env = os.environ.copy()
    env.update({
        "PATH": f"{bin_dir}:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "DOCKER_STATE": str(state_path),
        "DOCKER_CALLS": str(tmp_path / "docker-calls.jsonl"),
        "TEST_SECRET": secret,
        "CLOUDOPS_RUNTIME_ENV": str(runtime_env),
        "CLOUDOPS_DEPLOY_LOG": str(tmp_path / "deploy.log"),
        "CLOUDOPS_VERIFY_SCRIPT": str(verify),
        "CLOUDOPS_CANDIDATE_WAIT_SECONDS": "0",
        "CLOUDOPS_PRODUCTION_WAIT_SECONDS": "0",
        "CLOUDOPS_ALB_WAIT_SECONDS": "0",
        "CLOUDOPS_RESTORE_WAIT_SECONDS": "0",
    })
    return {"env": env, "state": str(state_path), "image": image, "version": "a" * 40, "tmp": str(tmp_path)}


def run_fake_deploy(host: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "deploy/cloudops-deploy.sh"), host["image"], host["version"],
         "--target-group-arn", "arn:aws:elasticloadbalancing:eu-north-1:489502663059:targetgroup/tg-cloudops-app/abc",
         "--instance-id", "i-02777a62f2a65bc1e"],
        env=host["env"], capture_output=True, text=True, timeout=30,
    )


def test_alb_authorization_failure_prevents_candidate_and_cutover(fake_canary_host) -> None:
    before = Path(fake_canary_host["state"]).read_bytes()
    fake_canary_host["env"]["TEST_ALB_DENIED"] = "true"
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "ALB permission check failed before candidate creation" in result.stdout + result.stderr
    assert Path(fake_canary_host["state"]).read_bytes() == before
    assert not Path(fake_canary_host["env"]["DOCKER_CALLS"]).exists()


def configure_legacy_bridge(host: dict[str, str], port_bindings: dict[str, list[dict[str, str]]] | None = None, *, dual_stack: bool = False) -> None:
    state_path = Path(host["state"])
    state = json.loads(state_path.read_text())
    state["containers"]["legacy-1"]["network"] = "bridge"
    state["containers"]["legacy-1"]["port_bindings"] = port_bindings or {
        "5000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5000"}],
    }
    if dual_stack:
        state["containers"]["legacy-1"]["active_ports"] = {
            "5000/tcp": [{"HostIp": address, "HostPort": "5000"} for address in ("0.0.0.0", "::")],
        }
    state_path.write_text(json.dumps(state), encoding="utf-8")


def test_docker_id_contract_and_observed_dualstack_cutover_failure(fake_canary_host) -> None:
    configure_legacy_bridge(fake_canary_host, dual_stack=True)
    env = fake_canary_host["env"]
    def docker(*args):
        return subprocess.check_output(["docker", *args], env=env, text=True).strip()
    short_id = docker("ps", "-q")
    full_id = docker("ps", "--quiet", "--no-trunc")
    assert len(short_id) == 12
    assert len(full_id) == 64
    assert short_id == full_id[:12]
    assert short_id != docker("inspect", "--format", "{{.Id}}", "cloudops-flask")
    assert docker("inspect", "--format", "{{.Id}}", short_id) == full_id
    # Real Docker accepts an unambiguous truncated ID for operations, too.
    docker("stop", short_id)
    docker("start", short_id)
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "port=5001 Docker-health=healthy /health=200 /ready=200" in result.stdout
    assert "Canary cutover completed" in result.stdout
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    previous = state["containers"]["legacy-1"]
    assert previous["id"] == full_id
    assert previous["network"] == "bridge"
    assert previous["running"] is False
    calls = [json.loads(line) for line in Path(env["DOCKER_CALLS"]).read_text().splitlines()]
    cutover_stop = next(call for call in calls if call[:3] == ["stop", "--time", "20"])
    assert cutover_stop[-1] == full_id


def test_fake_docker_rejects_ambiguous_short_ids(fake_canary_host) -> None:
    state_path = Path(fake_canary_host["state"])
    state = json.loads(state_path.read_text())
    original = state["containers"]["legacy-1"]
    full_id = original["id"]
    state["containers"]["collision"] = dict(original, id=full_id[:12] + "f" * 52, name="different-container")
    state_path.write_text(json.dumps(state), encoding="utf-8")
    ambiguous = subprocess.run(["docker", "inspect", full_id[:12]], env=fake_canary_host["env"], capture_output=True)
    assert ambiguous.returncode != 0
    exact = subprocess.check_output(["docker", "inspect", "--format", "{{.Id}}", full_id],
                                    env=fake_canary_host["env"], text=True).strip()
    assert exact == full_id


@pytest.mark.parametrize("change", [
    {"id": "f" * 64}, {"name": "unexpected-renamed-container"},
    {"image": "unexpected:2"}, {"network": "unexpected-network"},
    {"port_bindings": {"5000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "6000"}]}},
])
def test_inventory_change_after_candidate_verification_refuses_cutover(fake_canary_host, change) -> None:
    configure_legacy_bridge(fake_canary_host, dual_stack=True)
    fake_canary_host["env"]["TEST_PRIOR_CHANGE"] = json.dumps(change)
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "prior container identity or configuration changed" in result.stdout
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    assert state["containers"]["legacy-1"]["running"] is True
    assert not any(c["name"] == "cloudops-app" for c in state["containers"].values())


def test_docker_discovery_failure_does_not_become_empty_inventory(fake_canary_host) -> None:
    fake_canary_host["env"]["TEST_PS_FAIL"] = "true"
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "legacy container discovery failed" in result.stdout
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    assert len(state["containers"]) == 1
    assert state["containers"]["legacy-1"]["running"] is True


def test_created_but_not_started_production_container_can_be_rolled_back(fake_canary_host) -> None:
    configure_legacy_bridge(fake_canary_host, dual_stack=True)
    fake_canary_host["env"]["TEST_PRODUCTION_START_FAIL"] = "true"
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "Previous container restored" in result.stdout
    old = json.loads(Path(fake_canary_host["state"]).read_text())["containers"]["legacy-1"]
    assert old["id"] == hashlib.sha256(b"legacy-1").hexdigest()
    assert old["running"] is True
    assert old["network"] == "bridge"


@pytest.mark.parametrize("recovers", [True, False])
def test_rollback_waits_for_original_application_startup_with_bounded_failure(fake_canary_host, recovers) -> None:
    configure_legacy_bridge(fake_canary_host, dual_stack=True)
    env = fake_canary_host["env"]
    env["TEST_PRODUCTION_READY"] = "503"
    env["TEST_RESTORE_WARMUP_POLLS"] = "2"
    env["CLOUDOPS_RESTORE_WAIT_SECONDS"] = "8" if recovers else "0"
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    if recovers:
        assert "Previous container restored" in result.stdout
    else:
        assert "CRITICAL: previous container restarted" in result.stdout
        assert "Previous container restored" not in result.stdout
    previous = json.loads(Path(fake_canary_host["state"]).read_text())["containers"]["legacy-1"]
    assert previous["id"] == hashlib.sha256(b"legacy-1").hexdigest()
    assert previous["running"] is True
    assert previous["name"] == "cloudops-flask"


def test_production_name_race_does_not_adopt_unknown_container(fake_canary_host) -> None:
    configure_legacy_bridge(fake_canary_host, dual_stack=True)
    fake_canary_host["env"]["TEST_PRODUCTION_NAME_RACE"] = "true"
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "Refusing rollback mutation" in result.stdout
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    unknown = next(c for c in state["containers"].values() if c["name"] == "cloudops-app")
    assert unknown["running"] is True
    assert "org.apexforge.deployment" not in unknown["labels"]
    assert state["containers"]["legacy-1"]["name"].startswith("cloudops-rollback-")


def test_canary_port_conflict_refuses_candidate_before_mutating_legacy(fake_canary_host: dict[str, str]) -> None:
    listener = socket.socket()
    listener.bind(("0.0.0.0", 5001))
    listener.listen()
    try:
        result = run_fake_deploy(fake_canary_host)
    finally:
        listener.close()
    assert result.returncode != 0
    assert "port 5001 is already in use" in result.stdout
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    assert state["containers"]["legacy-1"]["running"] is True
    assert not any("candidate" in item["name"] for item in state["containers"].values())


def test_candidate_port_probe_allows_time_wait_from_prior_stopped_candidate(fake_canary_host) -> None:
    # A real listening socket is covered above. Here only a closed TCP connection remains.
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("0.0.0.0", 5001))
        listener.listen()
        with socket.create_connection(("127.0.0.1", 5001)) as client:
            server, _ = listener.accept()
            server.shutdown(socket.SHUT_WR)
            server.close()
            assert client.recv(1) == b""
    with socket.socket() as old_probe:
        with pytest.raises(OSError):
            old_probe.bind(("0.0.0.0", 5001))
    configure_legacy_bridge(fake_canary_host, dual_stack=True)
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode == 0, result.stdout + result.stderr


def test_successful_canary_candidate_cutover_retains_previous_container(fake_canary_host: dict[str, str]) -> None:
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    production = next(item for item in state["containers"].values() if item["name"] == "cloudops-app")
    previous = state["containers"]["legacy-1"]
    candidate = next(item for item in state["containers"].values() if item["name"].startswith("cloudops-verified-"))
    assert production["running"] is True
    assert production["image"] == fake_canary_host["image"]
    assert previous["running"] is False
    assert previous["name"].startswith("cloudops-rollback-")
    assert previous["image"] == "cloudops-flask:1.1"
    assert candidate["running"] is False


@pytest.mark.parametrize("dual_stack", [False, True])
def test_successful_bridge_legacy_canary_cutover_preserves_original_container_configuration(
    fake_canary_host: dict[str, str], dual_stack: bool,
) -> None:
    configure_legacy_bridge(fake_canary_host, dual_stack=dual_stack)
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    previous = state["containers"]["legacy-1"]
    production = next(item for item in state["containers"].values() if item["name"] == "cloudops-app")
    candidate = next(item for item in state["containers"].values() if item["name"].startswith("cloudops-verified-"))
    assert previous["running"] is False
    assert previous["network"] == "bridge"
    assert previous["port_bindings"] == {"5000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5000"}]}
    assert previous["name"].startswith("cloudops-rollback-")
    assert production["running"] is True
    assert production["network"] == "host"
    assert candidate["running"] is False


@pytest.mark.parametrize("dual_stack", [False, True])
def test_bridge_legacy_candidate_failure_leaves_original_serving_port_5000(
    fake_canary_host: dict[str, str], dual_stack: bool,
) -> None:
    configure_legacy_bridge(fake_canary_host, dual_stack=dual_stack)
    fake_canary_host["env"]["TEST_CANDIDATE_DOCKER_HEALTH"] = "unhealthy"
    fake_canary_host["env"]["TEST_CANDIDATE_HTTP"] = "503"
    fake_canary_host["env"]["TEST_CANDIDATE_READY"] = "503"
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    previous = state["containers"]["legacy-1"]
    assert previous["running"] is True
    assert previous["name"] == "cloudops-flask"
    assert previous["network"] == "bridge"
    assert previous["port_bindings"] == {"5000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5000"}]}
    assert not any(item["name"] == "cloudops-app" for item in state["containers"].values())


@pytest.mark.parametrize("dual_stack", [False, True])
def test_bridge_legacy_production_failure_restarts_original_publication(
    fake_canary_host: dict[str, str], dual_stack: bool,
) -> None:
    configure_legacy_bridge(fake_canary_host, dual_stack=dual_stack)
    fake_canary_host["env"]["TEST_PRODUCTION_HTTP"] = "503"
    fake_canary_host["env"]["TEST_PRODUCTION_READY"] = "503"
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "Previous container restored" in result.stdout
    previous = json.loads(Path(fake_canary_host["state"]).read_text())["containers"]["legacy-1"]
    assert previous["running"] is True
    assert previous["name"] == "cloudops-flask"
    assert previous["network"] == "bridge"
    assert previous["port_bindings"] == {"5000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5000"}]}


@pytest.mark.parametrize("dual_stack", [False, True])
def test_bridge_legacy_alb_failure_restores_original_and_waits_for_target_health(
    fake_canary_host: dict[str, str], dual_stack: bool,
) -> None:
    configure_legacy_bridge(fake_canary_host, dual_stack=dual_stack)
    fake_canary_host["env"]["TEST_ALB_HEALTHY"] = "false"
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "Previous container restored" in result.stdout
    previous = json.loads(Path(fake_canary_host["state"]).read_text())["containers"]["legacy-1"]
    assert previous["running"] is True
    assert previous["name"] == "cloudops-flask"
    assert previous["network"] == "bridge"
    assert previous["port_bindings"] == {"5000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5000"}]}


def test_legacy_bridge_unexpected_port_mapping_is_rejected_without_mutation(
    fake_canary_host: dict[str, str],
) -> None:
    configure_legacy_bridge(fake_canary_host, {
        "5000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5000"}],
        "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}],
    })
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "network or published ports failed validation" in result.stdout
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    previous = state["containers"]["legacy-1"]
    assert previous["running"] is True
    assert previous["name"] == "cloudops-flask"
    assert previous["network"] == "bridge"
    assert not any("candidate" in item["name"] for item in state["containers"].values())


def test_ambiguous_running_legacy_images_are_rejected_before_candidate(fake_canary_host: dict[str, str]) -> None:
    state_path = Path(fake_canary_host["state"])
    state = json.loads(state_path.read_text())
    state["containers"]["legacy-2"] = {
        "id": hashlib.sha256(b"legacy-2").hexdigest(), "name": "unexpected-legacy-copy", "image": "cloudops-flask:1.1",
        "running": True, "network": "bridge", "port_bindings": {
            "5000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5000"}],
        }, "labels": {}, "health": "none", "mounts": [],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "multiple running containers" in result.stdout
    state = json.loads(state_path.read_text())
    assert state["containers"]["legacy-1"]["running"] is True
    assert state["containers"]["legacy-2"]["running"] is True
    assert not any("candidate" in item["name"] for item in state["containers"].values())


@pytest.mark.parametrize("configured,active,publication,accepted", [
    ([""], ["0.0.0.0"], None, True),
    ([""], ["0.0.0.0", "::"], None, True),
    (["0.0.0.0"], ["0.0.0.0", "::"], None, True),
    (["0.0.0.0", "::"], ["0.0.0.0", "::"], None, True),
    (["::"], ["::"], None, True),
    (["0.0.0.0", "0.0.0.0"], ["0.0.0.0"], None, False),
    (["", "::"], ["0.0.0.0", "::"], None, False),
    (["0.0.0.0"], ["0.0.0.0", "0.0.0.0"], None, False),
    (["0.0.0.0"], ["127.0.0.1"], None, False),
    (["127.0.0.1"], ["127.0.0.1"], None, False),
    (["0.0.0.0", "::"], ["0.0.0.0"], None, False),
    (["::"], ["0.0.0.0", "::"], None, False),
    (["0.0.0.0"], ["0.0.0.0"], "5000/tcp -> 0.0.0.0:5000\n5000/tcp -> 0.0.0.0:5000", False),
    (["0.0.0.0"], ["0.0.0.0"], "5000/tcp -> 0.0.0.0:5000\n8080/tcp -> 0.0.0.0:8080", False),
    (["0.0.0.0"], ["0.0.0.0"], "5000/tcp -> 0.0.0.0:5001", False),
    (["0.0.0.0"], ["0.0.0.0", "::"], "5000/tcp -> 0.0.0.0:5000", False),
])
def test_effective_legacy_publications(
    fake_canary_host, configured, active, publication, accepted,
) -> None:
    bindings = {"5000/tcp": [{"HostIp": host, "HostPort": "5000"} for host in configured]}
    configure_legacy_bridge(fake_canary_host, bindings)
    state_path = Path(fake_canary_host["state"])
    state = json.loads(state_path.read_text())
    legacy = state["containers"]["legacy-1"]
    legacy["active_ports"] = {"5000/tcp": [{"HostIp": host, "HostPort": "5000"} for host in active]}
    if publication is not None:
        legacy["publications"] = publication
    state_path.write_text(json.dumps(state), encoding="utf-8")
    script = (ROOT / "deploy/cloudops-deploy.sh").read_text()
    validator = script[script.index("validate_legacy_network() {"):script.index("if ((EUID != 0));")]
    result = subprocess.run(
        ["bash", "-eu", "-c", validator + '\nvalidate_legacy_network bridge "$1" "$2"',
         "validator", json.dumps(bindings), legacy["id"]],
        env=fake_canary_host["env"], capture_output=True, text=True, timeout=10,
    )
    assert (result.returncode == 0) == accepted, result.stdout + result.stderr
    assert json.loads(state_path.read_text()) == state


@pytest.mark.parametrize("source,port,host_port", [
    ("port_bindings", "5000/tcp", "5001"),
    ("active_ports", "5000/tcp", "5001"),
    ("active_ports", "8080/tcp", "8080"),
    ("active_ports", "5000/udp", "5000"),
])
def test_unexpected_inspected_publications_rejected(fake_canary_host, source, port, host_port) -> None:
    configure_legacy_bridge(fake_canary_host)
    state_path = Path(fake_canary_host["state"])
    state = json.loads(state_path.read_text())
    legacy = state["containers"]["legacy-1"]
    legacy["active_ports"] = {"5000/tcp": [{"HostIp": "0.0.0.0", "HostPort": "5000"}]}
    legacy[source][port] = [{"HostIp": "0.0.0.0", "HostPort": host_port}]
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "network or published ports failed validation" in result.stdout
    assert json.loads(state_path.read_text()) == state


def test_successful_cutover_from_managed_previous_container(fake_canary_host: dict[str, str]) -> None:
    state_path = Path(fake_canary_host["state"])
    state = json.loads(state_path.read_text())
    old_image = "489502663059.dkr.ecr.eu-north-1.amazonaws.com/apexforge-cloudops-portal@sha256:" + "9" * 64
    state["containers"] = {"managed-1": {
        "id": hashlib.sha256(b"managed-1").hexdigest(), "name": "cloudops-app", "image": old_image, "running": True,
        "network": "host", "labels": {"service": "cloudops", "org.apexforge.version": "9" * 12},
        "health": "healthy", "mounts": [],
    }}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(state_path.read_text())
    old = state["containers"]["managed-1"]
    current = next(item for item in state["containers"].values() if item["name"] == "cloudops-app")
    assert old["image"] == old_image
    assert old["running"] is False
    assert old["name"].startswith("cloudops-rollback-")
    assert current["image"] == fake_canary_host["image"]
    assert current["running"] is True


def test_failed_candidate_keeps_legacy_running_and_never_cuts_over(fake_canary_host: dict[str, str]) -> None:
    fake_canary_host["env"]["TEST_CANDIDATE_DOCKER_HEALTH"] = "unhealthy"
    fake_canary_host["env"]["TEST_CANDIDATE_HTTP"] = "503"
    fake_canary_host["env"]["TEST_CANDIDATE_READY"] = "503"
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "previous port-5000 service remains running" in result.stdout
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    assert state["containers"]["legacy-1"]["running"] is True
    assert state["containers"]["legacy-1"]["name"] == "cloudops-flask"
    assert not any(item["name"] == "cloudops-app" for item in state["containers"].values())
    failed = next(item for item in state["containers"].values() if item["name"].startswith("cloudops-failed-candidate-"))
    assert failed["running"] is False


@pytest.mark.parametrize("prerequisite, expected_message", [
    ("secret", "signing key does not match"),
    ("log_group", "log group is unavailable"),
    ("ecr", "existing port-5000 service remains untouched"),
    ("runtime", "runtime.env is missing"),
    ("runtime_mode", "mode 0600"),
])
def test_missing_canary_prerequisite_fails_before_candidate_or_cutover(
    fake_canary_host: dict[str, str], prerequisite: str, expected_message: str
) -> None:
    if prerequisite == "secret":
        fake_canary_host["env"]["TEST_SECRET"] = "a-different-stable-secret-value-long-enough-0123456789"
    elif prerequisite == "log_group":
        fake_canary_host["env"]["TEST_LOG_GROUP_MISSING"] = "true"
    elif prerequisite == "ecr":
        fake_canary_host["env"]["TEST_ECR_PULL_FAIL"] = "true"
    elif prerequisite == "runtime":
        Path(fake_canary_host["env"]["CLOUDOPS_RUNTIME_ENV"]).unlink()
    else:
        Path(fake_canary_host["env"]["CLOUDOPS_RUNTIME_ENV"]).chmod(0o640)
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert expected_message in result.stdout
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    assert state["containers"]["legacy-1"]["running"] is True
    assert state["containers"]["legacy-1"]["name"] == "cloudops-flask"
    assert len(state["containers"]) == 1


def test_failed_production_verification_restores_exact_previous_container(fake_canary_host: dict[str, str]) -> None:
    fake_canary_host["env"]["TEST_PRODUCTION_HTTP"] = "503"
    fake_canary_host["env"]["TEST_PRODUCTION_READY"] = "503"
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "Previous container restored" in result.stdout
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    previous = state["containers"]["legacy-1"]
    assert previous["running"] is True
    assert previous["name"] == "cloudops-flask"
    assert previous["image"] == "cloudops-flask:1.1"
    assert any(item["name"].startswith("cloudops-failed-") and item["running"] is False for item in state["containers"].values())


def test_failed_required_alb_health_restores_previous_container(fake_canary_host: dict[str, str]) -> None:
    fake_canary_host["env"]["TEST_ALB_HEALTHY"] = "false"
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "Previous container restored" in result.stdout
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    previous = state["containers"]["legacy-1"]
    assert previous["running"] is True
    assert previous["name"] == "cloudops-flask"
    assert previous["image"] == "cloudops-flask:1.1"
