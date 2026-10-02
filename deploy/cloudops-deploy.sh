#!/usr/bin/env bash
set -Eeuo pipefail
exec > >(tee -a /var/log/cloudops-deploy.log) 2>&1

readonly AWS_REGION="eu-north-1"
readonly ECR_REGISTRY="489502663059.dkr.ecr.eu-north-1.amazonaws.com"
readonly RUNTIME_ENV="/etc/cloudops/runtime.env"
readonly OLD_CONTAINER_ALLOWLIST="/etc/cloudops/old-containers.allowlist"
readonly CONTAINER_NAME="cloudops-app"
readonly VERIFY_SCRIPT="/usr/local/sbin/cloudops-verify.sh"

log() { printf '[cloudops-deploy] %s\n' "$*"; }
fail() { printf '[cloudops-deploy] ERROR: %s\n' "$*" >&2; exit 1; }

if ((EUID != 0)); then
    fail 'run this deployment as root through SSM or cloud-init.'
fi
if (($# != 2)); then
    printf 'Usage: %s ECR_URI@sha256:DIGEST APP_VERSION\n' "$0" >&2
    exit 2
fi

image_uri="$1"
app_version="$2"
if [[ ! "$image_uri" =~ ^489502663059\.dkr\.ecr\.eu-north-1\.amazonaws\.com/apexforge-cloudops-portal@sha256:[0-9a-f]{64}$ ]]; then
    fail 'image must be this account/repository pinned to a SHA256 digest.'
fi
if [[ ! "$app_version" =~ ^[0-9a-f]{12,40}$ ]]; then
    fail 'application version must be a Git commit SHA.'
fi
if [[ ! -f "$RUNTIME_ENV" ]]; then
    fail "$RUNTIME_ENV is missing. Provision the approved runtime environment before deploying."
fi

runtime_owner_mode="$(stat -c '%U:%G:%a' "$RUNTIME_ENV")"
if [[ "$runtime_owner_mode" != root:root:600 ]]; then
    fail "$RUNTIME_ENV must be owned by root:root with mode 0600 (found $runtime_owner_mode)."
fi
if ! awk -F= '
    /^[[:space:]]*$/ || /^[[:space:]]*#/ { next }
    $1 !~ /^[A-Z_][A-Z0-9_]*$/ { exit 1 }
    $1 !~ /^(FLASK_ENV|SECRET_KEY|SESSION_COOKIE_SECURE|USE_AWS_SECRETS|AWS_SECRET_NAME|AWS_REGION|S3_BUCKET_NAME|ENABLE_LAB_FAILURE_ENDPOINTS|APP_VERSION)$/ { exit 1 }
    END { if (NR == 0) exit 1 }
' "$RUNTIME_ENV"; then
    fail 'runtime.env contains invalid syntax or an unapproved key; only reviewed application variables are allowed.'
fi
for required_line in 'FLASK_ENV=production' 'SESSION_COOKIE_SECURE=false' 'USE_AWS_SECRETS=true' "AWS_SECRET_NAME=cloudops/prod/mariadb" "AWS_REGION=$AWS_REGION" 'ENABLE_LAB_FAILURE_ENDPOINTS=false'; do
    if ! grep -Fqx "$required_line" "$RUNTIME_ENV"; then
        fail 'runtime.env is missing a required production setting or enables an unsafe setting.'
    fi
done
if ! grep -Eq '^SECRET_KEY=.{32,}$' "$RUNTIME_ENV"; then
    fail 'runtime.env must contain the stable SECRET_KEY provisioned from its authorized secret source.'
fi

if [[ ! -x "$VERIFY_SCRIPT" ]]; then
    fail "$VERIFY_SCRIPT is not installed; obtain the deployment scripts from the approved repository commit first."
fi
for required_command in aws docker curl stat awk grep; do
    command -v "$required_command" >/dev/null 2>&1 || fail "required command not installed: $required_command"
done

log 'Inventorying Docker container names and images before any container mutation.'
docker ps -a --format 'container name={{.Names}} image={{.Image}}'

# An operator may populate this allowlist only after inventorying a specific old container's name, image and mounts.
# Format: one TAB-separated name and exact image reference per line. No volumes are ever removed.
if [[ -e "$OLD_CONTAINER_ALLOWLIST" ]]; then
    allowlist_owner_mode="$(stat -c '%U:%G:%a' "$OLD_CONTAINER_ALLOWLIST")"
    [[ "$allowlist_owner_mode" == root:root:600 ]] || fail "$OLD_CONTAINER_ALLOWLIST must be root-owned mode 0600."
    while IFS=$'\t' read -r old_name expected_old_image extra || [[ -n "${old_name:-}" ]]; do
        [[ -z "${old_name:-}" || "$old_name" == \#* ]] && continue
        [[ -z "${extra:-}" && "$old_name" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ && -n "${expected_old_image:-}" ]] || fail 'invalid old-container allowlist entry.'
        [[ "$old_name" != "$CONTAINER_NAME" ]] || fail "do not put the active $CONTAINER_NAME in the old-container allowlist."
        old_id="$(docker inspect --type=container --format '{{.Id}}' "$old_name")" || fail "allowlisted container not found: $old_name"
        actual_old_image="$(docker inspect --format '{{.Config.Image}}' "$old_id")"
        [[ "$actual_old_image" == "$expected_old_image" ]] || fail "allowlisted image mismatch for $old_name; refusing to stop it."
        docker inspect --format 'inventoried old CloudOps container name={{.Name}} image={{.Config.Image}} mounts={{json .Mounts}}' "$old_id"
        old_running="$(docker inspect --format '{{.State.Running}}' "$old_id")"
        if [[ "$old_running" == true ]]; then
            docker stop --time 20 "$old_id" >/dev/null
        fi
        docker rm "$old_id" >/dev/null
        log "Removed explicitly allowlisted old CloudOps container $old_name; its volumes were retained."
    done < "$OLD_CONTAINER_ALLOWLIST"
fi

docker_config="$(mktemp -d /run/cloudops-docker-config.XXXXXX)"
chmod 700 "$docker_config"
cleanup_docker_config() { rm -rf -- "$docker_config"; }
trap cleanup_docker_config EXIT
export DOCKER_CONFIG="$docker_config"
set +x
aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY" >/dev/null
docker pull "$image_uri"

previous_id=""
previous_name=""
previous_was_running=false
if docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    service_label="$(docker inspect --format '{{index .Config.Labels "service"}}' "$CONTAINER_NAME")"
    [[ "$service_label" == cloudops ]] || fail "container name $CONTAINER_NAME is occupied by an unrecognized workload; it was not changed."
    docker inspect --format 'inventoried managed container name={{.Name}} image={{.Config.Image}} mounts={{json .Mounts}}' "$CONTAINER_NAME"
    current_image="$(docker inspect --format '{{.Config.Image}}' "$CONTAINER_NAME")"
    current_running="$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME")"
    if [[ "$current_image" == "$image_uri" && "$current_running" == true ]]; then
        log 'The requested digest is already running; verifying it without replacing the container.'
        "$VERIFY_SCRIPT" --expected-image "$image_uri" --expected-version "$app_version"
        exit 0
    fi
    if [[ "$current_running" != true ]]; then
        fail 'managed container exists but is stopped; inspect it and its rollback containers before retrying.'
    fi

    previous_id="$(docker inspect --format '{{.Id}}' "$CONTAINER_NAME")"
    previous_name="cloudops-rollback-${app_version:0:12}-$(date -u +%Y%m%d%H%M%S)"
    previous_was_running=true
    docker stop --time 20 "$previous_id" >/dev/null
    docker rename "$previous_id" "$previous_name"
fi

restore_previous() {
    local failed_name
    set +e
    if docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
        docker inspect --format 'inventoried failed candidate name={{.Name}} image={{.Config.Image}} mounts={{json .Mounts}}' "$CONTAINER_NAME"
        docker stop --time 10 "$CONTAINER_NAME" >/dev/null 2>&1
        failed_name="cloudops-failed-${app_version:0:12}-$(date -u +%Y%m%d%H%M%S)"
        docker rename "$CONTAINER_NAME" "$failed_name"
    fi
    if [[ -n "$previous_id" ]] && docker inspect "$previous_id" >/dev/null 2>&1; then
        docker rename "$previous_id" "$CONTAINER_NAME"
        if [[ "$previous_was_running" == true ]]; then
            docker start "$CONTAINER_NAME" >/dev/null
        fi
        log 'Previous managed container restored; no image or volume was removed.'
    else
        log 'No prior managed container existed; failed candidate, if any, was stopped and retained for investigation.'
    fi
    set -e
}

if ! docker run --detach \
    --name "$CONTAINER_NAME" \
    --network host \
    --restart unless-stopped \
    --label service=cloudops \
    --label "org.apexforge.version=$app_version" \
    --env-file "$RUNTIME_ENV" \
    --env "APP_VERSION=$app_version" \
    "$image_uri"; then
    restore_previous
    fail 'new container could not start; previous container rollback was attempted.'
fi

if ! "$VERIFY_SCRIPT" --expected-image "$image_uri" --expected-version "$app_version"; then
    restore_previous
    fail 'health/version verification failed; previous container rollback was attempted. Readiness is never bypassed.'
fi

log "Deployment verified for version $app_version using the pinned ECR image digest."
if [[ -n "$previous_id" ]]; then
    log "Previous container retained stopped as $previous_name for manual rollback/retirement after review."
fi
