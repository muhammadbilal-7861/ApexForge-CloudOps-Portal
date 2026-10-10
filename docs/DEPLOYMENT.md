# Controlled independent AWS laboratory deployment

No command here provisions a complete cloud stack. Configure your own resources and validate inventory first. This PR performs no live AWS operations and does not authorize changes to the original environment.

1. Review configuration, clean official Ubuntu AMI provenance, private routing/endpoints, IMDSv2, existing RDS readiness, role identities, scoped IAM, secrets, log group, ALB/SG wiring and quotas. Do not reuse a contaminated AMI, runtime file or legacy container.
2. Render/review IAM policies; an authorized owner applies them separately. Enable branch protection and required security checks. Restrict Jenkins configuration and approvals.
3. Run Jenkins with **AWS_OPERATIONS=false / DEPLOY_TARGET=none**. Review Gitleaks, tests, Sonar gate and complete Trivy evidence. Resolve vendor-fixable findings; document owner-reviewed risks for unfixed findings.
4. On main, explicitly request **AWS_OPERATIONS=true / DEPLOY_TARGET=none** for publication only. The designated approver checks account/source/gates before the pipeline can publish. Existing immutable tags must match the newly built/scanned image ID; mismatches fail and cannot overwrite tags.
5. For canary, request `DEPLOY_TARGET=canary`. The approved runtime file must be root:root 0600 and match Secrets Manager's stable signing secret. SSM must be Online; application-role ECR/layer and ALB permissions must pass. Review the pinned digest before the second manual approval. Candidate 5001 is verified while the previous 5000 service runs; failed cutover restores and verifies the exact previous container plus ALB recovery.
6. For ASG, request `DEPLOY_TARGET=asg` and `ASG_AMI_REVIEWED=true`. The first restricted AWS approval authorizes creating a retained **candidate launch-template version only**. Source version 5 stays unchanged. The candidate is read back, validated against the allowlist and DryRun-checked in both private subnets. Preview/evidence is archived **before the second approval**. Rollout reuses that exact version/release digest, verifies authorization again, then changes initial capacity to 1/1/1.
7. Confirm every owned ASG instance has SSM Online, the first-boot marker, exact immutable image, Docker health, `/health`, `/ready`, application route and ALB health. Do not count the retained canary as an ASG instance. Do not retire the canary automatically.

## Rollback and evidence

A failed candidate leaves the previous service running. Failed production readiness or ALB health restores the retained original container/image/network/ports. No unknown container or Docker volume is deleted. Preserve failed/verified release containers for investigation and remove only explicitly inventoried artifacts after owner review.

A failed initial ASG rollout drains to 0/0/0, retaining the new clean template rather than launching the unsafe source image. An unchanged zero-capacity ASG avoids unnecessary updates. Active-fleet rollback requires an inventoried safe previous version. Evidence records commit, digest, candidate version, capacity, expected/verified instances and canary retention. Restrict artifact access: metadata is sensitive even without credentials.

## Promotion checklist — still pending live acceptance

- [ ] Independently reviewed inventory and IAM; no original account coupling.
- [ ] Restricted approvals and branch protection; untrusted PRs cannot access privileged agents.
- [ ] Current security scans reviewed, no secrets, fixable findings resolved; unfixed risks accepted explicitly.
- [ ] TLS/browser cookie settings, proxy trust, registration/rate-limit and upload controls reviewed.
- [ ] Clean Ubuntu first boot verified in both intended subnet configurations with application-role APIs.
- [ ] Every ASG instance passes SSM, marker, immutable image, Docker, readiness and ALB checks.
- [ ] Failure diagnostics visible in private CloudWatch; retention/redaction reviewed.
- [ ] Candidate/cutover/ALB failure and zero-capacity rollback rehearsed independently.
- [ ] Budgets, quota, backup/restore, cleanup and canary retirement separately approved.

ASG is **not production-ready** until these live checks pass. Repository tests and previews are necessary evidence, not live acceptance.
