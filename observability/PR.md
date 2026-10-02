# Draft PR: Reconcile existing CloudOps observability without replacing storage

The existing monitoring EC2 stack has unknown Compose paths, installed versions and persistent mounts. Add an inventory-first reconciliation plan that reuses the current project and preserves Prometheus TSDB, Grafana data sources/dashboards and Loki storage.

Provide private tag-based EC2 discovery with instance-role IAM, SSM tunnels, app-scoped sanitized journald collection with an Alloy systemd bootstrap, dashboard/provisioning examples, readiness probes, alert rules and validation scripts. Document current Gunicorn worker-counter limitations and the required ALB metrics-denial gate. Changes are limited to observability/ and the main README observability section; Jenkinsfile and deploy/ are untouched.

Validation: native promtool config/rules and nine rule-test scenarios, amtool, Alloy syntax, dashboard PromQL, YAML/JSON, shell syntax and privacy fixtures passed locally. Exact tool versions and limitations are in VALIDATION.md. AWS, actual Loki configuration, installed-version compatibility, Linux journal behavior, network policy and notification delivery remain unverified. No deployment success is claimed.

Keep this PR in draft and do not merge until live monitoring inventory is reviewed. Do not launch a parallel stack, overwrite existing Compose or delete monitoring data.

Publication was blocked by Git push 403 for apexforge360 and connected GitHub API 403 (Resource not accessible by integration). After repository access is corrected, run from this feature worktree:

```bash
git push -u origin feat/cloudops-observability
# If GitHub CLI is available:
gh pr create --repo muhammadbilal-7861/ApexForge-CloudOps-Portal --base main --head feat/cloudops-observability --draft --title "Reconcile existing CloudOps observability without replacing storage" --body-file observability/PR.md
```
