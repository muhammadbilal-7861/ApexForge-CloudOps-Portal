> Historical engineering notes: resource identifiers below have been replaced with synthetic examples. They are not current deployment instructions or verified live resources. Use [the reusable configuration guide](../docs/CONFIGURATION.md) and reviewed private inventory.

# Clean Ubuntu 24.04 CloudOps ASG rollout

## Reviewed image and launch configuration

Read-only AWS discovery on 2026-10-04 resolved Canonical's public parameter `/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id` (reported version 77) to **`ami-00000000000000001`** in `us-east-1`. `DescribeImages --owners 099720109477` confirmed Canonical ownership, public/available x86_64 HVM EBS, IMDSv2 support and name `ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-20260923`. The gp3 root disk uses snapshot `snap-00000000000000001`. AWS reports alias `amazon` and a `noble` public-parameter alias; the Canonical account ID is authoritative. Both aliases are pinned in `reviewed-ubuntu24.json`. [Canonical image discovery](https://ubuntu.com/aws/docs/aws-how-to/instances/find-ubuntu-images/).

Deployment uses the literal reviewed ImageId, never `resolve:ssm` or the moving parameter as a launch input. Preflight checks the current public parameter against the reviewed ID/version and aborts on drift. A replacement requires a new provenance review, IAM image-resource update, fixtures and bootstrap tests. Deprecated, private, wrong-owner, non-gp3 or wrong-architecture images fail closed.

Contaminated Ubuntu AMI `ami-00000000000000002` is rejected for launch. It is allowed only as the inventoried LT v5 source reference while the ASG is idle at 0/0/0. Its user data and snapshots are never copied; no AMI is built from it. Version 5 also pins `Placement.AvailabilityZoneId=use1-az1`. New numeric versions **omit `--source-version` entirely** and use a complete validated allowlist: reviewed AMI, explicit `t3.micro`, approved profile ARN, one private subnet-free interface, rendered user data, reviewed instance tags and IMDSv2 settings. Placement, Availability Zone, SubnetId, inherited disk mappings and other unreviewed fields are forbidden. Version 5 is inventory evidence only. [AWS SourceVersion semantics](https://docs.aws.amazon.com/AWSEC2/latest/APIReference/API_CreateLaunchTemplateVersion.html). V5 and the default version stay unchanged. Preserve `t3.micro`, profile ARN `arn:aws:iam::123456789012:instance-profile/ExampleAppRole`, and group `sg-00000000000000001`. The new LT has no subnet pin or public IP. ASG subnets `subnet-00000000000000001` and `subnet-00000000000000002` remain unchanged. IMDS requires tokens, an enabled endpoint and hop limit 1.

## Initial single-instance capacity policy

The reviewed regional quota is 8 vCPUs: Jenkins, observability and the healthy canary consume 2 each. Initial capacity is **Min=1, Desired=1, Max=1**, adding one 2-vCPU `t3.micro`. Preview and deployment evidence identify the one expected ASG target and explicitly retain the canary. This is an initial deployment policy; later refreshes preserve the existing group's capacity rather than silently scaling it down.

Only the new ASG instance counts toward success. It must be InService and ALB healthy, SSM Online, and pass the first-boot completion marker, exact digest/runtime image, Docker health, `/health`, `/ready` and application-route checks. No canary retirement action is included. Retirement requires a separate operator review after all checks pass; it must never be used to make a failed bootstrap appear successful. Future replacement/refresh capacity must be reviewed against the same quota, or scheduled separately with an approved downtime/retirement plan.

Initial failure drains only the ASG to 0/0/0 while retaining its reviewed clean template; an unchanged zero-capacity configuration requires no update. The canary is untouched. EC2 DryRun proves authorization, not vCPU availability or bootstrap readiness.

## Ubuntu first boot

Only Ubuntu Server 24.04 AMD64 is accepted. Ubuntu's signed apt archive installs `docker.io`, Python, curl, CA certificates, nftables, unzip and snapd. Docker is enabled and checked. Reviewed AWS binary artifacts are downloaded through HTTPS at fixed-version URLs and verified before installation:

| Artifact | Version | SHA256 |
| --- | --- | --- |
| AWS CLI ZIP | 2.37.5 | `850ba65f1342a1f725f4868de3c4621ea729af31dac96e54b574cbb0ef309029` |
| Ubuntu CloudWatch DEB | 1.300073.2b1889-1 | `f25c81f42627ac481b51215e8e6f989208ab266f8b224ffd66a208061e790f1c` |

The review verified upstream signatures against fingerprints `FB5DB77FD5C118B80511ADA8A6310ACC4672475C` (AWS CLI) and `937616F3450B7D806CBD9725D58167303B789C72` (CloudWatch). To update, verify the new version's signature against AWS's documented fingerprint in an isolated keyring, update URLs/hashes and rerun tests/rehearsal. Never adopt an unverified checksum merely to pass bootstrap. [AWS CLI installation](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html), [CloudWatch verification](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/verify-CloudWatch-Agent-Package-Signature.html).

Official Ubuntu EC2 AMIs normally include Snap SSM. Bootstrap waits for snap seeding, installs `amazon-ssm-agent --classic --channel=stable` if missing, enables it and checks **`snap.amazon-ssm-agent.amazon-ssm-agent.service`**. A competing deb agent fails validation. Jenkins requires SSM Online before submitting per-instance verification; authorization errors abort immediately. [AWS Ubuntu SSM instructions](https://docs.aws.amazon.com/systems-manager/latest/userguide/agent-install-ubuntu-64-snap.html).

CloudWatch is configured and checked immediately after its installation, before Snap/SSM setup, runtime secrets or application startup. `/var/log/cloudops-bootstrap.log` is private (0600). Controlled stage/status/exit/line events also reach the EC2 console, without commands or environment dumps. Logs use a one-second flush interval. On failure, a bounded IMDSv2/instance-role CLI fallback writes only a structured status to `/cloudops/app`, stream `<instance-id>/bootstrap-status`, even if the agent failed. Existing scoped CreateLogStream/PutLogEvents permissions suffice; no group or IAM permissions are added. Explicit nonzero exits now run the same cleanup/diagnostic path as command errors.

Failures before CLI installation or with unavailable network/IAM cannot upload to CloudWatch; use the safe EC2 console status and private local log. Upload failures retain the original bootstrap exit code and cannot open the network gate or create a success marker. Do not publish raw user data, runtime files, secret responses, arbitrary journals or Docker environment dumps. [CloudWatch log flush configuration](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/CloudWatch-Agent-Configuration-File-Details.html), [PutLogEvents](https://docs.aws.amazon.com/AmazonCloudWatchLogs/latest/APIReference/API_PutLogEvents.html). See [the incident review](ASG-BOOTSTRAP-INVESTIGATION.md) for the prior failure evidence and remaining uncertainty.

Both subnets need outbound access/endpoints for Ubuntu archives, Snap Store, awscli.amazonaws.com, the CloudWatch download bucket, GitHub, ECR, Secrets Manager, SSM and CloudWatch. No routes/endpoints are changed here. `/cloudops/app` must already exist. Review startup timing against the current 300-second ASG grace period before approval.

The nftables gate blocks external IPv4/IPv6 port 5000 while permitting loopback checks. Existing containers, inherited runtime.env or an existing gate table fail closed. Secrets are fetched through the EC2 role without printing values. Runtime settings are root-owned mode 0600, use the stable signing secret and never inherit DATABASE_URL. Secrets are not rotated; CloudWatch logging is retained.

Checksum-pinned deploy/verify scripts from the exact release commit check candidate 5001, production 5000, `/health`, `/ready`, the protected route, Docker health and exact ECR digest/runtime image ID. Only success writes a private release marker and opens port 5000. Failure keeps the gate closed, removes the marker and stops/disables restart only for this release's containers. Unknown containers and volumes are retained. Each ASG instance must pass SSM and its own ALB target-health checks; the independent healthy canary cannot count toward readiness.

## Jenkins IAM policy: operator action after review

The repository policy adds `ec2:RunInstances` for the pinned AMI, exact LT, both subnets and SG, restricted by region/LT. Generated instances require `t3.micro`, IMDSv2 and `Role=app`; generated volumes/interfaces remain region/LT restricted. `ec2:CreateTags` is limited to instance/volume tagging during RunInstances and approved keys: Role, Monitoring, Version, existing ASG Environment/Name/Project, and the AWS-managed autoscaling group tag. Read-only discovery verified the propagated values in `reviewed-ubuntu24.json`; permission probes merge those ASG tags with LT instance tags and reject drift. `iam:PassRole` remains limited to ExampleAppRole and EC2. [AWS launch-template IAM guidance](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/permissions-for-launch-templates.html).

**Do not perform the IAM write while preparing this PR.** After review, an authorized IAM administrator should inventory/back up current policies and inspect SCPs, boundaries and explicit denies. Apply this reviewed inline policy separately; it does not replace other attached policies or grant Jenkins IAM-edit permissions:

```bash
aws iam get-role --role-name ExampleToolsRole --query Role.Arn
aws iam list-role-policies --role-name ExampleToolsRole
aws iam list-attached-role-policies --role-name ExampleToolsRole
# Authorized administrator, only after review:
aws iam put-role-policy --role-name ExampleToolsRole \
  --policy-name CloudOpsReviewedAsgDeployment \
  --policy-document file://deploy/iam/jenkins-cloudops-deploy-policy.json
```

An Allow does not override an explicit Deny. Audit other broad grants too. Use instance-role authentication; do not add static credentials. Application-role permissions remain separate.

## Preview, permission validation and controlled deployment

First run successful merged-main CI with `DEPLOY_TARGET=none`. Gitleaks, pytest, SonarQube/Quality Gate, Trivy and immutable ECR publication must pass. For the later rollout, use Jenkins's ExampleToolsRole session and pipeline-supplied source commit/digest, `DEPLOY_TARGET=asg`, `ASG_AMI_REVIEWED=true` and designated `CLOUDOPS_DEPLOY_APPROVERS`.

Before manual approval Jenkins runs:

```bash
bash deploy/collect-cloudops-preflight.sh asg
```

This renders the allowlisted settings, checks LT creation authorization with DryRun, **creates a clean candidate LT version without source inheritance**, reads the complete version back privately from AWS, and performs RunInstances DryRun in both approved subnets. It archives `reports/asg-launch-template-preview.json`, `reports/asg-launch-candidate.json` and `reports/asg-launch-permissions.json` **before input**. This preflight writes a candidate version; it does not launch instances, change the default version or change ASG configuration/capacity. Failed or unapproved candidate versions are retained for inspection; no automatic deletion occurs. Console and approval text link `$BUILD_URL/artifact/reports/asg-launch-template-preview.json`. The preview shows inventory source v5 (not inherited), the exact numeric candidate, pinned Ubuntu ID/owner, explicit instance type, profile, both subnets, SG, private interface, metadata and user-data checksum/size/Bash validation. Full AWS readback must match the allowlist and expected settings; Placement/AZ/SubnetId restrictions are rejected. The candidate record binds the version, commit, immutable release image and launch-settings/user-data checksums. Approval identifies that exact candidate and digest. It never includes raw user data or secrets. Private requests stay in ignored `.deploy-work/`.

`validate-launch-permissions.sh "$ASG_VALIDATED_VERSION"` re-reads and verifies the exact candidate against its approval record, then uses hard-coded `--dry-run` for RunInstances in **each** subnet. Requests reference the clean candidate version, supply the subnet-specific private interface and reviewed ASG propagated tags, and do not override image/profile/metadata/user data to mask defects in that version. Version 5 is never used for a launch permission probe. Only nonzero `DryRunOperation` is accepted. AccessDenied, unauthorized, throttling, malformed output or unexpected zero exit fail. Nothing launches; the separately authorized preapproval candidate-version creation is the only preapproval resource write. DryRun establishes authorization, not EC2 capacity or application readiness.

After reviewing the artifacts and approving through the designated Jenkins input:

```bash
# Jenkins sets ASG_VALIDATED_VERSION from the successful preapproval record.
# All release/identity environment values must still come from the same build.
ASG_AMI_REVIEWED=true ASG_VALIDATED_VERSION="$ASG_VALIDATED_VERSION" \
  bash deploy/cloudops-asg-rollout.sh
```

This refreshes architecture/security discovery, verifies the existing approval record and AWS readback, repeats both subnet authorization checks for the **same numeric candidate version and digest**, then performs the existing initial rollout to 1/1/1. It creates no new version after approval. Missing, changed, inaccessible or mismatched candidate evidence fails before any capacity/refresh operation. Permission failure causes no capacity/refresh change. Later rollouts retain prior clean numeric version and Instance Refresh/rollback protection. Initial failure restores capacity 0/0/0; if read-only discovery confirms the original version/capacity are unchanged, it skips an unnecessary UpdateAutoScalingGroup. After an applied update, initial rollback drains to zero while retaining the clean numeric template, because the restricted policy excludes the contaminated v5 image and selecting it can fail authorization even at zero capacity. Unexpected concurrent template changes fail closed. Never scale old v5 separately. Keep the healthy canary until a separate retirement review.

## Validation limits

Pytest simulates version 5's inherited AZ pin, allowlist rendering, full candidate readback, preview rejection, approval/release binding, both subnet DryRuns (including independent denials), Ubuntu first boot, SSM availability, failure cleanup, runtime/image checks and rollback. `python3 tests/integration/rehearse_ubuntu24_gate.py` exercises real Ubuntu apt installation, reviewed AWS binaries when supplied locally, and the exact firewall gate without AWS access, host sockets or host port publication. It cannot establish EC2 systemd/Snap registration or RDS/ALB acceptance; those remain controlled-rollout checks. Live Jenkins Declarative/SonarQube acceptance also remains required.

No canary, RDS, secrets, live IAM policy, LT version or ASG capacity is modified while preparing this PR. No instance is launched.
