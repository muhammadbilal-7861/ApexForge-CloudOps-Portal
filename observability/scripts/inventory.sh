#!/usr/bin/env bash
set -euo pipefail
# Read-only, no environment/secret dumps. Keep output private until reviewed.
[ "$(id -u)" = 0 ] || { echo 'Run via sudo in an SSM session'; exit 1; }
printf '%s\n' '=== compose projects ==='
docker compose ls
printf '%s\n' '=== containers: identity, image, project, files, mounts, ports ==='
for c in $(docker ps -aq); do
 docker inspect --format '{{.Name}} image={{.Config.Image}} imageID={{.Image}} project={{index .Config.Labels "com.docker.compose.project"}} files={{index .Config.Labels "com.docker.compose.project.config_files"}} service={{index .Config.Labels "com.docker.compose.service"}} mounts={{json .Mounts}} ports={{json .NetworkSettings.Ports}} network={{.HostConfig.NetworkMode}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c"
done
printf '%s\n' '=== persistent volume metadata ==='
docker volume ls
for v in $(docker volume ls -q); do docker volume inspect "$v"; done
printf '%s\n' '=== configuration paths only; review contents privately ==='
find /opt -maxdepth 5 -type f \( -name '*compose*' -o -name '*prometheus*' -o -name '*loki*' -o -name '*grafana*' -o -name '*alertmanager*' \) -print
ss -lntp
for u in http://127.0.0.1:3000/api/health http://127.0.0.1:9090/-/ready http://127.0.0.1:3100/ready http://127.0.0.1:9093/-/ready; do
 printf '%s ' "$u"; curl --max-time 5 -sS -o /dev/null -w '%{http_code}\n' "$u" || true
done
