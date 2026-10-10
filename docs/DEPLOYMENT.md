# Single-instance deployment runbook

[Complete deployment/rollback lab](labs/13-deployment-rollback.md) · [Diagram](diagrams/07-deployment-rollback.png)

CI is safe by default: AWS_OPERATIONS=false and DEPLOY_TARGET=none. Publication requires explicit AWS_OPERATIONS, exact main/account/role, blocking HIGH/CRITICAL gates, private inventory and designated approval. Only DEPLOY_TARGET=canary requests a release to the one inventoried EC2. Topology/application-role preflight precedes the second manual approval.

ECR tags remain immutable. Repeated commit builds reuse an existing digest only when its pulled linux/amd64 image ID matches the scanned build. API failures and conflicts stop. SSM source downloads are full-commit/checksum pinned. Candidate host-network5001 binds loopback; existing5000 stays serving. Successful candidate precedes exact prior ID/name/image/network/port-binding recheck and retained-container cutover. Production checks exact image/version, Docker health, /health, /ready, application route and ALB. Failed production restores original network/publications and verifies recovery. No unknown container/volume is deleted.

First installation requires ALLOW_INITIAL_INSTALL=true and an empty Docker inventory; prepare runtime and initialize schema using the approved image first. No prior app exists for restoration, so failure leaves no serving new container and must not count as successful deployment. Set the flag false after success. Do not retire any other healthy instance automatically.

Live acceptance requires independently verified IAM/SSM/routing/ECR/ALB/RDS/agents, browser flow, monitoring/alerts and rollback maintenance rehearsal in the owner's lab. These were not live-tested by this PR. Repository tests do not grant production acceptance.
