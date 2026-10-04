"""Clean-image provenance, first-boot isolation and safe launch preview regressions."""
from __future__ import annotations

import base64
import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

from tests.test_deployment_foundation import ROOT, fake_canary_host, load_helper, write_asg_preflight_snapshots

FIXTURES = ROOT / "tests/fixtures"
IMAGE = "489502663059.dkr.ecr.eu-north-1.amazonaws.com/apexforge-cloudops-portal@sha256:" + "a" * 64
COMMIT = "b" * 40


def read_fixture(name):
    return json.loads((FIXTURES / name).read_text())


def rendered_bootstrap():
    return load_helper("render-cloudops-user-data").render(
        (ROOT / "deploy/cloudops-user-data.sh.tmpl").read_text(), image=IMAGE,
        version=COMMIT[:12], commit=COMMIT, deploy_script=(ROOT / "deploy/cloudops-deploy.sh").read_bytes(),
        verify_script=(ROOT / "deploy/cloudops-verify.sh").read_bytes())


def test_preview_preserves_source_v5_profile_and_group_and_removes_subnet():
    renderer = load_helper("render-launch-template-data")
    source = read_fixture("cloudops-launch-template-v5.json")
    original = copy.deepcopy(source)
    encoded = base64.b64encode(rendered_bootstrap().encode()).decode()
    overrides = renderer.render(source["LaunchTemplateData"], encoded, COMMIT[:12])
    result = load_helper("preview-cloudops-asg").preview(
        source, read_fixture("aws-reviewed-ubuntu24.json"), read_fixture("aws-reviewed-ubuntu24-parameter.json"), overrides)
    assert source == original
    assert result["sourceVersion"] == 5
    assert result["newImageId"] == "ami-0769f265f707fecc8"
    assert result["imageOwnerId"] == "099720109477"
    assert result["instanceProfile"] == {"Arn": "arn:aws:iam::489502663059:instance-profile/CloudOpsEC2Role"}
    interface = result["networkInterfaces"][0]
    assert interface["Groups"] == ["sg-0f9613afd389c288d"]
    assert "SubnetId" not in interface
    assert interface["AssociatePublicIpAddress"] is False
    assert result["metadataOptions"]["HttpTokens"] == "required"
    assert result["metadataOptions"]["HttpPutResponseHopLimit"] == 1
    assert result["userDataValidation"]["bashSyntax"] == "passed"
    assert result["capacityChanged"] is False
    serialized = json.dumps(result)
    assert "SECRET_KEY" not in serialized
    assert encoded not in serialized
    assert "UserData" not in result


@pytest.mark.parametrize("key,value", [
    ("OwnerId", "489502663059"), ("ImageId", "ami-09b67ca726bea7328"), ("Public", False),
    ("Architecture", "arm64"), ("State", "pending"), ("ImageOwnerAlias", "private"),
    ("ImageLocation", "private/contaminated"), ("PublicSsmParameterName", "aws/service/untrusted/image"),
    ("DeprecationTime", "2020-01-01T00:00:00Z"),
])
def test_ami_review_rejects_untrusted_or_unavailable_image(key, value):
    load_helper("preview-cloudops-asg")
    from cloudops_ami import validate_ami
    document = read_fixture("aws-reviewed-ubuntu24.json")
    document["Images"][0][key] = value
    with pytest.raises(ValueError):
        validate_ami(document)


def test_public_parameter_provenance_cannot_move_silently():
    load_helper("preview-cloudops-asg")
    from cloudops_ami import validate_ami
    parameter = read_fixture("aws-reviewed-ubuntu24-parameter.json")
    parameter["Parameter"]["Version"] += 1
    document = read_fixture("aws-reviewed-ubuntu24.json")
    with pytest.raises(ValueError, match="public SSM parameter"):
        validate_ami(document, parameter)


def test_asg_preflight_never_approves_launching_the_contaminated_image(tmp_path):
    helper, _ = write_asg_preflight_snapshots(tmp_path)
    path = tmp_path / "asg.json"
    document = json.loads(path.read_text())
    document["AutoScalingGroups"][0].update(MinSize=1, DesiredCapacity=2, MaxSize=2)
    path.write_text(json.dumps(document))
    with pytest.raises(helper.PreflightError, match="contaminated"):
        helper.validate_common(tmp_path, "asg")


def test_asg_preflight_accepts_clean_arn_profile_and_asg_subnet_selection(tmp_path):
    helper, expected_target = write_asg_preflight_snapshots(tmp_path)
    path = tmp_path / "asg.json"
    document = json.loads(path.read_text())
    document["AutoScalingGroups"][0]["LaunchTemplate"]["Version"] = "6"
    document["AutoScalingGroups"][0].update(MinSize=1, DesiredCapacity=2, MaxSize=2)
    path.write_text(json.dumps(document))
    template = read_fixture("cloudops-launch-template-v5.json")
    template["VersionNumber"] = 6
    template["LaunchTemplateData"].pop("Placement")
    template["LaunchTemplateData"].update(
        ImageId="ami-0769f265f707fecc8", MetadataOptions={"HttpTokens": "required", "HttpEndpoint": "enabled", "HttpPutResponseHopLimit": 1},
        NetworkInterfaces=[{"DeviceIndex": 0, "Groups": ["sg-0f9613afd389c288d"], "AssociatePublicIpAddress": False}])
    path = tmp_path / "launch-template.json"
    path.write_text(json.dumps(template))
    target, groups = helper.validate_common(tmp_path, "asg")
    assert target == expected_target
    assert groups == ["sg-0f9613afd389c288d"]
    template["LaunchTemplateData"]["NetworkInterfaces"][0]["SubnetId"] = "subnet-08469c4e69b5c4d65"
    path.write_text(json.dumps(template))
    with pytest.raises(helper.PreflightError, match="subnet selection"):
        helper.validate_common(tmp_path, "asg")


@pytest.mark.parametrize("profile", [
    {"Name": "CloudOpsEC2Role"}, {"Arn": "arn:aws:iam::489502663059:instance-profile/CloudOpsEC2Role"},
])
def test_launch_renderer_supports_exact_profile_name_or_arn(profile):
    source = read_fixture("cloudops-launch-template-v5.json")["LaunchTemplateData"]
    source["IamInstanceProfile"] = profile
    output = load_helper("render-launch-template-data").render(source, base64.b64encode(rendered_bootstrap().encode()).decode(), COMMIT[:12])
    assert output["IamInstanceProfile"] == {"Arn": "arn:aws:iam::489502663059:instance-profile/CloudOpsEC2Role"}


@pytest.mark.parametrize("change", [
    {"IamInstanceProfile": {"Arn": "arn:aws:iam::000000000000:instance-profile/CloudOpsEC2Role"}},
    {"NetworkInterfaces": [{"DeviceIndex": 0, "Groups": ["sg-unexpected"]}]},
    {"BlockDeviceMappings": [{"DeviceName": "/dev/xvda", "Ebs": {"SnapshotId": "snap-contaminated"}}]},
])
def test_launch_renderer_rejects_unreviewed_profile_network_or_disk(change):
    source = read_fixture("cloudops-launch-template-v5.json")["LaunchTemplateData"]
    source.update(change)
    renderer = load_helper("render-launch-template-data")
    encoded = base64.b64encode(rendered_bootstrap().encode()).decode()
    with pytest.raises(ValueError):
        renderer.render(source, encoded, COMMIT[:12])


def first_boot_deploy(host):
    host["env"]["CLOUDOPS_FIRST_BOOT"] = "true"
    return subprocess.run(["bash", str(ROOT / "deploy/cloudops-deploy.sh"), host["image"], host["version"]],
                          env=host["env"], capture_output=True, text=True, timeout=30)


def empty_host(host):
    Path(host["state"]).write_text(json.dumps({"containers": {}, "next": 0}))


@pytest.mark.parametrize("failure", [None, "TEST_CANDIDATE_READY", "TEST_PRODUCTION_READY"])
def test_first_boot_without_previous_container_succeeds_or_leaves_no_serving_app(fake_canary_host, failure):
    empty_host(fake_canary_host)
    if failure:
        fake_canary_host["env"][failure] = "503"
    result = first_boot_deploy(fake_canary_host)
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    if failure:
        assert result.returncode != 0
        assert not any(item["running"] and item["name"] == "cloudops-app" for item in state["containers"].values())
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        application = next(item for item in state["containers"].values() if item["name"] == "cloudops-app")
        assert application["running"] is True
        assert application["image"] == fake_canary_host["image"]
        assert application["health"] == "healthy"
        assert application["network"] == "host"


def test_first_boot_refuses_existing_legacy_container(fake_canary_host):
    before = Path(fake_canary_host["state"]).read_bytes()
    result = first_boot_deploy(fake_canary_host)
    assert result.returncode != 0
    assert "legacy reuse is forbidden" in result.stdout
    assert Path(fake_canary_host["state"]).read_bytes() == before


def test_verifier_checks_runtime_image_id_not_only_configured_image(fake_canary_host):
    empty_host(fake_canary_host)
    assert first_boot_deploy(fake_canary_host).returncode == 0
    state = json.loads(Path(fake_canary_host["state"]).read_text())
    application = next(item for item in state["containers"].values() if item["name"] == "cloudops-app")
    application["runtime_image_id"] = "sha256:" + "f" * 64
    Path(fake_canary_host["state"]).write_text(json.dumps(state))
    result = subprocess.run(["bash", str(ROOT / "deploy/cloudops-verify.sh"), "--expected-image", fake_canary_host["image"],
                             "--expected-version", fake_canary_host["version"]], env=fake_canary_host["env"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "runtime image ID" in result.stderr


@pytest.mark.parametrize("marker_state", ["valid", "missing", "wrong-image"])
def test_per_instance_verification_requires_matching_bootstrap_marker(fake_canary_host, marker_state):
    host = fake_canary_host
    empty_host(host)
    assert first_boot_deploy(host).returncode == 0
    marker = Path(host["tmp"]) / "bootstrap-complete.json"
    if marker_state != "missing":
        marker.write_text(json.dumps(dict(image=host["image"] if marker_state == "valid" else "unexpected",
                                         version=host["version"], commit=host["version"])))
        marker.chmod(0o600)
    verifier = Path(host["tmp"]) / "verify-marker.sh"
    verifier.write_text((ROOT / "deploy/cloudops-verify.sh").read_text().replace("/var/lib/cloudops/bootstrap-complete.json", str(marker)))
    result = subprocess.run(["bash", str(verifier), "--expected-image", host["image"], "--expected-version", host["version"],
                             "--require-container-health", "--require-bootstrap-complete"],
                            env=host["env"], capture_output=True, text=True, timeout=10)
    assert (result.returncode == 0) == (marker_state == "valid")


@pytest.mark.parametrize("failure", [None, "one-unhealthy", "authorization", "instance-verification",
                                     "initial-denied", "initial-partial", "new-version-denied"])
def test_asg_rollout_reuses_approved_candidate_counts_only_its_instances_and_rolls_back(tmp_path, failure):
    if os.name != "posix":
        pytest.skip("shell orchestration requires Linux")
    workspace = tmp_path / "workspace"
    (workspace / "deploy").mkdir(parents=True)
    (workspace / ".deploy-work").mkdir()
    (workspace / "reports").mkdir()
    (workspace / ".deploy-work/launch-template-overrides.json").write_text("{}")
    (workspace / "reports/asg-launch-template-preview.json").write_text("{}")
    for name in ("assert-deploy-context.sh", "collect-cloudops-preflight.sh"):
        (workspace / "deploy" / name).write_text("#!/bin/bash\nexit 0\n")
    (workspace / "deploy/validate-launch-permissions.sh").write_text(
        '#!/bin/bash\n[[ "$1" == 6 ]] || exit 2\n[[ "$TEST_ROLLOUT_FAILURE" != new-version-denied ]]\n')
    (workspace / "deploy/cloudops-ssm-deploy.sh").write_text(
        '#!/bin/bash\n[[ "$CLOUDOPS_REQUIRE_BOOTSTRAP_COMPLETE" == true ]] || exit 2\n'
        '[[ "$*" == "verify i-11111111111111111 i-22222222222222222" ]] || exit 2\n'
        '[[ "$TEST_ROLLOUT_FAILURE" != instance-verification ]]\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a=sys.argv[sys.argv.index("mock-aws")+1:]
with open(os.environ["AWS_TEST_CALLS"],"a") as log: log.write(json.dumps(a)+"\\n")
failure=os.environ["TEST_ROLLOUT_FAILURE"]
state_path=Path(os.environ["ASG_TEST_STATE"])
state=json.loads(state_path.read_text())
if a[:2]==["ec2","create-launch-template-version"]: print("6")
elif a[:2]==["autoscaling","describe-auto-scaling-groups"]:
    query=a[a.index("--query")+1]
    if "LaunchTemplate.Version" in query and "MinSize" in query: print(" ".join(state))
    elif "MinSize" in query: print(" ".join(state[:3]))
    elif "LaunchTemplate.Version" in query: print("5")
    else: print("i-11111111111111111 i-22222222222222222")
elif a[:2]==["elasticloadbalancing","describe-target-groups"] or a[:2]==["elbv2","describe-target-groups"]:
    print("arn:aws:elasticloadbalancing:eu-north-1:489502663059:targetgroup/tg-cloudops-app/test")
elif a[:2]==["elbv2","describe-target-health"]:
    if failure=="authorization": sys.exit(254)
    if "--targets" not in a: raise SystemExit("Global target counts could include the canary")
    target=a[a.index("--targets")+1]
    print("unhealthy" if failure=="one-unhealthy" and "i-2222" in target else "healthy")
elif a[:2]==["autoscaling","update-auto-scaling-group"]:
    initial=a[a.index("--desired-capacity")+1]=="2"
    if initial and failure=="initial-denied": sys.exit(254)
    state=[a[a.index(flag)+1] for flag in ("--min-size","--desired-capacity","--max-size")]
    state.append(a[a.index("--launch-template")+1].rsplit("=",1)[-1] if "--launch-template" in a else json.loads(state_path.read_text())[3])
    state_path.write_text(json.dumps(state))
    if initial and failure=="initial-partial": sys.exit(254)
else: sys.exit(2)
''')
    docker.chmod(0o755)
    script = tmp_path / "rollout.sh"
    script.write_text((ROOT / "deploy/cloudops-asg-rollout.sh").read_text().replace("target_deadline=$((SECONDS + 1800))", "target_deadline=$SECONDS"))
    call_log = tmp_path / "aws-calls.jsonl"
    state_path = tmp_path / "asg-state.json"
    state_path.write_text(json.dumps(["0", "0", "0", "5"]))
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", WORKSPACE=str(workspace), AWS_CLI_IMAGE="mock-aws",
               PYTHON_IMAGE="unused-python", AWS_REGION="eu-north-1", ECR_DEPLOY_IMAGE=IMAGE,
               GIT_COMMIT_SHORT=COMMIT[:12], GIT_COMMIT_FULL=COMMIT, ASG_AMI_REVIEWED="true", ASG_VALIDATED_VERSION="6",
               AWS_TEST_CALLS=str(call_log), ASG_TEST_STATE=str(state_path), TEST_ROLLOUT_FAILURE=failure or "none")
    result = subprocess.run(["bash", str(script)], cwd=workspace, env=env, capture_output=True, text=True, timeout=10)
    calls = [json.loads(line) for line in call_log.read_text().splitlines()]
    assert not any(call[:2] == ["ec2", "create-launch-template-version"] for call in calls)
    updates = [call for call in calls if call[:2] == ["autoscaling", "update-auto-scaling-group"]]
    if failure:
        assert result.returncode != 0
        if failure == "new-version-denied":
            assert updates == []
        elif failure == "initial-denied":
            assert len(updates) == 1
            assert "no rollback update needed" in result.stderr
        else:
            restored = updates[-1]
            for flag in ("--min-size", "--desired-capacity", "--max-size"):
                assert restored[restored.index(flag) + 1] == "0"
            assert "--launch-template" not in restored
        expected_version = "5" if failure in ("new-version-denied", "initial-denied") else "6"
        assert json.loads(state_path.read_text()) == ["0", "0", "0", expected_version]
        assert not (workspace / "reports/deployment-evidence.json").exists()
    else:
        assert result.returncode == 0, result.stdout + result.stderr
        evidence = json.loads((workspace / "reports/deployment-evidence.json").read_text())
        assert evidence["healthyTargets"] == 2
        assert evidence["instances"] == ["i-11111111111111111", "i-22222222222222222"]


@pytest.fixture
def bootstrap_host(fake_canary_host):
    host = fake_canary_host
    empty_host(host)
    root = Path(host["tmp"]) / "root"
    for folder in ("var/log", "var/lib/cloudops", "etc", "run", "opt/aws/amazon-cloudwatch-agent/bin"):
        (root / folder).mkdir(parents=True, exist_ok=True)
    (root / "etc/os-release").write_text("ID=ubuntu\nVERSION_ID=24.04\n")
    tool_log = root / "tools.jsonl"
    gate = root / "gate.json"
    host["env"].update(BOOTSTRAP_TOOL_LOG=str(tool_log), BOOTSTRAP_GATE=str(gate),
                       BOOTSTRAP_SOURCE_ROOT=str(ROOT / "deploy"),
                       CLOUDOPS_RUNTIME_ENV=str(root / "etc/cloudops/runtime.env"))
    generic = '''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
name=Path(sys.argv[0]).name; a=sys.argv[1:]
with open(os.environ["BOOTSTRAP_TOOL_LOG"],"a") as log: log.write(json.dumps([name,*a])+"\\n")
if name=="nft":
    if a[:4]==["delete","table","inet","cloudops_bootstrap"]:
        if os.getenv("TEST_GATE_RELEASE_FAIL")=="true": sys.exit(1)
        Path(os.environ["BOOTSTRAP_GATE"]).write_text("false")
    else: Path(os.environ["BOOTSTRAP_GATE"]).write_text("true")
elif name=="apt-get" and os.getenv("TEST_INSTALL_FAIL")=="true": sys.exit(1)
elif name=="systemctl" and "snap.amazon-ssm-agent.amazon-ssm-agent.service" in a and os.getenv("TEST_SSM_INACTIVE")=="true": sys.exit(1)
elif name=="snap" and a[:1]==["list"] and os.getenv("TEST_SSM_ABSENT")=="true": sys.exit(1)
elif name=="dpkg-query":
    if os.getenv("TEST_SSM_DEB")=="true": print("install ok installed")
    else: sys.exit(1)
elif name=="unzip":
    install=Path(a[a.index("-d")+1])/"aws/install"
    install.parent.mkdir(parents=True,exist_ok=True)
    install.write_text("#!/bin/bash\\nexit 0\\n")
    install.chmod(0o755)
'''
    bin_dir = Path(host["env"]["PATH"].split(":")[0])
    for name in ("apt-get", "systemctl", "nft", "snap", "dpkg-query", "dpkg", "unzip"):
        path = bin_dir / name
        path.write_text(generic)
        path.chmod(0o755)
    ctl = root / "opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl"
    ctl.write_text(generic)
    ctl.chmod(0o755)
    curl = bin_dir / "curl"
    curl.rename(bin_dir / "fixture-http")
    curl.write_text('''#!/usr/bin/env python3
import os, shutil, sys
from pathlib import Path
a=sys.argv[1:]
url=next((item for item in a if item.startswith("https://raw.githubusercontent.com/")),None)
if url:
    shutil.copyfile(Path(os.environ["BOOTSTRAP_SOURCE_ROOT"])/url.rsplit("/",1)[1], a[a.index("-o")+1])
elif any(item.startswith(("https://awscli.amazonaws.com/", "https://amazoncloudwatch-agent.s3.amazonaws.com/")) for item in a):
    Path(a[a.index("-o")+1]).write_bytes(b"fixture artifact")
else:
    os.execv(str(Path(sys.argv[0]).with_name("fixture-http")), ["fixture-http",*a])
''')
    curl.chmod(0o755)
    # Sandbox all host paths; use unmodified real deploy/verify scripts and mock only external services.
    script = rendered_bootstrap()
    import hashlib
    fixture_hash = hashlib.sha256(b"fixture artifact").hexdigest()
    for reviewed_hash in ("850ba65f1342a1f725f4868de3c4621ea729af31dac96e54b574cbb0ef309029",
                          "f25c81f42627ac481b51215e8e6f989208ab266f8b224ffd66a208061e790f1c"):
        script = script.replace(reviewed_hash, fixture_hash)
    for path in ("/var/log", "/var/lib/cloudops", "/etc/cloudops", "/etc/os-release",
                 "/etc/cloudwatch-agent-cloudops.json", "/usr/local/sbin", "/usr/local/aws-cli", "/usr/local/bin", "/var/lib/apt/lists", "/opt/aws/amazon-cloudwatch-agent", "/run/cloudops-bootstrap"):
        script = script.replace(path, str(root) + path)
    target = root / "bootstrap.sh"
    target.write_text(script)
    host["bootstrap"] = target
    host["root"] = root
    return host


@pytest.mark.parametrize("failure", [None, "TEST_CANDIDATE_READY", "TEST_PRODUCTION_READY", "TEST_GATE_RELEASE_FAIL", "TEST_INSTALL_FAIL", "TEST_SSM_INACTIVE"])
def test_rendered_bootstrap_installs_agents_and_gates_first_boot(bootstrap_host, failure):
    host = bootstrap_host
    if failure:
        host["env"][failure] = "503" if "READY" in failure else "true"
    result = subprocess.run(["bash", str(host["bootstrap"])], env=host["env"], capture_output=True, text=True, timeout=30)
    root = host["root"]
    logs = (root / "var/log/cloudops-bootstrap.log").read_text()
    assert host["env"]["TEST_SECRET"] not in logs
    assert "db-secret" not in logs
    marker = root / "var/lib/cloudops/bootstrap-complete.json"
    state = json.loads(Path(host["state"]).read_text())
    if failure:
        assert result.returncode != 0, logs
        assert not marker.exists()
        assert not any(item["running"] and item["name"] == "cloudops-app" for item in state["containers"].values())
        if failure not in ("TEST_INSTALL_FAIL", "TEST_SSM_INACTIVE"):
            assert (root / "gate.json").read_text() == "true"
    else:
        assert result.returncode == 0, logs
        assert json.loads(marker.read_text())["image"] == IMAGE
        assert marker.stat().st_mode & 0o777 == 0o600
        assert (root / "gate.json").read_text() == "false"
        runtime = root / "etc/cloudops/runtime.env"
        assert runtime.stat().st_mode & 0o777 == 0o600
        assert "DATABASE_URL=" not in runtime.read_text()
        tools = [json.loads(line) for line in (root / "tools.jsonl").read_text().splitlines()]
        install = next(call for call in tools if call[:2] == ["apt-get", "install"])
        for package in ("docker.io", "python3", "curl", "nftables", "snapd", "unzip"):
            assert package in install
        assert ["systemctl", "enable", "--now", "snap.amazon-ssm-agent.amazon-ssm-agent.service"] in tools
        assert ["systemctl", "is-active", "--quiet", "amazon-cloudwatch-agent"] in tools
