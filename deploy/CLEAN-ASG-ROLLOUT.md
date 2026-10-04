# Clean CloudOps ASG rollout

`ami-09b67ca726bea7328` is rejected for launch. It is retained only as an immutable source reference in launch-template v5 while `asg-cloudops-app` is idle at 0/0/0. Its user data is never read or copied, and no AMI is built from it. A running group using that image fails preflight. Initial rollback restores capacity 0/0/0 and never launches the old image.

## Reviewed image and source

Read-only AWS queries on 2026-10-04 resolved public parameter `/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64`, reported version 191, to **`ami-01e082ac2f79f3918`** in `eu-north-1`. `DescribeImages --owners amazon` verified owner **`137112412989`**, alias `amazon`, public/available HVM EBS x86_64, IMDSv2 support, name `al2023-ami-2023.12.20260930.0-kernel-6.18-x86_64`, and official image location. The AWS source image is `ami-0d53cc9bd365ad65b` in `us-west-2`. Provenance is pinned in `reviewed-al2023.json` and checked before approval.

The public service rejected a `:191` selector with `ParameterVersionNotFound`, so preflight reads the public parameter without a version suffix and verifies its reported version/value against the review. A moved pointer, deprecated image, different owner or wrong architecture requires a new review. It never silently changes the pinned AMI. [AWS public AMI parameter documentation](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/finding-an-ami-parameter-store.html).

Source `lt-028eb222c6fcfffc1` v5 uses `t3.micro`, profile ARN `arn:aws:iam::489502663059:instance-profile/CloudOpsEC2Role`, and group `sg-0f9613afd389c288d`. Its primary interface pins one subnet. The renderer preserves the ARN/group and replaces the interface with a private primary interface with **no SubnetId**. The ASG selects either configured private subnet. Explicit disk/snapshot overrides are rejected pending review; v5 has none.

Every new version uses `--source-version 5`, overriding ImageId, user data, tags, metadata and interface. `HttpTokens=required`, `HttpEndpoint=enabled`, and hop limit 1 are enforced. Neither v5 nor the default template version is edited. [AWS source-version behavior](https://docs.aws.amazon.com/cli/latest/reference/ec2/create-launch-template-version.html).

## Bootstrap and evidence

Bootstrap accepts only AL2023 x86_64, installs Docker, `awscli-2`, Python, SSM Agent, CloudWatch Agent and nftables, and starts/checks the services. These package names were resolved against the official AL2023 repository. Existing outbound connectivity/endpoints must support package downloads, GitHub, ECR, Secrets Manager, SSM and CloudWatch.

A private `inet cloudops_bootstrap` firewall table blocks external port 5000 for both IP families while allowing loopback checks. Existing containers, runtime.env or a same-name firewall table cause failure. Runtime secrets are fetched without printing values; signing configuration is root-owned mode 0600, DATABASE_URL is not inherited, and the stable key is not rotated. `/cloudops/app` must already exist.

Checksum-pinned scripts from the exact source commit verify candidate 5001 and production 5000, `/health`, `/ready`, the application route, Docker health, configured ECR digest and actual Docker image ID. Only successful first boot writes a root-owned mode-0600 release marker and removes its firewall gate. ASG target registration stays automatic. Failure removes the marker, keeps traffic blocked, and stops/disables restart for this exact release's containers. Unknown containers and volumes are retained.

Per-instance SSM verification requires the release marker, exact image/version, health/readiness, Docker health and that instance's healthy ALB target. The independent healthy canary cannot count toward ASG readiness. API authorization errors fail immediately; numeric version/capacity rollback remains enabled.

Before manual approval, `collect-cloudops-preflight.sh asg` calls `prepare-cloudops-asg.sh`. This performs only read-only discovery and local rendering. Restricted overrides remain in ignored `.deploy-work/`. Jenkins archives `reports/asg-launch-template-preview.json`, containing source v5, reviewed AMI/owner, ARN profile, security group, subnet-free interface, metadata options and user-data checksum/size/Bash validation. It never prints raw user data or secret values.

## Controlled commands after review

Use the approved Jenkins environment on merged `main`, with the existing `DevSecOpsToolsRole` identity and pipeline-populated commit/ECR variables. First run CI with `DEPLOY_TARGET=none`; Gitleaks, pytest, SonarQube/Quality Gate and Trivy must pass and the commit image must be published immutably. Review/apply the separate IAM policies if needed, including Jenkins public-parameter reads and the application's ECR, Secrets Manager, SSM, logging and region-restricted target-health permissions.

Read-only confirmation:

```bash
aws autoscaling describe-auto-scaling-groups --region eu-north-1 \
  --auto-scaling-group-names asg-cloudops-app \
  --query 'AutoScalingGroups[0].{Capacity:[MinSize,DesiredCapacity,MaxSize],LaunchTemplate:LaunchTemplate,Subnets:VPCZoneIdentifier,HealthChecks:HealthCheckType}'
aws ec2 describe-images --region eu-north-1 --owners amazon \
  --image-ids ami-01e082ac2f79f3918 \
  --query 'Images[0].{Image:ImageId,Owner:OwnerId,Name:Name,Architecture:Architecture,State:State}'
```

Select Jenkins `DEPLOY_TARGET=asg`, `ASG_AMI_REVIEWED=true`. Before its designated-approver input, Jenkins runs:

```bash
bash deploy/collect-cloudops-preflight.sh asg
```

Inspect the archived preview, then approve through the restricted Jenkins input. Only afterward does Jenkins run:

```bash
ASG_AMI_REVIEWED=true bash deploy/cloudops-asg-rollout.sh
```

That command creates a new numeric LT version from **5**, then performs the existing initial rollout to 1/2/2 and verifies both nodes. Later rollouts use a prior clean numeric version and Instance Refresh/automatic rollback. Do not scale the old v5 group separately. Observe deployment evidence, `/cloudops/app` logs, SSM and instance-specific target health. The existing ASG grace period is 300 seconds; review package/image startup timing against it before approval. Keep the healthy canary until a separate retirement review.

Tests simulate the complete rendered bootstrap and real deploy/verify code with mocked AWS/Docker/services, including failures and zero-capacity rollback. `python3 tests/integration/rehearse_al2023_gate.py` also exercises the exact firewall rules in disposable real AL2023 network namespaces, without AWS access or host port publication. This does not substitute for live EC2/systemd/RDS acceptance during the later controlled rollout. No instance, LT version, ASG capacity, secret, canary or RDS resource is changed while preparing this PR.
