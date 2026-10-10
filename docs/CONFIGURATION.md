# Configuration

## Local application

Copy `.env.example` to ignored `.env`. The published passwords/session key are **development-only**. Compose sets the app's database host to `mariadb`; host Python uses `DB_HOST` from `.env` only if exported. `FLASK_ENV=development` enables demo seeding. `USE_AWS_SECRETS=false` avoids all AWS dependencies; S3 uploads remain unavailable unless independently configured.

`SECRET_KEY` must be stable and private in production. `SESSION_COOKIE_SECURE=true` requires an HTTPS browser endpoint. `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` configure local DB access. Production uses `AWS_SECRET_NAME` with Secrets Manager instead. Never put secret values in deployment inventory.

## Private AWS inventory

Copy `deploy/config.example.json` to ignored `deploy/environment.json`, or provide an absolute `CLOUDOPS_CONFIG_FILE` inside the Jenkins named volume. Every example ID is fake and `example_only=true` prevents authorization. Populate every field from your reviewed resources, review AMI provenance, then explicitly set `example_only=false`. No loader defaults to test fixtures or the original account. Validate without AWS calls:

```bash
python3 deploy/cloudops_config.py --file deploy/environment.json
```

The accompanying [JSON schema](../deploy/config.schema.json) describes the document; the runtime validator also enforces cross-field identities, exact two-subnet inventory, Canonical owner and reviewed security tags. Unknown/missing fields fail. The file contains sensitive metadata, not credentials. Keep it outside Git/Docker build contexts and restrict its access.

| Fields | Meaning |
| --- | --- |
| AWS_ACCOUNT_ID, AWS_REGION, AWS_ROLE_NAME | Exact independent account, region and Jenkins instance role |
| APP_ROLE_NAME, INSTANCE_PROFILE_NAME, INSTANCE_PROFILE_ARN | Approved application role/profile; matching account and profile name required; profile and role names may differ |
| ECR_REPOSITORY | Existing IMMUTABLE registry repository; URI derived from account/region |
| VPC_ID, PRIVATE_SUBNET_IDS, APP_SECURITY_GROUP_ID | Reviewed VPC, exactly two distinct private subnets and app SG |
| LAUNCH_TEMPLATE_ID, ASG_NAME | Existing source version 5 and ELB-backed ASG |
| TARGET_GROUP_ARN, TARGET_GROUP_NAME, ALB_NAME | Exact approved target identity and listener association |
| CANARY_INSTANCE_ID, OBSERVABILITY_INSTANCE_ID | Explicit inventory for canary and private scraper SG checks |
| RDS_INSTANCE_ID | Existing healthy DB; never created or changed by this pipeline |
| DB_SECRET_NAME, SESSION_SECRET_NAME | Existing secret names; stable signing key and structured DB secret |
| LOG_GROUP_NAME | Pre-created CloudWatch group; no automatic group creation |
| SOURCE_REPOSITORY | Reviewed GitHub owner/repository for checksum-pinned source downloads |
| SESSION_COOKIE_SECURE | `true` for HTTPS; `false` only for explicitly reviewed HTTP labs |
| LEGACY_CONTAINER_NAME, LEGACY_IMAGE | Exact pre-inventoried legacy container/image, never a broad allowlist |
| reviewed_ami | Pinned Ubuntu 24.04 AMD64 gp3 provenance and approved ASG tags |

AMI review must capture official Canonical owner `099720109477`, image ID/name/location/creation date/root snapshot, public parameter name/version, profile/SG/subnets and source version 5's rejected AMI. Fetch parameter/image through read-only AWS tooling, independently review, and pin the result. Deployment never follows a moving pointer silently; pointer drift fails review. Tests use fabricated images and snapshots. Binary/bootstrap pins are maintained separately in the template and reproducible build document.

Use [Canonical's official image discovery procedure](https://ubuntu.com/aws/docs/aws-how-to/instances/find-ubuntu-images/) when preparing your region's private inventory; discovering a current image is separate from approving and pinning it for deployment.

## Jenkins

Linux agent with Docker CLI, Bash, Git and Python 3.12. Jenkins container is named `jenkins`, workspace is a named volume and `/var/run/docker.sock` points to the host daemon. This grants significant host control: isolate agents, restrict job edits and never execute untrusted PRs with privileged credentials/socket access.

`--volumes-from jenkins` exposes the Jenkins volume, not merely the checkout directory; root tool containers may also read controller secrets and inherited bind mounts. This topology is appropriate only for trusted laboratory code on isolated agents. Do not run fork PRs on this agent or give public CI a deployment role. Production separation of controller, untrusted CI and protected deployment agents requires an independently reviewed Jenkins/volume/IAM design; an opt-in parameter is not a sandbox for malicious code.

Required plugins: Pipeline/Declarative, Git, Credentials Binding, SonarQube Scanner for Jenkins, JUnit, Pipeline Utility Steps (`readJSON`). Configure Sonar server **sonarqube**, token credential ID **sonarqube-token**, existing project key **ApexForge-CloudOps-Portal**, and webhook `<jenkins-url>/sonarqube-webhook/`; protect the webhook with an independently configured secret where supported. No tokens belong in Git.

Set private `CLOUDOPS_CONFIG_FILE` and `CLOUDOPS_DEPLOY_APPROVERS` (designated Jenkins IDs) administratively. Scanners use `--volumes-from jenkins`; Sonar runs with host networking and Jenkins UID/GID, preserving `.scannerwork/report-task.txt`. Trivy uses a separate named cache volume.

AWS CLI containers use host networking and the EC2 instance role; no static AWS keys or mounted credential files. Jenkins' role must match AWS_ROLE_NAME exactly. AWS_OPERATIONS defaults false, DEPLOY_TARGET defaults none. Publication/preflight needs restricted approval; deployment has a second digest-bound approval. ASG additionally requires ASG_AMI_REVIEWED=true and a reviewed archived candidate preview.

IAM JSON files are **synthetic templates**, not policies to apply unchanged. Render them against validated private inventory using `deploy/render-iam-policy.py`, review the result and attach it administratively to your own roles. The script never applies policies. Preserve scoped PassRole, resource/region constraints and required DescribeTargetHealth wildcard. ECR publication permissions must be reviewed for the exact repository.

```bash
mkdir -p .deploy-work
chmod 700 .deploy-work
python3 deploy/render-iam-policy.py --template deploy/iam/jenkins-cloudops-deploy-policy.json --output .deploy-work/jenkins-policy.json
python3 deploy/render-iam-policy.py --template deploy/iam/cloudops-ec2-instance-policy.json --output .deploy-work/app-policy.json
```

Application roles also need the reviewed SSM/CloudWatch agent prerequisites and any customer-managed KMS key permissions appropriate to your secrets; these example policies are not a substitute for reviewing all live attachments. Policies are rendered only; no IAM update is performed. The current deployment contract inventories source launch-template version 5 and `t3.micro`; it does not support arbitrary instance families or source versions without a reviewed code change.
