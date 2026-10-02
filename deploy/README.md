# CloudOps EC2 deployment runbook

This is a repository-side, opt-in extension. It does not create networking, modify RDS, or deploy during normal CI. Jenkins uses the existing EC2 role and the ECR image digest produced by the successful CI build. Do not add AWS credentials to the job or repository.

## Targets and safety controls

* `DEPLOY_TARGET=none` is the Jenkins default. `canary` and `asg` each require `main`, `HEAD == origin/main`, the expected AWS account/role, HIGH/CRITICAL Trivy gates enabled, immutable ECR tag policy, a valid ECR digest, successful read-only topology checks, and a separate 15-minute Jenkins approval. ECR repository tag mutability must be configured to `IMMUTABLE`.
* `canary` addresses only `i-02777a62f2a65bc1e` through SSM. It must already be running and SSM Online. The helper never starts it. The last operator-reported state was stopped; resolve that manually before requesting this target. If RDS is not available or `/ready` is not 200, deployment is refused.
* `asg` requires `ASG_AMI_REVIEWED=true` after an operator verifies that the configured AMI is sanitized and contains no secrets or application state. The current LT source must remain an explicit numeric version of `lt-028eb222c6fcfffc1`. The first opt-in rollout scales the existing `asg-cloudops-app` from 0/0/0 to 1/2/2. Later rollouts use Instance Refresh with an explicit numeric version and automatic rollback enabled.
* Before either target, preflight checks the existing `alb-load`, listener/rule forwarding, `tg-cloudops-app` port 5000 and `/ready`, private subnets, app/ALB/observability security groups, RDS status, and `/cloudops/app` log group. App port 5000 ingress must reference only the ALB and authorized observability security groups; CIDR/prefix-list exposure blocks the run.
* Every node uses the same Secrets Manager database secret and the stable `cloudops/prod/flask-session-key` signing secret. Bootstrap writes `/etc/cloudops/runtime.env` as root:root mode 0600; it never prints secret data. Do not rotate the session key per node. `SESSION_COOKIE_SECURE=false` remains until HTTPS termination at the ALB is confirmed end to end, after which the secure-cookie policy must be changed deliberately.

## Container inventory and rollback

Before changing a container, `cloudops-deploy.sh` prints its name and image and inspects mounts for an existing managed or explicitly allowlisted container. It accepts only the managed `service=cloudops` label at the canonical name. A prior managed container is stopped and retained under a rollback name; a failed candidate is stopped and retained for investigation before the old container is restored. No images or volumes are pruned.

Unknown legacy/test containers are left alone by default. To retire a specific old container, first inspect and record its name, exact image and mount list on the instance. Only then may an operator create `/etc/cloudops/old-containers.allowlist`, root-owned mode 0600, with one tab-separated exact name and image per line. Entries are stopped/removed only after the image matches and mounts are printed again; `docker rm` never uses `-v`. Do not allowlist `cloudops-app` or an unrelated workload.

Neither path registers/deregisters targets, terminates instances, deletes storage, changes the VPC/subnets/listeners, or edits RDS. Canary verification reports the current target state but does not require or mutate registration. ASG rollout requires the expected count of healthy ALB targets and per-instance SSM verification of immutable image, `/health`, `/ready`, and the protected API route response. If a deployment fails, retain the Jenkins evidence and instance logs, then inspect rollback health before retrying.

## IAM and Jenkins setup

Review and attach `iam/jenkins-cloudops-deploy-policy.json` to the existing Jenkins EC2 role only after checking the account's actual role names. It separates Jenkins orchestration permissions from the application node permissions in `iam/cloudops-ec2-instance-policy.json`. The sample Jenkins policy scopes ECR, the named ASG/LT, approved SSM instance IDs/tags, the database secret, and the app role passed to EC2. Some AWS discovery/output APIs require `Resource: "*"`. EC2 launch-template-version creation is powerful: AWS does not provide field-level restrictions on all data supplied to a new version. Protect Jenkinsfile changes and the manual approval permission accordingly; the helper itself copies a specific existing numeric version and only overrides user data, metadata requirements, and instance tags.

The EC2 app instance profile needs ECR pull, read access to the two named Secrets Manager secrets, CloudWatch log-group discovery plus log-stream write access under `/cloudops/app`, and read-only target health lookup. It also needs the standard SSM managed-instance permissions for Run Command. Pre-create `/cloudops/app`; the scripts do not create log groups. SSM Agent and Docker must be installed/running. The bootstrap template supports common Debian/Ubuntu and Amazon Linux package managers, but the AMI must still be reviewed and tested before any ASG rollout.

No manual app EC2 retirement is automated. After an ASG run, independently verify healthy targets and version/image, inventory any manually managed app container and its mounts, and confirm its target registration before deciding whether to retire it. Keep rollback containers and data volumes until a separate reviewed cleanup.

## Evidence and checks

Jenkins archives `reports/deployment-release.json` and `reports/deployment-evidence.json` with the normal security reports. Evidence contains only commit, immutable image digest, selected target, numeric launch-template version, instance IDs and health outcome. User data, runtime environment contents, database values, signing keys and AWS credentials are not archived. `.deploy-work/` is ignored and preflight deliberately excludes launch-template `UserData` from snapshots.

This runbook describes intended controls; the repository authoring task does not query or mutate live AWS. Jenkins read-only preflight is authoritative at the time an operator opts in.
