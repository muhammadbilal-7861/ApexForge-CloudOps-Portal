# Single-EC2 validation record

Date:2026-10-10. Branch:feat/single-ec2-illustrated-guide. Implementation tested at4e7be380d7de6e28b60ebc7add62051e88c728a7; follow-up documentation records results. Local proof does not establish AWS/Jenkins acceptance.

| Check | Actual result |
| --- | --- |
| Full pytest | **125 passed**, no skips,71.21s; root Linux container, no network/credentials/host socket |
| Python | compileall passed; Ruff F821/F822/F823 passed for app/deploy/tests/docs |
| Bash and ShellCheck | All retained deployment/integration/monitoring scripts passed |
| Jenkinsfile | Full Groovy4/JDK17 compilation passed;18 embedded scripts passed syntax and ShellCheck |
| Private inventory | Example/fixture JSON Schema2020-12 and semantic checks passed; example cannot authorize AWS |
| IAM | Both policy templates parsed/rendered offline; no unresolved placeholders; exact-instance SSM/no compute mutation |
| Compose/fresh clone | Example config valid; fresh committed clone built and started with no AWS credentials/inventory |
| Local app | /health,/ready,/metrics HTTP200; real CSRF-enabled registration/login/record write passed |
| Visual guide |14 labs;92 relative links;38 Bash example blocks parsed; YAML/JSON examples parsed |
| Diagrams | Eight PNG1280x900 and editable SVG pairs; XML/header checks and all eight visual inspections passed |
| Gitleaks8.29.1 | Zero findings, all reachable history and committed current tree |
| TruffleHog3.99.2 | One unverified JDBC candidate in history/current tree; private review confirms documented loopback URL without credentials |
| Trivy0.74.0 filesystem | No HIGH/CRITICAL vulnerabilities or failing misconfigurations |
| Trivy fresh app image |44 HIGH,0 CRITICAL,0 fixable; all OS findings; complete reports retained privately, no suppressions |
| Original identifiers |13 original resource IDs and one original private account absent from tracked current tree |
| Removed paths | No remaining code/documentation references to removed deployment functionality; application code unchanged |

## Test coverage retained and added

Removed obsolete feature tests/fixtures. Retained application/CSRF/log privacy, reproducibility, immutable ECR matching/conflict/API errors, OCI manifest/index/attestation handling, canonical Docker IDs, bridge/dual-stack publications, port conflicts, candidate success/failure, production readiness/ALB failure and exact original-container restoration. Added Single-AZ MariaDB/two DB subnets, private SSM-only instance checks, explicit empty-host first installation and failure cleanup, unknown-container refusal, runtime role/signing preparation, restricted tools policy and visual-guide validation.

## Secret-review detail

The sole TruffleHog JDBC detector result is instructional localhost PostgreSQL connection syntax in Lab10, with no username/password in its authority or query. Credential verification/network probing was disabled. No rule exclusions or scanner suppression were added. The repeatable audit helper deliberately exits nonzero when candidates need private review; that exit was reviewed, not treated as zero findings. Raw logs remain private; this record publishes aggregate counts/classification only. Baseline historical audit in SECURITY-AUDIT.md remains distinct from this revision.

## Isolation and untested AWS steps

Docker checks used a separate local daemon/socket/data directory, not an existing application daemon. Fresh-clone Compose used its own project; only its containers/network were stopped, database volumes retained. Repository edits/commits/PR are the only shared-system writes.

**No live AWS or Jenkins resources were read or changed**: no instance launch/termination, role attachment, RDS operation, secret rotation, deployment or capacity action. Network/AMI/account/host values in the guide are examples or learner-resolved values, never claimed observed live metadata. Independent lab acceptance still needs actual EC2/IAM/SSM/NAT/endpoints/ALB/RDS/packages/agent verification, Jenkins Declarative/plugin linter and Sonar webhook execution, app transport-security review, Grafana/Loki/alerts and maintenance rollback rehearsal. Groovy compilation is syntax validation, not a Jenkins plugin runtime test. The application driver does not yet establish documented RDS CA verification; do not claim live database TLS assurance.
