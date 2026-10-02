# Local validation evidence

Validated locally on 2026-10-02 (Windows). Official upstream Windows release binaries were downloaded outside the repository: Prometheus/promtool 3.15.0, Alertmanager/amtool 0.34.1 and Alloy 1.20.1. These are validation tool versions, NOT claims about the installed EC2 images and NOT a server upgrade recommendation. Repeat checks with inventoried server versions before reconciliation.

Passed:

- `promtool check config --syntax-only observability/prometheus/prometheus.example.yml`
- `promtool check rules observability/prometheus/cloudops-rules.yml` (seven rules)
- `promtool test rules observability/prometheus/rules.test.yml`: app-down timing, healthy app, recovery, empty discovery, high-traffic 5xx firing, low-traffic guard, readiness failure, high CPU and high p95 latency.
- `amtool check-config observability/alertmanager/alertmanager.example.yml`
- `alloy validate observability/alloy/config.alloy` with non-sensitive validation environment values.
- Git Bash `bash -n` on inventory, bootstrap and validation scripts.
- `python observability/tests/validate_assets.py` using Python 3.14/PyYAML 6.0.3: YAML/JSON parsing, dashboard IDs, app metric references and privacy acceptance/rejection fixtures.
- Dashboard PromQL parsed by promtool as temporary recording rules outside the repository.
- `git diff --check`.

Not verified: live AWS inventory/instance start, installed version compatibility, actual Loki configuration/schema/storage, Compose reconciliation, Linux journal cursor/rotation/restart behavior, ASG discovery, role/IMDS routing, SG/ALB denial rules, dashboard rendering, readiness exporter runtime, Gunicorn aggregate metrics, notifications or deployment. Docker CLI is installed but the daemon is unavailable; AWS CLI is unavailable. No monitoring state or storage was changed. Follow the exact host and maintenance checks in README before rollout and review live inventory before merge.

The disabled Alertmanager receiver deliberately has no notification integration. Validate realistic latency/traffic before threshold adoption. The privacy fixture checks the source acceptance pattern and output field allowlist; Linux end-to-end Alloy processing remains mandatory.
