"""Public defaults, independent configuration and application privacy regressions."""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys

import pytest

from cloudops_config import environment, load, validate
from tests.test_deployment_foundation import ROOT, load_helper


def inventory():
    return json.loads((ROOT / "tests/fixtures/deployment-config.json").read_text())


def test_public_example_matches_schema_but_cannot_authorize_operations():
    example = json.loads((ROOT / "deploy/config.example.json").read_text())
    validated = validate(example, allow_example=True)
    assert validated is example
    with pytest.raises(ValueError, match="example inventory"):
        validate(example)


@pytest.mark.parametrize("mutation", [
    {"AWS_ACCOUNT_ID": "bad"}, {"AWS_REGION": "region;exit 0"}, {"AWS_ROLE_NAME": "*"},
    {"INSTANCE_PROFILE_NAME": "another-profile"}, {"PRIVATE_SUBNET_IDS": ["subnet-00000000000000001"]},
    {"TARGET_GROUP_ARN": "arn:aws:elasticloadbalancing:us-east-1:999999999999:targetgroup/tg-cloudops-app/0000000000000001"},
    {"SOURCE_REPOSITORY": "owner/repo;curl attacker"}, {"SECRET_KEY": "never-in-inventory"},
])
def test_inventory_rejects_invalid_or_cross_account_settings(mutation):
    document = inventory()
    document.update(mutation)
    with pytest.raises(ValueError):
        validate(document)


def test_missing_private_inventory_fails_closed(tmp_path):
    absent = tmp_path / "absent.json"
    with pytest.raises(ValueError, match="missing or invalid"):
        load(absent)


def test_independent_account_region_repository_and_iam_are_parameterized():
    document = copy.deepcopy(inventory())
    document.update(AWS_ACCOUNT_ID="234567890123", AWS_REGION="us-west-2",
                    ECR_REPOSITORY="independent-cloudops", AWS_ROLE_NAME="IndependentTools",
                    APP_ROLE_NAME="IndependentApp", INSTANCE_PROFILE_NAME="IndependentApp",
                    INSTANCE_PROFILE_ARN="arn:aws:iam::234567890123:instance-profile/IndependentApp",
                    TARGET_GROUP_ARN="arn:aws:elasticloadbalancing:us-west-2:234567890123:targetgroup/tg-cloudops-app/0000000000000001")
    document["reviewed_ami"].update(region=document["AWS_REGION"], instance_profile_arn=document["INSTANCE_PROFILE_ARN"])
    validate(document)
    values = environment(document)
    assert values["ECR_URI"] == "234567890123.dkr.ecr.us-west-2.amazonaws.com/independent-cloudops"
    assert values["AWS_EXPECTED_ROLE"] == "IndependentTools"
    helper = load_helper("render-iam-policy")
    template = json.loads((ROOT / "deploy/iam/jenkins-cloudops-deploy-policy.json").read_text())
    policy = helper.render(template, document)
    statements = {item["Sid"]: item for item in policy["Statement"]}
    assert statements["PassOnlyCloudOpsAppRoleToEc2"]["Resource"] == "arn:aws:iam::234567890123:role/IndependentApp"
    encoded = json.dumps(policy)
    assert "${" not in encoded
    assert "123456789012" not in encoded


def test_independent_bootstrap_renderer_uses_supplied_inventory(tmp_path):
    document = inventory()
    document["ECR_REPOSITORY"] = "another-reviewed-repository"
    document["SOURCE_REPOSITORY"] = "independent-owner/cloudops-lab"
    source = tmp_path / "inventory.json"
    source.write_text(json.dumps(document))
    output = tmp_path / "bootstrap.sh"
    image = environment(document)["ECR_URI"] + "@sha256:" + "a" * 64
    commit = "b" * 40
    result = subprocess.run([sys.executable, str(ROOT / "deploy/render-cloudops-user-data.py"),
                             "--template", str(ROOT / "deploy/cloudops-user-data.sh.tmpl"),
                             "--deploy-script", str(ROOT / "deploy/cloudops-deploy.sh"),
                             "--verify-script", str(ROOT / "deploy/cloudops-verify.sh"),
                             "--image", image, "--version", commit[:12], "--commit", commit,
                             "--output", str(output)],
                            env=dict(os.environ, CLOUDOPS_CONFIG_FILE=str(source)),
                            capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
    script = output.read_text()
    assert f'IMAGE_URI="{image}"' in script
    assert "independent-owner/cloudops-lab" in script
    assert "@@" not in script
    assert len(output.read_bytes()) <= 16384


def test_ami_owner_and_security_tags_cannot_be_relaxed():
    document = inventory()
    document["reviewed_ami"]["owner_id"] = "123456789012"
    with pytest.raises(ValueError, match="owner_id"):
        validate(document)


def test_public_jenkins_default_needs_approval_before_any_aws_write():
    pipeline = (ROOT / "Jenkinsfile").read_text()
    assert "name: 'AWS_OPERATIONS', defaultValue: false" in pipeline
    approval = pipeline.index("stage('Validate and approve AWS operations')")
    publication = pipeline.index("stage('Push image to Amazon ECR')")
    preflight = pipeline.index("stage('Authorize opt-in deployment')")
    assert approval < publication
    assert publication < preflight
    assert "params.AWS_OPERATIONS && env.SCM_BRANCH == 'main'" in pipeline
    assert "submitterParameter: 'AWS_APPROVED_BY'" in pipeline
    assert "DEPLOY_TARGET requires explicit AWS_OPERATIONS=true" in pipeline


def test_production_requires_a_stable_signing_secret(monkeypatch):
    from app import create_app
    monkeypatch.delenv("SECRET_KEY", raising=False)
    settings = {"TESTING": False, "FLASK_ENV": "production"}
    with pytest.raises(RuntimeError, match="stable SECRET_KEY"):
        create_app(settings)


def test_logs_do_not_include_arbitrary_request_paths(client, caplog):
    with caplog.at_level("INFO"):
        response = client.get("/synthetic-private-token-in-path",
                              headers={"X-Request-ID": "synthetic-private-header"})
    assert response.status_code == 404
    assert "synthetic-private-token-in-path" not in caplog.text
    assert "synthetic-private-header" not in caplog.text
    assert "endpoint=unknown" in caplog.text


def test_invalid_secret_fields_are_not_logged(monkeypatch, caplog):
    import boto3
    from app.services.secrets import database_config
    class SecretClient:
        def get_secret_value(self, **kwargs):
            return {"SecretString": json.dumps({"host": "fixture", "username": "fixture",
                                               "password": "fixture", "port": "synthetic-private-field"})}
    monkeypatch.setenv("USE_AWS_SECRETS", "true")
    monkeypatch.setenv("AWS_SECRET_NAME", "fixture")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: SecretClient())
    with pytest.raises(RuntimeError, match="Unable to load"):
        database_config()
    assert "synthetic-private-field" not in caplog.text
    assert "details withheld" in caplog.text


def test_demo_seed_is_development_only_and_does_not_duplicate(app):
    runner = app.test_cli_runner()
    app.config["FLASK_ENV"] = "production"
    result = runner.invoke(args=["seed-demo"])
    assert result.exit_code != 0
    app.config["FLASK_ENV"] = "development"
    result = runner.invoke(args=["seed-demo"])
    assert result.exit_code == 0
    second = runner.invoke(args=["seed-demo"])
    assert second.exit_code == 0
    from app.models import User, Record
    with app.app_context():
        assert User.query.filter_by(username="local-demo").count() == 1
        assert Record.query.count() == 1


def test_sql_engine_logs_hide_parameters():
    from app.config import Config
    assert Config.SQLALCHEMY_ENGINE_OPTIONS["hide_parameters"] is True


def test_database_configuration_failure_retains_log_redaction(monkeypatch, caplog):
    import app as app_module
    def unavailable():
        raise RuntimeError("synthetic configuration failure")
    monkeypatch.setattr(app_module, "database_uri", unavailable)
    settings = {"TESTING": True, "SECRET_KEY": "fixture", "WTF_CSRF_ENABLED": False}
    application = app_module.create_app(settings)
    client = application.test_client()
    assert application.config["SQLALCHEMY_ENGINE_OPTIONS"]["hide_parameters"] is True
    assert client.get("/ready").status_code == 503
    client.post("/register", data={"username": "synthetic-private-username",
                                   "email": "fixture@example.invalid", "password": "fixture-password"})
    assert "synthetic-private-username" not in caplog.text


def test_upload_names_are_sanitized_and_keys_remain_user_scoped(monkeypatch):
    import boto3
    from app.services.s3 import upload_file
    class Upload:
        filename = "../../synthetic name.pdf"
    class Storage:
        def upload_fileobj(self, file, bucket, key):
            assert bucket == "synthetic-private-bucket"
            assert key.startswith("uploads/7/")
    monkeypatch.setenv("S3_BUCKET_NAME", "synthetic-private-bucket")
    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: Storage())
    key = upload_file(Upload(), 7)
    assert ".." not in key
    assert "synthetic_name.pdf" in key


def test_csrf_rejects_missing_token_when_enabled(app, client):
    app.config["WTF_CSRF_ENABLED"] = True
    response = client.post("/login", data={"username": "fixture", "password": "fixture"})
    assert response.status_code == 400
    assert response.headers["X-Request-ID"]


def test_browser_forms_submit_valid_csrf_tokens(app, client):
    app.config["WTF_CSRF_ENABLED"] = True
    def token(path):
        response = client.get(path)
        assert response.status_code == 200
        matches = re.findall(r'<input[^>]*name="csrf_token"[^>]*value="([^"]+)"', response.get_data(as_text=True))
        assert matches, "Browser forms must submit a hidden CSRF token"
        return matches[0]
    registration_token = token("/register")
    response = client.post("/register", data={"username": "browser-demo", "email": "browser@example.invalid",
                                              "password": "development-only-password", "csrf_token": registration_token})
    assert response.status_code == 302
    login_token = token("/login")
    response = client.post("/login", data={"username": "browser-demo", "password": "development-only-password",
                                           "csrf_token": login_token})
    assert response.status_code == 302
    record_token = token("/records/add")
    response = client.post("/records/add", data={"title": "Browser regression", "description": "synthetic",
                                                 "csrf_token": record_token})
    assert response.status_code == 302
    delete_token = token("/records")
    response = client.post("/records/1/delete", data={"csrf_token": delete_token})
    assert response.status_code == 302
    upload_token = token("/upload")
    assert upload_token
