"""Regression checks for opt-in CloudOps deployment helpers."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

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
    assert "CLOUDOPS_DEPLOY_APPROVERS" in pipeline
    assert "submitter: env.CLOUDOPS_DEPLOY_APPROVERS" in pipeline
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


def test_asg_preflight_uses_elb_target_group_arn_key(tmp_path: Path) -> None:
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
        }]},
        "launch-template.json": {"VersionNumber": 5, "LaunchTemplateData": {
            "ImageId": "ami-09b67ca726bea7328", "InstanceType": "t3.micro",
            "IamInstanceProfile": {"Name": "CloudOpsEC2Role"}, "SecurityGroupIds": ["sg-app"],
        }},
        "security-groups.json": {"SecurityGroups": [{"GroupId": "sg-app", "IpPermissions": [{
            "IpProtocol": "tcp", "FromPort": 5000, "ToPort": 5000,
            "UserIdGroupPairs": [{"GroupId": "sg-alb"}, {"GroupId": "sg-obs"}],
        }]}]},
    }
    for name, document in snapshots.items():
        (tmp_path / name).write_text(json.dumps(document), encoding="utf-8")

    found_arn, security_groups = helper.validate_common(tmp_path, "asg")
    assert found_arn == target_arn
    assert security_groups == ["sg-app"]


@pytest.fixture
def fake_canary_host(tmp_path: Path) -> dict[str, str]:
    if sys.platform != "linux" or os.geteuid() != 0 or not shutil.which("bash"):
        pytest.skip("canary cutover integration tests require a root Linux shell")

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state_path = tmp_path / "docker-state.json"
    secret = "test-stable-session-signing-key-0123456789abcdef"
    state_path.write_text(json.dumps({"containers": {
        "legacy-1": {"id": "legacy-1", "name": "cloudops-flask", "image": "cloudops-flask:1.1", "running": True, "network": "host", "labels": {}, "health": "none", "mounts": []}
    }, "next": 0}), encoding="utf-8")

    docker_stub = r'''#!/usr/bin/env python3
import json, os, sys
p=os.environ["DOCKER_STATE"]
state=json.load(open(p)); cs=state["containers"]; a=sys.argv[1:]
def save(): json.dump(state,open(p,"w"))
def get(x):
    if x in cs: return cs[x]
    return next((c for c in cs.values() if c["name"]==x),None)
def fmt(t,c):
    if t=="{{.Id}}": return c["id"]
    if t=="{{.Config.Image}}": return c["image"]
    if t=="{{.State.Running}}": return str(c["running"]).lower()
    if t=="{{.HostConfig.NetworkMode}}": return c["network"]
    if t=="{{.Name}}": return "/"+c["name"]
    if t=="{{json .Mounts}}": return json.dumps(c["mounts"])
    if t=='{{index .Config.Labels "service"}}': return c["labels"].get("service","<no value>")
    if t=='{{index .Config.Labels "org.apexforge.version"}}': return c["labels"].get("org.apexforge.version","<no value>")
    if t=="{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}": return c["health"]
    return ""
if a[0]=="ps":
    if "--quiet" in a:
        print("\n".join(c["id"] for c in cs.values() if c["running"] and c["image"]=="cloudops-flask:1.1"))
    else:
        for c in cs.values(): print(f'container name={c["name"]} image={c["image"]}')
elif a[0]=="inspect":
    target=a[-1]; c=get(target)
    if c is None: sys.exit(1)
    if "--format" in a: print(fmt(a[a.index("--format")+1],c))
elif a[0]=="pull": sys.exit(1) if os.getenv("TEST_ECR_PULL_FAIL")=="true" else None
elif a[0]=="login": sys.stdin.read()
elif a[0]=="run":
    name=a[a.index("--name")+1]; image=next(x for x in a if x.startswith("489502663059.dkr.ecr.") and "@sha256:" in x)
    labels={}
    for i,x in enumerate(a[:-1]):
        if x=="--label":
            k,v=a[i+1].split("=",1); labels[k]=v
    state["next"]+=1; ident=f'new-{state["next"]}'
    candidate="candidate" in name
    c={"id":ident,"name":name,"image":image,"running":True,"network":"host","labels":labels,"health":os.getenv("TEST_CANDIDATE_DOCKER_HEALTH" if candidate else "TEST_PRODUCTION_DOCKER_HEALTH","healthy"),"mounts":[]}
    cs[ident]=c; save(); print(ident)
elif a[0]=="stop":
    c=get(a[-1]); c["running"]=False; save(); print(c["name"])
elif a[0]=="start":
    c=get(a[-1]); c["running"]=True; save(); print(c["name"])
elif a[0]=="rename":
    c=get(a[-2]); c["name"]=a[-1]; save()
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
        "TEST_SECRET": secret,
        "CLOUDOPS_RUNTIME_ENV": str(runtime_env),
        "CLOUDOPS_DEPLOY_LOG": str(tmp_path / "deploy.log"),
        "CLOUDOPS_VERIFY_SCRIPT": str(verify),
        "CLOUDOPS_CANDIDATE_WAIT_SECONDS": "0",
        "CLOUDOPS_PRODUCTION_WAIT_SECONDS": "0",
        "CLOUDOPS_ALB_WAIT_SECONDS": "0",
    })
    return {"env": env, "state": str(state_path), "image": image, "version": "a" * 40, "tmp": str(tmp_path)}


def run_fake_deploy(host: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "deploy/cloudops-deploy.sh"), host["image"], host["version"],
         "--target-group-arn", "arn:aws:elasticloadbalancing:eu-north-1:489502663059:targetgroup/tg-cloudops-app/abc",
         "--instance-id", "i-02777a62f2a65bc1e"],
        env=host["env"], capture_output=True, text=True, timeout=30,
    )


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


def test_successful_canary_candidate_cutover_retains_previous_container(fake_canary_host: dict[str, str]) -> None:
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    production = next(item for item in state["containers"].values() if item["name"] == "cloudops-app")
    previous = state["containers"]["legacy-1"]
    candidate = next(item for item in state["containers"].values() if item["name"].startswith("cloudops-verified-"))
    assert production["running"] and production["image"] == fake_canary_host["image"]
    assert previous["running"] is False and previous["name"].startswith("cloudops-rollback-")
    assert previous["image"] == "cloudops-flask:1.1"
    assert candidate["running"] is False


def test_successful_cutover_from_managed_previous_container(fake_canary_host: dict[str, str]) -> None:
    state_path = Path(fake_canary_host["state"])
    state = json.loads(state_path.read_text())
    old_image = "489502663059.dkr.ecr.eu-north-1.amazonaws.com/apexforge-cloudops-portal@sha256:" + "9" * 64
    state["containers"] = {"managed-1": {
        "id": "managed-1", "name": "cloudops-app", "image": old_image, "running": True,
        "network": "host", "labels": {"service": "cloudops", "org.apexforge.version": "9" * 12},
        "health": "healthy", "mounts": [],
    }}
    state_path.write_text(json.dumps(state), encoding="utf-8")
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(state_path.read_text())
    old = state["containers"]["managed-1"]
    current = next(item for item in state["containers"].values() if item["name"] == "cloudops-app")
    assert old["image"] == old_image and old["running"] is False and old["name"].startswith("cloudops-rollback-")
    assert current["image"] == fake_canary_host["image"] and current["running"] is True


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
    assert previous["running"] is True and previous["name"] == "cloudops-flask"
    assert previous["image"] == "cloudops-flask:1.1"
    assert any(item["name"].startswith("cloudops-failed-") and item["running"] is False for item in state["containers"].values())


def test_failed_required_alb_health_restores_previous_container(fake_canary_host: dict[str, str]) -> None:
    fake_canary_host["env"]["TEST_ALB_HEALTHY"] = "false"
    result = run_fake_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "Previous container restored" in result.stdout
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    previous = state["containers"]["legacy-1"]
    assert previous["running"] is True and previous["name"] == "cloudops-flask"
    assert previous["image"] == "cloudops-flask:1.1"
