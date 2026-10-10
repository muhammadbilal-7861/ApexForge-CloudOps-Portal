# Private single-instance observability

[Complete illustrated monitoring lab](../docs/labs/11-monitoring.md) · [Original monitoring diagram](../docs/diagrams/08-monitoring.png)

These are reusable examples, not verified live inventory. Region/VPC/IP/datasource UIDs must come from your reviewed account. One app EC2 exposes5000 only to ALB and observability SGs. Prometheus discovers tagged app EC2 using its role or uses the lab's verified static private target; Blackbox probes /ready. Grafana/Prometheus management UIs bind loopback and are accessed through SSM. Loki3100 admits app SG only; do not expose it publicly.

Assets include Prometheus examples/rules/tests, Grafana provisioning/dashboard, Alertmanager placeholders and secret-safe Alloy journal filtering. The Alloy helper requires an exact cloudops container with journald/tag cloudops and a verified10.x private Loki address. Install a reviewed pinned Alloy binary and rerun the helper after successful releases to refresh image labels; it never recreates the app. Do not source application runtime secrets into monitoring.

For an existing owned monitoring installation use scripts/inventory.sh privately and RECONCILIATION.md before changes. Inventory exposes operational metadata; redact outputs. Preserve existing volumes/data/UIDs and back them up. Never assume an example instance/IP represents a running host. VALIDATION.md records fixture checks and live-test limits.
