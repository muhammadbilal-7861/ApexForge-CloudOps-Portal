# Redacted repository security audit

Date: 2026-10-10. Baseline: latest main `97e97a9`. Scope: all fetched remote branches/tags, local refs and all reachable Git objects; current tracked application, CI, deployment, fixtures, documentation and monitoring assets. Git history and contributor attribution are retained. No AWS resource, secret, Jenkins credential or running production container was changed.

## Methods and limits

- Gitleaks **8.29.1**, full history using `git --log-opts=--all --redact`: zero findings.
- TruffleHog **3.99.2**, Git scan plus an independent filesystem scan of the union of **202 unique reachable blobs from 38 baseline commits**: zero findings. Verification disabled: discovered credentials would never be tested against live services. Scan errors are fatal in the all-blob pass.
- Manual all-object inventory and review of credential/connection-string construction, environment examples, IAM, resource coupling, CI output, logs, private endpoints, contact domains and special-file extensions. A connection-string pattern hit was a URI constructed from runtime configuration, not an embedded credential.
- No tracked archives, backups, Terraform state, private keys, certificates, screenshots or binary reports were present in the baseline inventory. The only environment file was `.env.example`. Contact examples used reserved/synthetic domains; public GitHub repository identity and contributor attribution are retained.
- Scanner binaries were downloaded from official release assets and checked against the release API's SHA256 digest. Raw scanner output and intermediate inventories stayed in a private local audit directory; only aggregate results are published here.

This is a repository audit, not an audit of live AWS/Jenkins/AMI contents, forks, deleted unreachable Git objects, external build artifacts or ignored operator files. Zero findings do not prove absence of secrets. Future commits need continued scanning and review.

## Findings and classification

| Classification | Finding | Action/status |
| --- | --- | --- |
| Confirmed secret | **None confirmed in the audited Git scope.** | No repository-derived credential rotation list. Do not infer that live secrets are safe. |
| Sensitive infrastructure metadata | Original account/region, node/network/template/image identifiers, role/resource names and a private monitoring address appeared in current files and history. | Current operational source uses validated private configuration. Docs/assets/fixtures use synthetic identifiers; history remains exposed until an owner-coordinated cleanup decision. |
| Harmless public identifier | Canonical public AMI owner, official public SSM parameter, package/image digests, upstream URLs, repository URL and commit attribution. | Retained as provenance or attribution; these are not private deployment credentials. |
| Synthetic fixture | Public local demo passwords, signing key, fake role/account/resource IDs, mocked AWS responses and test-secret values. | Development/test only. Public config is marked `example_only` and cannot authorize AWS operations. No blanket scanner exclusions were added. |
| Needs manual verification | Existing public forks/caches, Jenkins artifacts, private inventory/runtime files, contaminated historical AMI, live role attachments, TLS/metrics controls and actual CloudWatch retention. | Owner review outside this PR. No live credential probing, secret rotation or infrastructure mutation performed. |

**Historical remediation:** changing current files does not remove exposed historical metadata. If the owner finds any real secret in history, revoke/rotate first, investigate use, then plan coordinated cleanup across shared refs, clones/forks and cached artifacts. Do not force-push or rewrite shared history automatically. The previously reported contaminated AMI needs a separate owner review of embedded credentials and any rotation obligations; it is not evidence that those credentials were committed to this Git repository.

## Engineering review

### Confirmed weaknesses addressed

- Production could silently generate an unstable signing key; it now requires an explicit stable key of at least 32 characters. Approved cloud runtime validation still checks the Secrets Manager value without printing it.
- Request logs could contain arbitrary paths/header request IDs and upload logs contained filenames through object keys. Logs now accept UUID request IDs only, use stable route endpoints, omit upload keys, and Alloy's sanitized parser follows the new format.
- A malformed Secrets Manager field could appear in a parser exception traceback. The secret loader now logs a generic warning without exception details; regression coverage verifies redaction.
- CSRF rejection could reach response logging before request-ID initialization, raising an exception instead of returning HTTP 400. Response logging now initializes the ID safely; the new CSRF regression verifies the rejection and response header.
- Five browser forms rendered CSRF tokens as visible text rather than hidden submitted fields. A new enabled-CSRF browser-flow regression reproduced the failure before the fix and now verifies registration, login, creation, deletion and the upload form. CSRF protection remains enabled.
- SQLAlchemy statement parameters are hidden from engine exception logging. Upload object names use Werkzeug's filename sanitizer while retaining random per-user object keys and the existing size/extension limits.
- AWS publication was automatic on main. It now requires explicit opt-in, validated account/role, blocking gates and designated approval before publishing or creating candidate versions. Deployment retains its separate release-bound approval.
- Real resource IDs and permissive fake target-group suffixes were embedded in deployment/test code. Private inventory is validated and frozen per build; target-group verification now binds the exact ARN. Fixtures use canonical synthetic IDs and consistent effective network mappings.

### Retained controls

Hashed passwords, login requirements, CSRF, ownership checks for deletion, SQLAlchemy binding, non-root Gunicorn, health/readiness separation, disabled lab-failure endpoints by default, hashed Python lock, pinned image/build tooling, full Trivy evidence, fixable-vulnerability gates, Gitleaks, webhook Sonar gate, strict main/HEAD/account/role checks, immutable ECR tag/image comparison, digest-pinned delivery, IMDSv2, scoped IAM/PassRole, both-subnet DryRun, candidate-first verification, exact original-container rollback, first-boot firewall and marker, one-instance initial rollout and safe zero-capacity rollback.

### Recommendations and unresolved acceptance

- Add environment-specific rate limiting, registration policy, authorization/tenant boundaries and admin-only user-directory access before production. Shared records/user directory are learning-project behavior, not a tenant-isolated service.
- Review upload MIME/content validation and malware scanning; extension allowlisting is not content inspection. Keep S3 private and scoped, with safe download headers.
- Configure TLS at the edge and `SESSION_COOKIE_SECURE=true`; limit trusted proxy access. Secure cookies default true in the cloud example, with HTTP-only lab use explicitly reviewed.
- Enforce ALB/private-scraper restrictions on `/metrics` and variants. Readiness health checks do not establish that metrics are private. Review private Loki/Grafana/Prometheus access, retention and label cardinality.
- Jenkins Docker socket access confers substantial host privilege. Isolate trusted agents; untrusted PRs must not run with deploy credentials, privileged sockets or secret-bearing workspaces. Configure branch protection, restricted approvers and webhook authentication administratively.
- Scanner images and GitHub Actions currently use reviewed version tags rather than digest/commit pins. Runtime/build inputs are pinned; review immutable scanner/action pins as a separate supply-chain improvement. MariaDB's local minor-version tag is also moving and development-only.

## Verification evidence

- Full pytest suite: final results recorded in [VALIDATION.md](VALIDATION.md).
- Local Docker Compose image built; MariaDB/app health, table initialization, synthetic demo seeding, `/health` and `/ready` passed on an isolated audit daemon. No AWS credentials were mounted.
- Trivy **0.74.0** complete HIGH/CRITICAL scans: filesystem **0** findings; application image **44 HIGH / 0 CRITICAL**, **0 vendor-fixable** findings at scan time. All image findings were OS packages. Findings remain visible; no CVEs were suppressed and no gate was changed to pass them.
- ShellCheck, Bash/Python syntax, JSON schema plus cross-field validation and monitoring privacy checks passed. Full Groovy compilation and embedded Jenkins shell checks passed. Live Jenkins Declarative/plugin execution and Sonar acceptance remain manual.

## Owner actions before public promotion

- Review this PR and the historical metadata exposure; decide whether coordinated history cleanup is worthwhile. Rotation is mandatory if any real historical credential is subsequently confirmed.
- Enable private vulnerability reporting, branch protection, protected Jenkins agents/approvers, artifact restrictions and a documented response process.
- Supply an independent reviewed private inventory; render/review IAM policies before applying them manually. Do not copy synthetic AMI/IDs into live use.
- Document risk acceptance for unfixed image vulnerabilities, update reviewed pins when fixes arrive, and complete the deployment acceptance checklist.
- Choose a license explicitly. No LICENSE file or legal terms are selected by this PR.

## Single-instance illustrated-guide follow-up

The 2026-10-10 follow-up reran current-tree and all-reachable-history scanners after adding original diagram binaries and learner commands. Gitleaks found zero; TruffleHog reported one documented loopback JDBC URL without credentials, privately reviewed as a non-secret example. No suppressions or history rewrite were used. See [current validation](VALIDATION.md) for full counts, fresh-clone proof and live-test limits. This supersedes no earlier baseline evidence.
