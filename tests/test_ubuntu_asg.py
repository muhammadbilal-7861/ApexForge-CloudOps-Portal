"""Ubuntu-specific bootstrap and non-launching ASG authorization regressions."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.test_clean_asg import COMMIT, IMAGE, bootstrap_host, read_fixture, rendered_bootstrap
from tests.test_deployment_foundation import ROOT, fake_canary_host, load_helper


def reviewed_overrides():
    renderer = load_helper("render-launch-template-data")
    source = read_fixture("cloudops-launch-template-v5.json")["LaunchTemplateData"]
    encoded = base64.b64encode(rendered_bootstrap().encode()).decode()
    return renderer.render(source, encoded, COMMIT[:12])


@pytest.mark.parametrize("subnet", ["subnet-08469c4e69b5c4d65", "subnet-05788ba98ca1096e6"])
def test_permission_request_preserves_reviewed_configuration_for_each_subnet(subnet):
    renderer = load_helper("render-launch-permission-check")
    result = renderer.render(reviewed_overrides(), "5", subnet)
    assert result["DryRun"] is True
    assert result["MinCount"] == 1
    assert result["MaxCount"] == 1
    assert result["ImageId"] == "ami-0769f265f707fecc8"
    assert result["InstanceType"] == "t3.micro"
    assert result["LaunchTemplate"]["Version"] == "5"
    assert result["NetworkInterfaces"][0]["SubnetId"] == subnet
    assert result["NetworkInterfaces"][0]["Groups"] == ["sg-0f9613afd389c288d"]
    assert result["NetworkInterfaces"][0]["AssociatePublicIpAddress"] is False
    assert result["MetadataOptions"]["HttpTokens"] == "required"
    assert result["IamInstanceProfile"]["Arn"].endswith("instance-profile/CloudOpsEC2Role")
    tags = {tag["Key"]: tag["Value"] for item in result["TagSpecifications"] if item["ResourceType"] == "instance" for tag in item["Tags"]}
    assert tags["Role"] == "app"
    assert tags["Environment"] == "Interview-Lab"
    assert tags["Name"] == "cloudops-asg-app"
    assert tags["Project"] == "ApexForge-CloudOps"
    assert tags["Version"] == COMMIT[:12]


@pytest.mark.parametrize("subnet", ["subnet-unapproved", "", "subnet-08469c4e69b5c4d65,subnet-05788ba98ca1096e6"])
def test_permission_renderer_rejects_unapproved_or_ambiguous_subnet(subnet):
    renderer = load_helper("render-launch-permission-check")
    overrides = reviewed_overrides()
    with pytest.raises(ValueError, match="approved subnet"):
        renderer.render(overrides, "5", subnet)


@pytest.mark.parametrize("tags", [{}, {"Role": "unexpected"}])
def test_permission_renderer_rejects_unreviewed_asg_tags(tags):
    renderer = load_helper("render-launch-permission-check")
    overrides = reviewed_overrides()
    with pytest.raises(ValueError, match="propagated tags"):
        renderer.render(overrides, "5", "subnet-08469c4e69b5c4d65", tags)


@pytest.mark.parametrize("operation,code", [
    ("none", "DryRunOperation"), ("none", "prefixed-success"), ("create-launch-template-version", "UnauthorizedOperation"),
    ("run-instances", "UnauthorizedOperation"), ("run-instances", "AccessDenied"),
    ("run-instances", "RequestLimitExceeded"), ("run-instances", "unexpected-success"),
])
def test_permission_check_never_launches_and_fails_closed_on_api_errors(tmp_path, operation, code):
    workspace = tmp_path / "workspace"
    deploy = workspace / "deploy"
    deploy.mkdir(parents=True)
    work = workspace / ".deploy-work"
    work.mkdir()
    for name in ("validate-launch-permissions.sh", "render-launch-permission-check.py", "cloudops_ami.py", "reviewed-ubuntu24.json"):
        shutil.copyfile(ROOT / "deploy" / name, deploy / name)
    (deploy / "assert-deploy-context.sh").write_text("#!/bin/bash\nexit 0\n")
    (work / "launch-template-overrides.json").write_text(json.dumps(reviewed_overrides()))
    review = json.loads((deploy / "reviewed-ubuntu24.json").read_text())
    (work / "asg.json").write_text(json.dumps({"AutoScalingGroups": [{
        "AutoScalingGroupName": "asg-cloudops-app", "VPCZoneIdentifier": ",".join(review["private_subnets"]),
        "Tags": [{"Key": key, "Value": value, "PropagateAtLaunch": True} for key, value in review["asg_propagated_tags"].items()],
    }]}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('''#!/usr/bin/env python3
import json, os, sys
a=sys.argv[1:]
if "test-python" in a:
    os.execv(sys.executable,[sys.executable,*a[a.index("test-python")+2:]])
a=a[a.index("test-aws")+1:]
with open(os.environ["CALLS"],"a") as log: log.write(json.dumps(a)+"\\n")
assert "--dry-run" in a
code=os.environ["ERROR_CODE"] if a[1]==os.environ["FAIL_OPERATION"] else "DryRunOperation"
if code=="unexpected-success": sys.exit(0)
prefix="aws: [ERROR]: " if os.environ["ERROR_CODE"]=="prefixed-success" else ""
print(f"{prefix}An error occurred ({code}) when calling the Test operation: private-response",file=sys.stderr)
sys.exit(255)
''')
    docker.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", WORKSPACE=str(workspace),
               AWS_CLI_IMAGE="test-aws", PYTHON_IMAGE="test-python", AWS_REGION="eu-north-1",
               CALLS=str(calls), ERROR_CODE=code, FAIL_OPERATION=operation)
    result = subprocess.run(["bash", "deploy/validate-launch-permissions.sh", "6"], cwd=workspace,
                            env=env, capture_output=True, text=True, timeout=10)
    requests = [json.loads(line) for line in calls.read_text().splitlines()]
    for request in requests:
        assert "--dry-run" in request
        assert request[0] == "ec2"
        assert request[1] in ("create-launch-template-version", "run-instances")
    assert "private-response" not in result.stdout + result.stderr
    report = workspace / "reports/asg-launch-permissions.json"
    if operation == "none":
        assert result.returncode == 0, result.stderr
        assert len(requests) == 3
        assert json.loads(report.read_text())["templateVersion"] == "6"
        subnets = []
        for request in requests[1:]:
            path = Path(request[request.index("--cli-input-json") + 1].removeprefix("file://"))
            document = json.loads(path.read_text())
            assert document["DryRun"] is True
            subnets.append(document["NetworkInterfaces"][0]["SubnetId"])
        assert set(subnets) == {"subnet-08469c4e69b5c4d65", "subnet-05788ba98ca1096e6"}
    else:
        assert result.returncode != 0
        assert not report.exists()
        assert "permission validation failed" in result.stderr


def test_jenkins_archives_preview_before_restricted_approval():
    pipeline = (ROOT / "Jenkinsfile").read_text()
    preview = pipeline.index("archiveArtifacts artifacts: 'reports/asg-launch-template-preview.json'")
    approval = pipeline.index("stage('Manual deployment approval')")
    assert preview < approval
    assert "artifact/reports/asg-launch-template-preview.json" in pipeline
    assert "submitter: env.CLOUDOPS_DEPLOY_APPROVERS" in pipeline


def test_launch_iam_is_scoped_to_reviewed_image_subnets_role_and_tags():
    policy = json.loads((ROOT / "deploy/iam/jenkins-cloudops-deploy-policy.json").read_text())
    statements = {item["Sid"]: item for item in policy["Statement"]}
    run = statements["LaunchOnlyReviewedCloudOpsResources"]
    assert "arn:aws:ec2:eu-north-1::image/ami-0769f265f707fecc8" in run["Resource"]
    assert not any("ami-09b67ca726bea7328" in resource for resource in run["Resource"])
    subnets = {resource.rsplit("/", 1)[-1] for resource in run["Resource"] if ":subnet/" in resource}
    assert subnets == {"subnet-08469c4e69b5c4d65", "subnet-05788ba98ca1096e6"}
    instance = statements["LaunchOnlyTaggedT3MicroWithImdsv2"]["Condition"]["StringEquals"]
    assert instance["ec2:InstanceType"] == "t3.micro"
    assert instance["ec2:MetadataHttpTokens"] == "required"
    assert instance["aws:RequestTag/Role"] == "app"
    tags = statements["TagOnlyCloudOpsResourcesDuringLaunch"]["Condition"]
    assert tags["StringEquals"]["ec2:CreateAction"] == "RunInstances"
    assert tags["StringEquals"]["aws:RequestedRegion"] == "eu-north-1"
    assert set(tags["ForAllValues:StringEquals"]["aws:TagKeys"]) == {"Role", "Monitoring", "Version", "Environment", "Name", "Project", "aws:autoscaling:groupName"}
    passed_role = statements["PassOnlyCloudOpsAppRoleToEc2"]
    assert passed_role["Resource"] == "arn:aws:iam::489502663059:role/CloudOpsEC2Role"
    assert passed_role["Condition"]["StringEquals"]["iam:PassedToService"] == "ec2.amazonaws.com"


@pytest.mark.parametrize("state", ["absent", "competing-deb"])
def test_ubuntu_bootstrap_handles_snap_install_or_refuses_duplicate_agent(bootstrap_host, state):
    host = bootstrap_host
    host["env"]["TEST_SSM_ABSENT" if state == "absent" else "TEST_SSM_DEB"] = "true"
    result = subprocess.run(["bash", str(host["bootstrap"])], env=host["env"], capture_output=True, text=True, timeout=30)
    if state == "absent":
        assert result.returncode == 0
        tools = [json.loads(line) for line in (host["root"] / "tools.jsonl").read_text().splitlines()]
        assert ["snap", "install", "amazon-ssm-agent", "--classic", "--channel=stable"] in tools
        assert ["systemctl", "is-active", "--quiet", "snap.amazon-ssm-agent.amazon-ssm-agent.service"] in tools
    else:
        assert result.returncode != 0
        assert not (host["root"] / "var/lib/cloudops/bootstrap-complete.json").exists()
        state = json.loads(Path(host["state"]).read_text())
        assert state["containers"] == {}


@pytest.mark.parametrize("ping", ["Online", "Offline", "AccessDenied"])
def test_asg_ssm_verification_requires_online_and_fails_on_authorization(tmp_path, ping):
    workspace = tmp_path / "workspace"
    shutil.copytree(ROOT / "deploy", workspace / "deploy")
    script = workspace / "deploy/cloudops-ssm-deploy.sh"
    script.write_text(script.read_text().replace("deadline=$((SECONDS + 900))", "deadline=$SECONDS"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    git = bin_dir / "git"
    git.write_text(f"#!/bin/bash\nprintf '%s\\n' {COMMIT}\n")
    git.chmod(0o755)
    docker = bin_dir / "docker"
    docker.write_text('''#!/usr/bin/env python3
import json, os, sys
a=sys.argv[1:]
if "test-python" in a:
    os.execv(sys.executable,[sys.executable,*a[a.index("test-python")+2:]])
a=a[a.index("test-aws")+1:]
with open(os.environ["CALLS"],"a") as log: log.write(json.dumps(a)+"\\n")
if a[:2]==["sts","get-caller-identity"]:
    print("489502663059" if a[a.index("--query")+1]=="Account" else "arn:aws:sts::489502663059:assumed-role/DevSecOpsToolsRole/test")
elif a[:2]==["elbv2","describe-target-groups"]:
    print("arn:aws:elasticloadbalancing:eu-north-1:489502663059:targetgroup/tg-cloudops-app/test")
elif a[:2]==["ssm","describe-instance-information"]:
    if os.environ["PING"]=="AccessDenied":
        print("AccessDenied",file=sys.stderr); sys.exit(254)
    print(os.environ["PING"])
elif a[:2]==["ssm","send-command"]: print("test-command")
elif a[:2]==["ssm","list-command-invocations"]: print("Success")
elif a[:2]==["ssm","get-command-invocation"]: print("Verification passed")
else: sys.exit(2)
''')
    docker.chmod(0o755)
    calls = tmp_path / "calls.jsonl"
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", WORKSPACE=str(workspace),
               AWS_CLI_IMAGE="test-aws", PYTHON_IMAGE="test-python", AWS_REGION="eu-north-1",
               AWS_ACCOUNT_ID="489502663059", AWS_EXPECTED_ROLE="DevSecOpsToolsRole", SCM_BRANCH="main",
               GIT_COMMIT_FULL=COMMIT, GIT_COMMIT_SHORT=COMMIT[:12], ECR_DEPLOY_IMAGE=IMAGE,
               ECR_IMAGE_DIGEST="sha256:" + "a" * 64, CALLS=str(calls), PING=ping,
               CLOUDOPS_REQUIRE_BOOTSTRAP_COMPLETE="true")
    result = subprocess.run(["bash", str(script), "verify", "i-11111111111111111"], cwd=workspace,
                            env=env, capture_output=True, text=True, timeout=10)
    requests = [json.loads(line) for line in calls.read_text().splitlines()]
    submitted = [request for request in requests if request[:2] == ["ssm", "send-command"]]
    if ping == "Online":
        assert result.returncode == 0, result.stderr
        assert len(submitted) == 1
        document = json.loads((workspace / ".deploy-work/ssm-verify-command.json").read_text())
        assert any("--require-bootstrap-complete" in command for command in document["Parameters"]["commands"])
    else:
        assert result.returncode != 0
        assert submitted == []
        assert len([request for request in requests if request[:2] == ["ssm", "describe-instance-information"]]) == 1


@pytest.mark.parametrize("existing", ["container", "runtime"])
def test_ubuntu_bootstrap_refuses_contaminated_state_without_reusing_it(bootstrap_host, existing):
    host = bootstrap_host
    path = Path(host["state"])
    if existing == "container":
        path.write_text(json.dumps({"next": 0, "containers": {
            "old": {"id": "c" * 64, "name": "cloudops-flask", "image": "cloudops-flask:1.1",
                    "running": True, "labels": {}, "network": "bridge", "health": "healthy", "mounts": []}}}))
    else:
        runtime = host["root"] / "etc/cloudops/runtime.env"
        runtime.parent.mkdir(parents=True)
        runtime.write_text("SECRET_KEY=legacy-value\nDATABASE_URL=legacy-url\n")
    before = path.read_bytes()
    result = subprocess.run(["bash", str(host["bootstrap"])], env=host["env"], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert path.read_bytes() == before
    assert not (host["root"] / "var/lib/cloudops/bootstrap-complete.json").exists()
    assert (host["root"] / "gate.json").read_text() == "true"
    if existing == "runtime":
        assert runtime.read_text() == "SECRET_KEY=legacy-value\nDATABASE_URL=legacy-url\n"
