# Hardening validation

Validated 2026-10-10 on the feature branch, without accessing live AWS or changing production/Jenkins resources.

| Check | Result |
| --- | --- |
| Full pytest, isolated Linux container, no network/host socket/credentials | **201 passed in 82.17s**, no skips |
| Gitleaks 8.29.1 reachable history and current tracked tree | **0 findings** |
| TruffleHog 3.99.2 Git/all-reachable-blob/current-tree scans, verification disabled | **0 findings** |
| Manual reachable-object inventory | 38 baseline commits / 202 distinct blobs; runtime URI constructor classified as non-secret |
| Trivy 0.74.0 HIGH/CRITICAL filesystem vulnerability/config scan | **0 findings** |
| Trivy 0.74.0 complete current application-image vulnerability scan | **44 HIGH / 0 CRITICAL; 0 vendor-fixable**, all OS packages |
| Docker Compose build and local DB/app smoke | Passed; health/readiness, table initialization and development-only sample data |
| ShellCheck / Bash syntax | Passed for deployment/integration/monitoring scripts and rendered bootstrap |
| Python compile / Ruff undefined-name checks | Passed; F821/F822/F823 |
| Jenkinsfile Groovy compilation | Passed with Groovy 4 / JDK17 |
| Embedded Jenkins shell syntax | **20 scripts passed** |
| JSON Schema and semantic configuration checks | Passed for public example and synthetic fixture |
| Monitoring YAML/JSON/metrics/privacy fixtures | Passed |
| Original private identifier check | 13 original resource IDs and original account absent from current tracked tree |

The 22 new tests cover non-deployable public examples, missing/invalid/cross-account configuration, independent inventory/rendering/IAM, AMI owner enforcement, safe CI approval ordering, stable production signing keys, path/header/secret/SQL log redaction, sanitized uploads, CSRF rejection and development-only idempotent demo data. Existing canary, bridge/dual-stack networking, canonical identity, immutable ECR, permissions, first-boot, per-instance ASG and rollback regressions remain intact.

## Reproduction

Use README for local setup and pytest. Use `deploy/audit-history.sh` with reviewed Gitleaks/TruffleHog releases for private, repeatable history scans. Scan an image with `trivy image --scanners vuln --severity HIGH,CRITICAL IMAGE`; retain complete reports and evaluate fixable vulnerabilities separately. Render bootstrap against synthetic test inventory for offline syntax checks, never against live credentials.

The complete suite was packaged from tracked source into a disposable test container. It ran as container root to cover root-owned runtime-file/first-boot simulations, with network disabled and no host Docker socket or AWS credentials. Docker smoke tests used a separate local audit daemon and owned Compose project; original running containers were not modified.

## Remaining manual validation

Groovy compilation validates syntax, not Jenkins plugin availability/Declarative execution. Configure Pipeline Utility Steps, Sonar credentials/server/webhook, restricted approvals, private inventory and trusted agents administratively, then run CI with AWS_OPERATIONS=false / DEPLOY_TARGET=none after review. Sonar live acceptance and independent AWS/Ubuntu ASG acceptance remain pending. No EC2 instance was launched, no capacity changed, no IAM policy applied and no deployment performed.

Fresh-clone verification is recorded after the implementation commit so the rehearsal uses committed source and public examples only.
