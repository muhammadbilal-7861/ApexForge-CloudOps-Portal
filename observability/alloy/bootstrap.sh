#!/usr/bin/env bash
set -euo pipefail
# Run after the app starts; rerun on each image rollout. Does not recreate the app.
[ "$(id -u)" = 0 ] || { echo 'Run as root'; exit 1; }
: "${CLOUDOPS_CONTAINER:?exact CloudOps container name required}"
: "${CLOUDOPS_ENVIRONMENT:?environment required}"
: "${LOKI_PRIVATE_IP:?confirm current monitoring private IP using operator DescribeInstances}"
[[ "$LOKI_PRIVATE_IP" =~ ^10\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 1
[[ "$CLOUDOPS_ENVIRONMENT" =~ ^[A-Za-z0-9_-]+$ ]] || exit 1
command -v /usr/local/bin/alloy >/dev/null
getent passwd alloy >/dev/null || useradd --system --no-create-home --shell /usr/sbin/nologin alloy
usermod -aG systemd-journal alloy
here=$(cd -- "$(dirname -- "$0")" && pwd)
image=$(docker inspect --format '{{.Config.Image}}' "$CLOUDOPS_CONTAINER")
[[ "$image" =~ ^[A-Za-z0-9./:@_-]+$ ]] || exit 1
[ "$(docker inspect --format '{{.HostConfig.LogConfig.Type}}' "$CLOUDOPS_CONTAINER")" = journald ] || { echo 'App must use journald with tag cloudops'; exit 1; }
[ "$(docker inspect --format '{{index .HostConfig.LogConfig.Config "tag"}}' "$CLOUDOPS_CONTAINER")" = cloudops ] || exit 1
token=$(curl --fail --silent --show-error --max-time 5 -X PUT -H 'X-aws-ec2-metadata-token-ttl-seconds: 60' http://169.254.169.254/latest/api/token)
iid=$(curl --fail --silent --show-error --max-time 5 -H "X-aws-ec2-metadata-token: $token" http://169.254.169.254/latest/meta-data/instance-id)
[[ "$iid" =~ ^i-[0-9a-f]+$ ]] || exit 1
curl --fail --silent --show-error --max-time 5 "http://$LOKI_PRIVATE_IP:3100/ready" >/dev/null
install -d -m 0755 /etc/cloudops-alloy
install -m 0644 "$here/config.alloy" /etc/cloudops-alloy/config.alloy
printf 'EC2_INSTANCE_ID=%s\nCLOUDOPS_ENVIRONMENT=%s\nCLOUDOPS_IMAGE_VERSION=%s\nLOKI_PUSH_URL=http://%s:3100/loki/api/v1/push\n' "$iid" "$CLOUDOPS_ENVIRONMENT" "$image" "$LOKI_PRIVATE_IP" > /etc/cloudops-alloy/runtime.env
chmod 0644 /etc/cloudops-alloy/runtime.env
set -a
# shellcheck source=/dev/null
source /etc/cloudops-alloy/runtime.env
set +a
/usr/local/bin/alloy validate /etc/cloudops-alloy/config.alloy
install -m 0644 "$here/cloudops-alloy.service" /etc/systemd/system/cloudops-alloy.service
systemctl daemon-reload
systemctl enable cloudops-alloy
systemctl restart cloudops-alloy
