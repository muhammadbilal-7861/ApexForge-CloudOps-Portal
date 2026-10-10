# 13 — Opt-in single-instance deployment and rollback

[Master guide](../MASTER-GUIDE.md).

## Architecture and purpose

![Cutover](../diagrams/07-deployment-rollback.png)
[Editable SVG](../diagrams/07-deployment-rollback.svg). `none` is default; `canary` means one permanent EC2 with a localhost5001 candidate. A verified candidate precedes any change to port5000. A failed production release restores the exact original container/configuration, including bridge/dual-stack publications when applicable.

## Prerequisites

Labs01–12, green security gates, source reviewed/merged to your main, `HEAD == origin/main`, immutable ECR, exact inventory, SSM Online, runtime0600, pre-created log group, app-role ECR/secret/ALB permissions, target registered on5000. You must be an authorized approver. No live acceptance is claimed by repository tests.

## First publication and schema setup

Run Jenkins with `AWS_OPERATIONS=true`, `DEPLOY_TARGET=none`, `TRIVY_SEVERITY=HIGH,CRITICAL`, `TRIVY_EXIT_CODE=1`. First approval authorizes ECR publishing only after gates/account/role/main checks. Record the immutable SHA tag/digest from successful evidence. Use a separate authorized app SSM shell to initialize the database **only on your first empty lab installation**, with the approved scanned image:

```bash
set -Eeuo pipefail
set +x
export CLOUDOPS_CONFIG_FILE=/etc/cloudops/environment.json
source /opt/apexforge360/deploy/load-config.sh
read -r -p 'Approved release digest (sha256: plus64 hex): ' RELEASE_DIGEST
[[ "$RELEASE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]]
APPROVED_IMAGE="$ECR_URI@$RELEASE_DIGEST"
PRIVATE_LOGIN=$(mktemp -d)
chmod 700 "$PRIVATE_LOGIN"
trap 'rm -rf -- "$PRIVATE_LOGIN"' EXIT
aws ecr get-login-password --region "$AWS_REGION" |
  sudo docker --config "$PRIVATE_LOGIN" login --username AWS --password-stdin "$ECR_REGISTRY"
sudo docker --config "$PRIVATE_LOGIN" pull "$APPROVED_IMAGE"
sudo docker run --rm --network host --env-file /etc/cloudops/runtime.env \
  "$APPROVED_IMAGE" flask --app wsgi init-db
```

Confirm digest against Jenkins evidence, not an arbitrary pasted value. This short-lived owned schema container exits/removes itself, leaving the first-install Docker inventory empty. Do not seed development/demo users in production. For later schema changes use reviewed migrations; never assume this rollback mechanism reverses database changes.

Set `ALLOW_INITIAL_INSTALL=true` in the reviewed private inventory **only** for the empty app host and submit a new Jenkins run with `AWS_OPERATIONS=true`, `DEPLOY_TARGET=canary`, blocking HIGH/CRITICAL. Both approvals are required. The deployment approval displays initial-install status. After successful installation, set the private inventory flag back to false for normal replacement releases.

## Cutover steps and evidence

1. Checkout/Gitleaks/pytest/Sonar/Trivy/build/image gates pass.
2. Restricted AWS approval freezes inventory; publisher validates IAM role/account and exact origin/main, publishes or reuses the matching immutable SHA tag.
3. Topology preflight validates one private SSM-managed app, IMDSv2/profile/SG, one Single-AZ MariaDB, two DB subnet-group AZs, ALB forwarding and log group.
4. SSM application-role preflight validates runtime, stable signing key, DB secret, ECR manifest/index/layer permissions and ALB DescribeTargetHealth **before** manual deployment approval. Authorization failures stop immediately.
5. Restricted second approval binds commit/digest/instance. SSM downloads source scripts by full commit and verifies SHA256 before executing.
6. Candidate on loopback5001 passes Docker health, `/health`, `/ready`, application route and image-ID/version checks. Old5000 stays serving.
7. Recheck canonical64-character prior ID/name/image/network/original port bindings. Stop and retain it; start same approved digest managed container5000. Verify Docker/readiness/route/ALB.
8. Archive release/evidence JSON plus security reports. No unknown container or volume is deleted.

## Expected output and validation

```bash
# Workstation, your owned app only:
aws elbv2 describe-target-health --target-group-arn "$TARGET_GROUP_ARN"
curl --fail "https://$APP_DOMAIN/health"
curl --fail "https://$APP_DOMAIN/ready"
# App SSM shell; never docker inspect .Config.Env:
sudo docker ps --all --no-trunc --format '{{.ID}} {{.Names}} {{.Image}} {{.Status}}'
sudo docker inspect --format '{{.Id}} {{.Config.Image}} {{.State.Health.Status}}' cloudops-app
```

Expect one healthy registered target, two HTTP200 responses, exact digest and healthy managed container. Previous and candidate containers remain stopped under identifiable names. Check browser authentication/record operations and CloudWatch diagnostics privately. Verify no image changes on a repeated same-commit run.

## Troubleshooting and root causes

Candidate failure: prior service untouched. Production readiness/ALB failure: exact prior container restarted with original network/ports and health/readiness/ALB recovery checked. First install has **no prior service**; new failed containers are stopped/retained and target must remain unhealthy. Do not call that a successful restoration.

If automated restoration fails, use an authorized SSM shell to inventory exact IDs/configurations and preserve logs. Never delete/recreate the old container. A manual restoration requires a separately reviewed ledger of previous64-character ID, name/image/network/port/mount settings and release digest. Example procedure after that review:

```bash
read -r -p 'Exact previously inventoried64-character container ID: ' PREVIOUS_ID
[[ "$PREVIOUS_ID" =~ ^[0-9a-f]{64}$ ]]
sudo docker inspect --format '{{.Id}} {{.Name}} {{.Config.Image}} {{.HostConfig.NetworkMode}} {{json .HostConfig.PortBindings}} {{json .Mounts}}' "$PREVIOUS_ID"
# Review output against saved inventory. Stop only the exact failed release ID after review.
# If the prior managed name was cloudops-app, first retain the failed container under a unique name;
# rename the original back only when that name is free and canonical IDs are rechecked.
sudo docker start "$PREVIOUS_ID"
curl --fail http://127.0.0.1:5000/health
curl --fail http://127.0.0.1:5000/ready
```

Then confirm exact digest/version and registered target healthy via the full verifier using approved inventory. Never execute `docker start` before ownership/config review; a successful start alone is not readiness. Port conflict: inspect owner, do not kill it. Identity mismatch: inventory changed during verification; investigate rather than removing identity checks. ALB AccessDenied is IAM, not an unhealthy app; fix reviewed role permissions, not polling timeouts.

## Security considerations

Manual approval cannot substitute for gates. Preserve immutable tag/image comparison, secret-safe tracing, application role and exact recipient. Single-instance cutover briefly interrupts requests; it is not zero downtime. Runtime/DB changes are outside image rollback. Record risk and maintenance expectations.

## Cleanup

Keep old images/containers/volumes until rollback window, backups and evidence review are complete. Cleanup only specific owned IDs through a separate approved procedure. Never auto-retire another healthy application host.

## Interview questions with answers

**Why canonical Docker IDs?** `ps -q` can truncate IDs; full inspect IDs avoid false mismatches and ambiguous resolution.
**Why candidate before stop?** A bad image must fail verification while the previous application is still serving.
