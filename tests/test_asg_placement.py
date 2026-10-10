"""Real helper orchestration with fake AWS: no instance or capacity changes."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from tests.test_clean_asg import COMMIT, IMAGE, read_fixture
from tests.test_deployment_foundation import ROOT, load_helper
from tests.test_ubuntu_asg import reviewed_overrides


def candidate(settings=None):
    return {"LaunchTemplateId": "lt-00000000000000001", "VersionNumber": 6,
            "LaunchTemplateData": reviewed_overrides() if settings is None else settings}


def test_allowlist_does_not_inherit_v5_placement_or_unreviewed_fields():
    renderer = load_helper("render-launch-template-data")
    source = read_fixture("cloudops-launch-template-v5.json")["LaunchTemplateData"]
    assert source["Placement"] == {"AvailabilityZoneId": "use1-az1"}
    source.update(KeyName="unexpected", Monitoring={"Enabled": True},
                  MetadataOptions={"InstanceMetadataTags": "enabled"},
                  TagSpecifications=[{"ResourceType": "volume", "Tags": [{"Key": "Unknown", "Value": "tag"}]}])
    original = copy.deepcopy(source)
    settings = reviewed_overrides()
    actual = renderer.render(source, settings["UserData"], COMMIT[:12])
    assert actual == settings
    assert source == original
    assert actual["InstanceType"] == "t3.micro"
    assert "Placement" not in actual
    assert "SubnetId" not in actual["NetworkInterfaces"][0]


@pytest.mark.parametrize("field,value", [
    ("Placement", {"AvailabilityZone": "us-east-1a"}),
    ("Placement", {"AvailabilityZoneId": "use1-az1"}),
    ("SubnetId", "subnet-00000000000000001"),
    ("KeyName", "unreviewed"),
    ("InstanceType", "t3.large"),
    ("NetworkInterfaces", [{"DeviceIndex": 0, "SubnetId": "subnet-00000000000000001"}]),
])
def test_readback_and_preview_reject_restricted_or_unreviewed_settings(field, value):
    settings = reviewed_overrides()
    bad = copy.deepcopy(settings)
    bad[field] = value
    validator = load_helper("validate-asg-candidate")
    document = candidate(bad)
    with pytest.raises(ValueError):
        validator.validate(document, settings, "6", IMAGE, COMMIT)
    preview = load_helper("preview-cloudops-asg")
    source = read_fixture("cloudops-launch-template-v5.json")
    ami = read_fixture("aws-reviewed-ubuntu24.json")
    parameter = read_fixture("aws-reviewed-ubuntu24-parameter.json")
    with pytest.raises(ValueError):
        preview.preview(source, ami, parameter, bad)


@pytest.mark.parametrize("change", ["version", "image", "commit", "configuration"])
def test_approved_candidate_cannot_change_after_manual_input(change):
    validator = load_helper("validate-asg-candidate")
    settings = reviewed_overrides()
    document = candidate(settings)
    approved = validator.validate(document, settings, "6", IMAGE, COMMIT)
    modified = copy.deepcopy(approved)
    key = {"version": "candidateVersion", "image": "applicationImage", "commit": "commit",
           "configuration": "launchSettingsSha256"}[change]
    modified[key] = "different"
    with pytest.raises(ValueError, match="preapproval record"):
        validator.validate(document, settings, "6", IMAGE, COMMIT, modified)


def test_readback_accepts_only_disabled_aws_metadata_defaults():
    validator = load_helper("validate-asg-candidate")
    settings = reviewed_overrides()
    document = candidate(copy.deepcopy(settings))
    document["LaunchTemplateData"]["MetadataOptions"].update(HttpProtocolIpv6="disabled", InstanceMetadataTags="disabled")
    approved = validator.validate(document, settings, "6", IMAGE, COMMIT)
    assert approved["availabilityZoneIndependent"] is True
    document["LaunchTemplateData"]["MetadataOptions"]["InstanceMetadataTags"] = "enabled"
    with pytest.raises(ValueError):
        validator.validate(document, settings, "6", IMAGE, COMMIT)


@pytest.mark.parametrize("failure", ["none", "create-denied", "read-denied", "inherited-placement",
                                     "subnet-00000000000000001", "subnet-00000000000000002"])
def test_preapproval_creates_reads_back_and_probes_exact_clean_candidate(tmp_path, failure):
    workspace = tmp_path / "workspace"
    shutil.copytree(ROOT / "deploy", workspace / "deploy")
    (workspace / "deploy/assert-deploy-context.sh").write_text("#!/bin/bash\nexit 0\n")
    work = workspace / ".deploy-work"
    work.mkdir()
    for name, fixture in (("launch-template-source.json", "cloudops-launch-template-v5.json"),
                          ("reviewed-ami.json", "aws-reviewed-ubuntu24.json"),
                          ("reviewed-ami-parameter.json", "aws-reviewed-ubuntu24-parameter.json")):
        (work / name).write_text(json.dumps(read_fixture(fixture)))
    review = json.loads(Path(os.environ["CLOUDOPS_CONFIG_FILE"]).read_text())["reviewed_ami"]
    (work / "asg.json").write_text(json.dumps({"AutoScalingGroups": [{
        "AutoScalingGroupName": "asg-cloudops-app", "VPCZoneIdentifier": ",".join(review["private_subnets"]),
        "Tags": [{"Key": k, "Value": v, "PropagateAtLaunch": True} for k, v in review["asg_propagated_tags"].items()],
    }]}))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    docker = bin_dir / "docker"
    docker.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
a=sys.argv[1:]
if "test-python" in a:
    os.execv(sys.executable,[sys.executable,*a[a.index("test-python")+2:]])
a=a[a.index("test-aws")+1:]
with open(os.environ["CALLS"],"a") as log: log.write(json.dumps(a)+"\\n")
failure=os.environ["FAILURE"]
def denied():
    print("An error occurred (UnauthorizedOperation) when calling the Test operation: private",file=sys.stderr)
    sys.exit(255)
if a[:2]==["ec2","create-launch-template-version"]:
    assert "--source-version" not in a
    if failure=="create-denied": denied()
    if "--dry-run" in a:
        print("An error occurred (DryRunOperation) when calling the Test operation: private",file=sys.stderr)
        sys.exit(255)
    print("6")
elif a[:2]==["ec2","describe-launch-template-versions"]:
    if failure=="read-denied": denied()
    assert a[a.index("--versions")+1]=="6"
    data=json.loads((Path(os.environ["WORKSPACE"])/".deploy-work/launch-template-overrides.json").read_text())
    if failure=="inherited-placement": data["Placement"]={"AvailabilityZoneId":"use1-az1"}
    print(json.dumps({"LaunchTemplateId":"lt-00000000000000001","VersionNumber":6,"LaunchTemplateData":data}))
elif a[:2]==["ec2","run-instances"]:
    assert "--dry-run" in a
    request=json.loads(Path(a[a.index("--cli-input-json")+1].removeprefix("file://")).read_text())
    assert request["DryRun"] is True
    assert request["LaunchTemplate"]["Version"]=="6"
    assert "ImageId" not in request
    if request["NetworkInterfaces"][0]["SubnetId"]==failure: denied()
    print("An error occurred (DryRunOperation) when calling the Test operation: private",file=sys.stderr)
    sys.exit(255)
else: raise SystemExit("Unexpected AWS mutation")
''')
    docker.chmod(0o755)
    log = tmp_path / "calls.jsonl"
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", WORKSPACE=str(workspace),
               AWS_CLI_IMAGE="test-aws", PYTHON_IMAGE="test-python", AWS_REGION="us-east-1",
               ECR_DEPLOY_IMAGE=IMAGE, GIT_COMMIT_FULL=COMMIT, GIT_COMMIT_SHORT=COMMIT[:12],
               CALLS=str(log), FAILURE=failure)
    result = subprocess.run(["bash", "deploy/prepare-cloudops-asg.sh"], cwd=workspace,
                            env=env, capture_output=True, text=True, timeout=15)
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert not any(call[0] == "autoscaling" for call in calls)
    assert not any("--source-version" in call for call in calls)
    probes = [call for call in calls if call[:2] == ["ec2", "run-instances"]]
    if failure == "none":
        assert result.returncode == 0, result.stderr
        assert len(probes) == 2
        report = json.loads((workspace / "reports/asg-launch-template-preview.json").read_text())
        assert report["candidateVersion"] == "6"
        assert report["sourceVersionInherited"] is False
        assert report["availabilityZoneIndependent"] is True
        assert report["capacityChanged"] is False
        approved = json.loads((workspace / "reports/asg-launch-candidate.json").read_text())
        assert approved["applicationImage"] == IMAGE
        assert approved["commit"] == COMMIT
        # Postapproval validation reuses the record/version without creating one.
        before = len(calls)
        result = subprocess.run(["bash", "deploy/validate-launch-permissions.sh", "6"], cwd=workspace,
                                env=env, capture_output=True, text=True, timeout=15)
        assert result.returncode == 0, result.stderr
        later = [json.loads(line) for line in log.read_text().splitlines()][before:]
        assert not any(call[:2] == ["ec2", "create-launch-template-version"] for call in later)
    else:
        assert result.returncode != 0
        assert not (workspace / "reports/asg-launch-permissions.json").exists()
        if failure in ("create-denied", "read-denied", "inherited-placement"):
            assert probes == []


def test_pipeline_binds_approval_to_candidate_version_and_digest():
    pipeline = (ROOT / "Jenkinsfile").read_text()
    assert "env.ASG_VALIDATED_VERSION = sh(" in pipeline
    assert "Candidate version ${env.ASG_VALIDATED_VERSION}, image ${env.ECR_DEPLOY_IMAGE}" in pipeline
    assert pipeline.index("archiveArtifacts artifacts: 'reports/asg-launch-candidate.json'") < pipeline.index("stage('Manual deployment approval')")
    rollout = (ROOT / "deploy/cloudops-asg-rollout.sh").read_text()
    assert "create-launch-template-version" not in rollout
    assert 'new_version="$ASG_VALIDATED_VERSION"' in rollout
