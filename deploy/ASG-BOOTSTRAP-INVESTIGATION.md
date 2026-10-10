> Historical engineering notes: resource identifiers below have been replaced with synthetic examples. They are not current deployment instructions or verified live resources. Use [the reusable configuration guide](../docs/CONFIGURATION.md) and reviewed private inventory.

# Prior ASG failure: read-only review, 2026-10-04

## Verified observations

- `DescribeScalingActivities` reports repeated launch failures because the regional vCPU limit is **8**. Requesting a second ASG instance while the existing three 2-vCPU nodes remain active exceeds that budget.
- Instances `i-0000000000000000d` and `i-0000000000000000b` launched at approximately 15:45 UTC using reviewed Ubuntu AMI `ami-00000000000000001`. ASG activity records explicitly report ELB health failures before their replacement/termination.
- The later instance `i-0000000000000000c` launched at 16:08 UTC. The ASG was subsequently drained by an existing operator action; its termination completed at 16:21 UTC. This review made no capacity changes.
- Retained EC2 console output for all three reports a failed cloud-init `scripts_user` module. Cloud-init finished after roughly 44–47 seconds. Bootstrap redirected its output into a private local file, so no specific failing command or bootstrap exit line is available in the retained console.
- Existing SSM command-invocation history for these three terminated instances is empty; no new SSM command was issued to investigate them.
- `/cloudops/app` has no log streams. The previous bootstrap configured/started CloudWatch after package installation, CLI/agent installation, Snap/SSM setup and runtime-secret validation. Failure before that point leaves no CloudWatch bootstrap evidence.
- Read-only launch-template v7 validation found rendered Bash user data, no carriage returns and valid Bash syntax. Raw user data was inspected privately and is not included in this document or reports.
- At inspection, ASG capacity was **0/0/0**, selected template version **7**, with no instances. The separate canary `i-00000000000000001` remained the sole healthy target.

## Conclusions and limits

Quota rejection and unsuccessful bootstrap are separate observed failures. A single initial ASG instance addresses the requested capacity budget; it does not establish that bootstrap will succeed. The precise prior bootstrap command failure cannot be proven from retained evidence after these instances were terminated. Do not attribute it to a particular package, credential, secret, download or network failure without command-level evidence.

This change starts CloudWatch earlier and adds controlled stage/status/exit/line diagnostics. A bounded instance-role CLI fallback publishes a structured failure when the agent is unavailable. Before CLI installation, or when IAM/network prevents logging, the safe console status remains the fallback. Explicit `exit` failures also trigger cleanup and diagnostics. No secret values, raw user data or arbitrary command/environment output are sent to the console or fallback event.

No instances, SSM commands, secret reads/rotations, live IAM writes, launch-template creation or capacity updates were performed during this incident review. Private console/user-data inspection files remain ignored under `.deploy-work/`.

## Controlled validation after review

1. Run merged-main Jenkins CI with `DEPLOY_TARGET=none`.
2. Run the opt-in ASG preflight and inspect the exact candidate/digest, both subnet permission checks and initial capacity **1/1/1** before designated manual approval.
3. After a separately authorized rollout, inspect `/cloudops/app` streams `<instance-id>/bootstrap`, `<instance-id>/deploy` and, on failure, `<instance-id>/bootstrap-status`.
4. Require first-boot completion, immutable image verification, Docker health, readiness, SSM Online and ALB health for the new ASG instance. Evidence must list exactly that instance and one healthy target, with `canaryRetired=false`.
5. On failure, retain diagnostic evidence and drain only the ASG to zero. Do not retire the canary or weaken health/security checks. Verify startup timing against the existing health-check grace period before subsequent capacity decisions.
