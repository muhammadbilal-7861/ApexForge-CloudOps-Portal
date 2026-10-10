# 09 — Private inventory and least-privilege policies

[Master guide](../MASTER-GUIDE.md).

## Architecture and purpose

Public examples contain fake IDs and `example_only=true`; they cannot authorize AWS operations. A reviewed private inventory binds a release to your exact account, roles, region, app instance, subnets, SG, target group and secret names. It contains metadata, never passwords.

## Prerequisites

Labs01–08 provisioning complete, IDs saved in your private ledger, owner review. The app and monitoring profiles exist; database is available. This chapter renders policy files locally; applying them is an explicit operator action.

## Configuration steps

In your workstation shell retain the variables from prior chapters and add:

```bash
export VPC_ID APP_A APP_B APP_SG APP_INSTANCE_ID OBS_INSTANCE_ID TARGET_GROUP_ARN ECR_REPOSITORY
export AWS_ACCOUNT_ID AWS_REGION
export SOURCE_REPOSITORY='YOUR-GITHUB-OWNER/YOUR-FORK' # replace before continuing
python3 - <<'PY'
import json, os
from pathlib import Path
d=json.loads(Path('deploy/config.example.json').read_text())
e=os.environ
d.update(example_only=False, AWS_ACCOUNT_ID=e['AWS_ACCOUNT_ID'], AWS_REGION=e['AWS_REGION'],
 AWS_ROLE_NAME='ApexForgeToolsRole', APP_ROLE_NAME='ApexForgeAppRole',
 INSTANCE_PROFILE_NAME='ApexForgeAppRole',
 INSTANCE_PROFILE_ARN=f"arn:aws:iam::{e['AWS_ACCOUNT_ID']}:instance-profile/ApexForgeAppRole",
 VPC_ID=e['VPC_ID'], APP_SECURITY_GROUP_ID=e['APP_SG'],
 CANARY_INSTANCE_ID=e['APP_INSTANCE_ID'], OBSERVABILITY_INSTANCE_ID=e['OBS_INSTANCE_ID'],
 PRIVATE_SUBNET_IDS=[e['APP_A'],e['APP_B']], ECR_REPOSITORY=e['ECR_REPOSITORY'],
 TARGET_GROUP_ARN=e['TARGET_GROUP_ARN'], TARGET_GROUP_NAME='apexforge-app', ALB_NAME='apexforge-alb',
 RDS_INSTANCE_ID='apexforge-db', DB_SECRET_NAME='apexforge/prod/mariadb',
 SESSION_SECRET_NAME='apexforge/prod/flask-signing', LOG_GROUP_NAME='/apexforge360/app',
 SOURCE_REPOSITORY=e['SOURCE_REPOSITORY'], SESSION_COOKIE_SECURE='true',
 LEGACY_CONTAINER_NAME='legacy-cloudops', LEGACY_IMAGE='legacy-cloudops:reviewed',
 ALLOW_INITIAL_INSTALL='false')
p=Path('.deploy-work/environment.json');p.write_text(json.dumps(d,indent=2)+'\n');p.chmod(0o600)
PY
export CLOUDOPS_CONFIG_FILE="$PWD/.deploy-work/environment.json"
python3 deploy/cloudops_config.py --format check
python3 deploy/render-iam-policy.py --template deploy/iam/cloudops-ec2-instance-policy.json \
  --output .deploy-work/app-policy.json
python3 deploy/render-iam-policy.py --template deploy/iam/jenkins-cloudops-deploy-policy.json \
  --output .deploy-work/tools-policy.json
```

Inspect rendered JSON privately. `PRIVATE_SUBNET_IDS` are the **two application subnets**, not DB/public subnets. `CANARY_INSTANCE_ID` names the single permanent app EC2; “canary” is the candidate-first release strategy on that host. Profile name and ARN must match. For an existing legacy app, inventory the exact container name/image without environment dumps; never guess those fields. For a clean first deployment change `ALLOW_INITIAL_INSTALL` to `true` only after confirming an empty Docker inventory; revert it to false after installation.

Apply reviewed policies from the operator workstation:

```bash
aws iam put-role-policy --role-name ApexForgeAppRole --policy-name ApexForgeApplication \
  --policy-document file://.deploy-work/app-policy.json
aws iam put-role-policy --role-name ApexForgeToolsRole --policy-name ApexForgeDeployment \
  --policy-document file://.deploy-work/tools-policy.json
```

Tools permissions cover exact-instance SSM, read-only topology, ECR publication and DB-secret schema validation. The app role pulls only its repository, reads its two secrets, writes only its log streams and checks ALB target health. DescribeTargetHealth requires `Resource:"*"`, with region restriction. Retain AmazonSSMManagedInstanceCore. Add exact customer-managed KMS key permissions only if using those keys.

Copy inventory via private SSM editing into the app host `/etc/cloudops/environment.json` and into Jenkins' named volume `/var/jenkins_home/private/cloudops/environment.json` (not a host path assumed to be a bind mount). Both must be0600 and outside the repository. Then run Lab06 runtime preparation. Set Jenkins `CLOUDOPS_CONFIG_FILE` to its volume path and `CLOUDOPS_DEPLOY_APPROVERS` to exact designated user IDs, comma-separated.

## Expected output and validation

Validator reports private inventory accepted without invoking AWS. Rendered files contain no `${...}` placeholders or fake IDs. Public example fails unless explicitly validated offline with `allow_example=True`. Account/profile/TG mismatches fail. Ensure `.deploy-work` is ignored. Read back role policies using `get-role-policy` and compare privately.

## Troubleshooting and root causes

Unknown/missing field: old inventory or typo; use current schema. Example refused: build your actual ledger, do not flip the flag on fake IDs. AWS AccessDenied: inspect actual role/policy/KMS/endpoint/SCP; never broaden to administrator rights to get green.

## Security considerations

Metadata is sensitive even without passwords. Freeze inventory per approval; Jenkins does this before publishing. Never archive private inventory or raw secret responses. `SOURCE_REPOSITORY` must be your reviewed source because SSM downloads commit-pinned checksum-verified scripts from it.

## Cleanup

Remove private scratch policy copies after reviewed installation; retain an encrypted ledger. Delete inline policies only when retiring their owned role. Never delete runtime secrets as part of a code change.

## Interview questions with answers

**Why fail on unknown fields?** A typo or stale inventory must not silently alter the deployment contract.
**Why freeze inventory before approval?** Changing destination metadata after approval could deploy to an unreviewed account or instance.
