# 11 — Prometheus, Grafana, Loki and Alloy

[Master guide](../MASTER-GUIDE.md).

## Architecture and purpose

![Monitoring](../diagrams/08-monitoring.png)
[Editable SVG](../diagrams/08-monitoring.svg). Prometheus scrapes the private app; Blackbox probes readiness. Grafana queries Prometheus/Loki. Alloy forwards only bounded request summaries. CloudWatch independently collects deployment diagnostics. One scrapeable instance does not prove ALB health.

## Prerequisites

Owned private observability host/role, Docker, OBS_SG↔APP_SG rules, SSM forwarding, disk/retention budget. Review each official image's version/digest. Existing monitoring resources must be inventoried/backed up before changes; the commands below assume a **new owned lab host**.

## Installation and configuration

On the observability host create `/opt/apexforge-monitoring`, copy the repository's `observability` assets there, and resolve reviewed images for `prom/prometheus`, `prom/blackbox-exporter`, `grafana/grafana`, `grafana/loki`. Set `PROMETHEUS_IMAGE`, `BLACKBOX_IMAGE`, `GRAFANA_IMAGE`, `LOKI_IMAGE` to concrete official `@sha256:` references. Set up Loki's configuration from [its official installation documentation](https://grafana.com/docs/loki/latest/setup/install/docker/), using single-node filesystem storage and current release schema. Record the storage path/retention; do not reuse an unknown schema or storage volume.

For a minimal static single-instance lab, create `/opt/apexforge-monitoring/prometheus.yml`, replacing the example `10.0.11.10` with your app's independently queried **private** IP:

```yaml
global:
  scrape_interval: 30s
  evaluation_interval: 30s
rule_files:
  - /etc/prometheus/cloudops-rules.yml
scrape_configs:
  - job_name: cloudops-app
    static_configs:
      - targets: ['10.0.11.10:5000']
  - job_name: cloudops-ready
    metrics_path: /probe
    params:
      module: [http_ready]
    static_configs:
      - targets: ['http://10.0.11.10:5000/ready']
    relabel_configs:
      - source_labels: [__address__]
        target_label: __param_target
      - source_labels: [__param_target]
        target_label: instance
      - target_label: __address__
        replacement: 127.0.0.1:9115
```

For role-based EC2 discovery instead, adapt `observability/prometheus/prometheus.example.yml` region/VPC/tags to your inventory. It uses IMDSv2 role credentials, no access keys. Optional node-exporter job remains disabled unless installed/tagged and port9100 is allowed **only** from OBS_SG. Use current metrics/rules validation before starting.

```bash
cd /opt/apexforge-monitoring
docker volume create apexforge_prometheus
docker volume create apexforge_grafana
docker volume create apexforge_loki
docker run --rm --entrypoint promtool \
  --mount "type=bind,source=$PWD/prometheus.yml,target=/etc/prometheus/prometheus.yml,readonly" \
  --mount "type=bind,source=$PWD/observability/prometheus/cloudops-rules.yml,target=/etc/prometheus/cloudops-rules.yml,readonly" \
  "$PROMETHEUS_IMAGE" check config /etc/prometheus/prometheus.yml
docker run -d --name cloudops-blackbox --restart unless-stopped --network host \
  --mount "type=bind,source=$PWD/observability/prometheus/blackbox.example.yml,target=/etc/blackbox.yml,readonly" \
  "$BLACKBOX_IMAGE" --config.file=/etc/blackbox.yml --web.listen-address=127.0.0.1:9115
docker run -d --name cloudops-prometheus --restart unless-stopped --network host \
  --mount "type=bind,source=$PWD/prometheus.yml,target=/etc/prometheus/prometheus.yml,readonly" \
  --mount "type=bind,source=$PWD/observability/prometheus/cloudops-rules.yml,target=/etc/prometheus/cloudops-rules.yml,readonly" \
  --mount type=volume,source=apexforge_prometheus,target=/prometheus \
  "$PROMETHEUS_IMAGE" --config.file=/etc/prometheus/prometheus.yml \
  --web.listen-address=127.0.0.1:9090 --storage.tsdb.retention.time=7d
docker run -d --name cloudops-loki --restart unless-stopped --network host \
  --mount "type=bind,source=$PWD/loki.yml,target=/etc/loki/loki.yml,readonly" \
  --mount type=volume,source=apexforge_loki,target=/loki \
  "$LOKI_IMAGE" -config.file=/etc/loki/loki.yml
docker run -d --name cloudops-grafana --restart unless-stopped --network host \
  --env GF_SERVER_HTTP_ADDR=127.0.0.1 \
  --mount type=volume,source=apexforge_grafana,target=/var/lib/grafana "$GRAFANA_IMAGE"
```

Before the Loki start command above, save this single-node example as `/opt/apexforge-monitoring/loki.yml` and check compatibility against your reviewed Loki3 release:

```yaml
auth_enabled: false
server:
  http_listen_address: 0.0.0.0
  http_listen_port: 3100
common:
  instance_addr: 127.0.0.1
  path_prefix: /loki
  replication_factor: 1
  ring:
    kvstore:
      store: inmemory
  storage:
    filesystem:
      chunks_directory: /loki/chunks
      rules_directory: /loki/rules
schema_config:
  configs:
    - from: '2024-01-01'
      store: tsdb
      object_store: filesystem
      schema: v13
      index:
        prefix: index_
        period: 24h
compactor:
  working_directory: /loki/compactor
  retention_enabled: true
  delete_request_store: filesystem
limits_config:
  retention_period: 168h
analytics:
  reporting_enabled: false
```

The observability host has no public IP; SG3100 admits only APP_SG. The all-interface listener includes localhost for Grafana and the private interface for Alloy. Check filesystem ownership for the reviewed image's non-root UID; initialize only the owned volume using that UID, never chmod777. This trusted-VPC ingestion example is HTTP without application authentication. For confidential environments configure authenticated TLS/reverse proxy and adapt the collector URL through a reviewed change; do not claim this example has mTLS. Verify disk/compactor retention with your pinned version.

Access Grafana through an SSM3000 tunnel; change initial credentials immediately. Add Prometheus URL `http://127.0.0.1:9090` and Loki URL `http://127.0.0.1:3100` when Loki also listens on loopback, otherwise its private address. Import `observability/grafana/cloudops-dashboard.json`; map `${DS_PROMETHEUS}`/`${DS_LOKI}` to the actual datasource UIDs. Provisioning examples allow preserving existing UIDs rather than deleting dashboards. Configure alert routes/contact points privately, then inject a disposable readiness failure and verify notifications end to end.

Optional app logs: **before the first application container**, on the clean app host configure Docker's reviewed daemon logging default as `journald` with tag `cloudops`. Do not overwrite existing daemon settings; inspect and merge privately, restart Docker only on the empty owned lab host. New containers inherit defaults; existing rollback containers retain their original configuration. Install a pinned verified [Alloy Linux release](https://grafana.com/docs/alloy/latest/set-up/install/linux/), place its binary at `/usr/local/bin/alloy` with755 permissions and validate the config. Then, after successful app deployment:

```bash
sudo env CLOUDOPS_CONTAINER=cloudops-app CLOUDOPS_ENVIRONMENT=lab \
  LOKI_PRIVATE_IP=10.0.12.10 bash /opt/apexforge360/observability/alloy/bootstrap.sh
```

Replace `10.0.12.10` with the verified monitoring private IP. The helper checks exact container/journal configuration before enabling the collector. It retains fixed endpoint/UUID summaries and drops raw lines, arbitrary paths and user values. Run it again after each successful release to refresh image labels; it does not redeploy the app. The main Jenkins workflow does not silently install or change monitoring.

## Expected output and validation

```bash
curl --fail http://127.0.0.1:9090/-/ready
curl --fail http://127.0.0.1:3000/api/health
# Use the actual Loki listen address:
curl --fail http://10.0.12.10:3100/ready
```

Grafana shows one app target, request rates/latency and readiness probe state. Verify controlled log correlation, no secret/private labels, correct clock and retention. Repository `observability/tests/validate_assets.py` checks fixture privacy/config syntax, not live alerts.

## Troubleshooting and root causes

Empty graphs: wrong private IP, SG, job/label mismatch or scrape path. Logs absent: journald tag/Alloy filter mismatch, unverified binary, wrong Loki IP or schema permissions. Blackbox failures can be service, database, network or probe config; inspect separately. A healthy Prometheus process does not prove app readiness.

## Security considerations

No public9090/3000/3100/9115. Persist only owned volumes; bound log fields/metric cardinality. Protect dashboards and credentials; an alert is useful only after delivery is tested.

## Cleanup

Stop only named owned lab services after evidence export. Preserve volumes until backup/retention review. Do not prune shared Docker state or wipe an existing Grafana/Loki stack.

## Interview questions with answers

**Why bounded labels?** Unbounded paths/user IDs cause memory/cardinality growth and expose private data.
**Why Blackbox plus app metrics?** Metrics characterize requests; a readiness probe independently tests the actual service response.
