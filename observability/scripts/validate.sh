#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")/.."
# Use matching-version binaries selected after live inventory.
promtool check config --syntax-only prometheus/prometheus.example.yml
promtool check rules prometheus/cloudops-rules.yml
(cd prometheus && promtool test rules rules.test.yml)
amtool check-config alertmanager/alertmanager.example.yml
EC2_INSTANCE_ID=i-validation CLOUDOPS_ENVIRONMENT=validation CLOUDOPS_IMAGE_VERSION=validation LOKI_PUSH_URL=http://127.0.0.1:3100/loki/api/v1/push alloy validate alloy/config.alloy
for script in scripts/*.sh alloy/*.sh; do bash -n "$script"; done
python3 tests/validate_assets.py
