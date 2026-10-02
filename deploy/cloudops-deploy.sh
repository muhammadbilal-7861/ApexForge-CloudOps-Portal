#!/usr/bin/env bash
set -Eeuo pipefail
exec > >(tee -a "${CLOUDOPS_DEPLOY_LOG:-/var/log/cloudops-deploy.log}") 2>&1

readonly AWS_REGION="eu-north-1"
readonly ECR_REGISTRY="489502663059.dkr.ecr.eu-north-1.amazonaws.com"
readonly RUNTIME_ENV="${CLOUDOPS_RUNTIME_ENV:-/etc/cloudops/runtime.env}"
readonly CONTAINER_NAME="cloudops-app"
readonly LEGACY_IMAGE="cloudops-flask:1.1"
readonly VERIFY_SCRIPT="${CLOUDOPS_VERIFY_SCRIPT:-/usr/local/sbin/cloudops-verify.sh}"

log() { printf '[cloudops-deploy] %s\n' "$*"; }
fail() { printf '[cloudops-deploy] ERROR: %s\n' "$*" >&2; exit 1; }
container_exists() { docker inspect "$1" >/dev/null 2>&1; }

if ((EUID != 0)); then
    fail 'run this deployment as root through SSM or cloud-init.'
fi
if (($# < 2 || $# > 6)); then
    printf 'Usage: %s ECR_URI@sha256:DIGEST APP_VERSION [--target-group-arn ARN --instance-id ID]\n' "$0" >&2
    exit 2
fi

image_uri="$1"
app_version="$2"
shift 2
target_group_arn=""
instance_id=""
while (($#)); do
    case "$1" in
        --target-group-arn) target_group_arn="${2:?missing target group ARN}"; shift 2 ;;
        --instance-id) instance_id="${2:?missing instance ID}"; shift 2 ;;
        *) printf 'Unknown deployment option: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [[ ! "$image_uri" =~ ^489502663059\.dkr\.ecr\.eu-north-1\.amazonaws\.com/apexforge-cloudops-portal@sha256:[0-9a-f]{64}$ ]]; then
    fail 'image must be this account/repository pinned to a SHA256 digest.'
fi
if [[ ! "$app_version" =~ ^[0-9a-f]{12,40}$ ]]; then
    fail 'application version must be a Git commit SHA.'
fi
if [[ -n "$target_group_arn" || -n "$instance_id" ]]; then
    [[ "$target_group_arn" == arn:aws:elasticloadbalancing:eu-north-1:489502663059:targetgroup/tg-cloudops-app/* ]] || fail 'unexpected ALB target group.'
    [[ "$instance_id" =~ ^i-[0-9a-f]{8,17}$ ]] || fail 'an EC2 instance ID is required for ALB verification.'
fi
if [[ ! -f "$RUNTIME_ENV" ]]; then
    fail "$RUNTIME_ENV is missing. Provision the approved runtime environment before deploying."
fi

runtime_owner_mode="$(stat -c '%U:%G:%a' "$RUNTIME_ENV")"
[[ "$runtime_owner_mode" == root:root:600 ]] || fail "$RUNTIME_ENV must be owned by root:root with mode 0600 (found $runtime_owner_mode)."
python3 - "$RUNTIME_ENV" <<'PY'
import sys

allowed = {
    "FLASK_ENV", "SECRET_KEY", "SESSION_COOKIE_SECURE", "USE_AWS_SECRETS",
    "AWS_SECRET_NAME", "AWS_REGION", "S3_BUCKET_NAME", "ENABLE_LAB_FAILURE_ENDPOINTS", "APP_VERSION",
}
values = {}
with open(sys.argv[1], encoding="utf-8") as source:
    for raw in source:
        line = raw.rstrip("\n")
        if not line or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or key not in allowed or key in values or "\r" in line:
            raise SystemExit("runtime.env has invalid syntax, a duplicate, or an unapproved key")
        values[key] = value
required = {
    "FLASK_ENV": "production",
    "SESSION_COOKIE_SECURE": "false",
    "USE_AWS_SECRETS": "true",
    "AWS_SECRET_NAME": "cloudops/prod/mariadb",
    "AWS_REGION": "eu-north-1",
    "ENABLE_LAB_FAILURE_ENDPOINTS": "false",
}
if any(values.get(key) != value for key, value in required.items()):
    raise SystemExit("runtime.env is missing a required setting or enables an unsafe setting")
key = values.get("SECRET_KEY", "")
if len(key) < 32 or "\n" in key or "\r" in key:
    raise SystemExit("runtime.env does not contain a valid stable session signing key")
print("Approved runtime environment shape validated.")
PY

for required_command in aws docker curl stat python3 tee date mktemp; do
    command -v "$required_command" >/dev/null 2>&1 || fail "required command not installed: $required_command"
done
[[ -x "$VERIFY_SCRIPT" ]] || fail "$VERIFY_SCRIPT is not installed; obtain the deployment scripts from the approved repository commit first."

# Prove the installed signing key is the stable Secrets Manager value without writing or logging either value.
aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id cloudops/prod/flask-session-key --query SecretString --output text |
    python3 -c 'import sys; p=sys.argv[1]; f=open(p,encoding="utf-8"); key=next((line.rstrip("\n").partition("=")[2] for line in f if line.startswith("SECRET_KEY=")),""); secret=sys.stdin.read(); secret=secret[:-1] if secret.endswith("\n") else secret; valid=bool(secret) and "\n" not in secret and "\r" not in secret and len(secret)>=32 and secret==key; print("Stable session signing secret validated.") if valid else sys.exit(1)' "$RUNTIME_ENV" ||
    fail 'runtime.env signing key does not match the stable Secrets Manager secret.'
log 'Stable session signing secret validated without printing its value.'

aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id cloudops/prod/mariadb --query SecretString --output text |
    python3 -c 'import json,sys; d=json.load(sys.stdin); ok=all(isinstance(d.get(k),str) and d[k] for k in ("host","username","password")) and isinstance(d.get("dbname",d.get("database")),str) and bool(d.get("dbname",d.get("database"))); port=int(d.get("port",3306)); sys.exit(0 if ok and 0 < port < 65536 else 1)'
log 'Database secret schema validated without persisting or printing its value.'

log 'Checking the pre-created CloudWatch log group and recording a complete container inventory.'
log_group="$(aws logs describe-log-groups --region "$AWS_REGION" --log-group-name-prefix /cloudops/app --query "logGroups[?logGroupName=='/cloudops/app'].logGroupName | [0]" --output text)"
[[ "$log_group" == /cloudops/app ]] || fail 'pre-created /cloudops/app log group is unavailable to the EC2 role.'
docker ps -a --format 'container name={{.Names}} image={{.Image}}'

previous_id=""
previous_name=""
previous_image=""
previous_version=""
previous_kind="none"
if container_exists "$CONTAINER_NAME"; then
    service_label="$(docker inspect --format '{{index .Config.Labels "service"}}' "$CONTAINER_NAME")"
    [[ "$service_label" == cloudops ]] || fail "container name $CONTAINER_NAME is occupied by an unrecognized workload; it was not changed."
    previous_id="$(docker inspect --format '{{.Id}}' "$CONTAINER_NAME")"
    previous_name="$CONTAINER_NAME"
    previous_image="$(docker inspect --format '{{.Config.Image}}' "$previous_id")"
    previous_version="$(docker inspect --format '{{index .Config.Labels "org.apexforge.version"}}' "$previous_id")"
    [[ "$(docker inspect --format '{{.State.Running}}' "$previous_id")" == true ]] || fail 'managed prior container is stopped; inspect it before retrying.'
    [[ "$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$previous_id")" == host ]] || fail 'managed prior container is not using host networking.'
    docker inspect --format 'inventoried prior managed container name={{.Name}} image={{.Config.Image}} mounts={{json .Mounts}}' "$previous_id"
    previous_kind=managed
else
    mapfile -t legacy_ids < <(docker ps --quiet --filter "ancestor=$LEGACY_IMAGE" --filter status=running)
    if ((${#legacy_ids[@]} > 1)); then
        fail "multiple running containers use $LEGACY_IMAGE; refusing to guess which one serves port 5000."
    elif ((${#legacy_ids[@]} == 1)); then
        previous_id="${legacy_ids[0]}"
        previous_name="$(docker inspect --format '{{.Name}}' "$previous_id")"
        previous_name="${previous_name#/}"
        previous_image="$(docker inspect --format '{{.Config.Image}}' "$previous_id")"
        [[ "$previous_image" == "$LEGACY_IMAGE" ]] || fail "legacy container's exact image differs from $LEGACY_IMAGE."
        [[ "$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$previous_id")" == host ]] || fail 'legacy container is not using host networking.'
        docker inspect --format 'inventoried prior legacy container name={{.Name}} image={{.Config.Image}} mounts={{json .Mounts}}' "$previous_id"
        previous_kind=legacy
    fi
fi

if [[ -n "$target_group_arn" && -z "$previous_id" ]]; then
    fail 'canary cutover requires the existing managed container or exact inventoried cloudops-flask:1.1 container.'
fi

if [[ "$previous_kind" == legacy ]]; then
    prior_health="$(curl --connect-timeout 3 --max-time 8 --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:5000/health || true)"
    prior_ready="$(curl --connect-timeout 3 --max-time 8 --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:5000/ready || true)"
    [[ "$prior_health" == 200 && "$prior_ready" == 200 ]] || fail 'legacy container is not healthy and ready on port 5000; leaving it untouched.'
elif [[ "$previous_kind" == managed ]]; then
    [[ "$previous_image" =~ ^489502663059\.dkr\.ecr\.eu-north-1\.amazonaws\.com/apexforge-cloudops-portal@sha256:[0-9a-f]{64}$ ]] || fail 'managed prior image is not an immutable approved ECR digest.'
    [[ "$previous_version" =~ ^[0-9a-f]{12,40}$ ]] || fail 'managed prior container has no valid version label.'
    "$VERIFY_SCRIPT" --expected-image "$previous_image" --expected-version "$previous_version" \
        --container-name "$CONTAINER_NAME" --port 5000 --require-container-health --wait-seconds 0
fi

python3 -c 'import socket,sys; s=socket.socket();
try: s.bind(("0.0.0.0",int(sys.argv[1])))
except OSError: sys.exit(1)
finally: s.close()' 5001 || fail 'localhost port 5001 is already in use; candidate was not started and the existing app was left untouched.'

candidate_name="cloudops-candidate-${app_version:0:12}"
candidate_retained="cloudops-verified-${app_version:0:12}-$(date -u +%Y%m%d%H%M%S)-$$"
failed_candidate="cloudops-failed-candidate-${app_version:0:12}-$(date -u +%Y%m%d%H%M%S)-$$"
for name in "$candidate_name" "$candidate_retained" "$failed_candidate"; do
    container_exists "$name" && fail "container name $name already exists; inspect it manually; it was not changed."
done

docker_config="$(mktemp -d /run/cloudops-docker-config.XXXXXX)"
chmod 700 "$docker_config"
trap 'rm -rf -- "$docker_config"' EXIT
export DOCKER_CONFIG="$docker_config"
set +x
if ! aws ecr get-login-password --region "$AWS_REGION" | docker login --username AWS --password-stdin "$ECR_REGISTRY" >/dev/null; then
    fail 'EC2 role ECR authentication failed; the existing port-5000 service remains untouched.'
fi
if ! docker pull "$image_uri"; then
    fail 'EC2 role could not pull the pinned ECR image; the existing port-5000 service remains untouched.'
fi

log "Starting the digest-pinned candidate on localhost port 5001; current port 5000 remains untouched."
candidate_id="$(docker run --detach \
    --name "$candidate_name" \
    --network host \
    --restart unless-stopped \
    --label service=cloudops-candidate \
    --label "org.apexforge.version=$app_version" \
    --env-file "$RUNTIME_ENV" \
    --env "APP_VERSION=$app_version" \
    --env PORT=5001 \
    --health-cmd 'python -c "import urllib.request; urllib.request.urlopen(\"http://127.0.0.1:5001/health\",timeout=2)"' \
    --health-interval 10s --health-timeout 3s --health-start-period 15s --health-retries 5 \
    "$image_uri" \
    gunicorn --workers 2 --bind 127.0.0.1:5001 --access-logfile - --error-logfile - wsgi:app)" || {
        fail 'candidate container could not start on port 5001; the existing port-5000 container remains untouched.'
    }

if ! "$VERIFY_SCRIPT" --expected-image "$image_uri" --expected-version "$app_version" \
    --container-name "$candidate_name" --port 5001 --require-container-health \
    --wait-seconds "${CLOUDOPS_CANDIDATE_WAIT_SECONDS:-180}"; then
    docker inspect --format 'failed candidate name={{.Name}} image={{.Config.Image}} mounts={{json .Mounts}}' "$candidate_id" || true
    docker stop --time 10 "$candidate_id" >/dev/null 2>&1 || true
    docker rename "$candidate_id" "$failed_candidate" >/dev/null 2>&1 || true
    fail 'candidate /health, /ready, Docker healthcheck, or API route verification failed; previous port-5000 service remains running.'
fi

docker stop --time 10 "$candidate_id" >/dev/null
docker rename "$candidate_id" "$candidate_retained"
log "Candidate passed /health and /ready on port 5001 and was retained stopped as $candidate_retained."

rollback_name="cloudops-rollback-${app_version:0:12}-$(date -u +%Y%m%d%H%M%S)-$$"
production_id=""
previous_moved=false

restore_previous() {
    local current_id current_label current_image restored=false
    set +e
    if container_exists "$CONTAINER_NAME"; then
        current_id="$(docker inspect --format '{{.Id}}' "$CONTAINER_NAME")"
        current_label="$(docker inspect --format '{{index .Config.Labels "service"}}' "$current_id")"
        current_image="$(docker inspect --format '{{.Config.Image}}' "$current_id")"
        if [[ "$current_id" == "$previous_id" && "$previous_name" == "$CONTAINER_NAME" ]]; then
            log 'The original managed container still owns cloudops-app; preserving it and restoring its running state if needed.'
        elif [[ -n "$production_id" && "$current_id" == "$production_id" && "$current_label" == cloudops && "$current_image" == "$image_uri" ]]; then
            docker inspect --format 'failed production candidate name={{.Name}} image={{.Config.Image}} mounts={{json .Mounts}}' "$current_id"
            docker stop --time 10 "$current_id" >/dev/null
            docker rename "$current_id" "cloudops-failed-${app_version:0:12}-$(date -u +%Y%m%d%H%M%S)-$$"
        else
            log 'Refusing rollback mutation: cloudops-app no longer identifies this invocation\047s exact new container.' >&2
            set -e
            return 1
        fi
    fi
    if [[ -n "$previous_id" ]] && container_exists "$previous_id"; then
        local actual_name
        actual_name="$(docker inspect --format '{{.Name}}' "$previous_id")"
        actual_name="${actual_name#/}"
        if [[ "$actual_name" == "$rollback_name" || "$actual_name" == "$previous_name" ]]; then
            if [[ "$actual_name" == "$rollback_name" ]]; then
                docker rename "$previous_id" "$previous_name"
            fi
            if [[ "$(docker inspect --format '{{.State.Running}}' "$previous_id")" != true ]]; then
                docker start "$previous_id" >/dev/null
            fi
            if [[ "$(docker inspect --format '{{.State.Running}}' "$previous_id")" != true \
                || "$(docker inspect --format '{{.Config.Image}}' "$previous_id")" != "$previous_image" \
                || "$(docker inspect --format '{{.HostConfig.NetworkMode}}' "$previous_id")" != host ]]; then
                log 'CRITICAL: prior container identity or running state did not recover after rollback.' >&2
                set -e
                return 1
            fi
            restored=true
        fi
    fi
    if [[ "$restored" == true ]]; then
        local old_health old_ready
        old_health="$(curl --connect-timeout 3 --max-time 8 --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:5000/health || true)"
        old_ready="$(curl --connect-timeout 3 --max-time 8 --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:5000/ready || true)"
        if [[ "$old_health" == 200 && "$old_ready" == 200 ]]; then
            if [[ -n "$target_group_arn" ]]; then
                local target_deadline=$((SECONDS + 300)) restored_target=unknown
                while ((SECONDS < target_deadline)); do
                    restored_target="$(aws elbv2 describe-target-health --region "$AWS_REGION" \
                        --target-group-arn "$target_group_arn" --targets "Id=$instance_id,Port=5000" \
                        --query 'TargetHealthDescriptions[0].TargetHealth.State' --output text)"
                    if [[ "$restored_target" == healthy ]]; then break; fi
                    sleep 10
                done
                if [[ "$restored_target" != healthy ]]; then
                    log "CRITICAL: restored service did not return to a healthy ALB target state (state=$restored_target)." >&2
                    set -e
                    return 1
                fi
            fi
            log 'Previous container restored and /health plus /ready returned 200; prior image and container were retained.'
            set -e
            return 0
        fi
        log "CRITICAL: previous container restarted but health/readiness are $old_health/$old_ready; immediate operator investigation required." >&2
    else
        log 'No prior container was present to restore; the failed production candidate was retained for investigation.' >&2
    fi
    set -e
    return 1
}

if [[ -n "$previous_id" ]]; then
    actual_id="$(docker inspect --format '{{.Id}}' "$previous_name")"
    [[ "$actual_id" == "$previous_id" ]] || fail 'prior container identity changed after candidate verification; refusing cutover.'
    docker stop --time 20 "$previous_id" >/dev/null || { restore_previous || true; fail 'could not stop the exact inventoried prior container.'; }
    if ! docker rename "$previous_id" "$rollback_name"; then
        restore_previous || true
        fail 'could not retain the stopped previous container under its rollback name.'
    fi
    previous_moved=true
    log "Exact prior container $previous_name/$previous_id is stopped and retained as $rollback_name."
fi

if ! production_id="$(docker run --detach \
    --name "$CONTAINER_NAME" \
    --network host \
    --restart unless-stopped \
    --label service=cloudops \
    --label "org.apexforge.version=$app_version" \
    --env-file "$RUNTIME_ENV" \
    --env "APP_VERSION=$app_version" \
    "$image_uri")"; then
    if container_exists "$CONTAINER_NAME"; then
        failed_image="$(docker inspect --format '{{.Config.Image}}' "$CONTAINER_NAME")"
        failed_service="$(docker inspect --format '{{index .Config.Labels "service"}}' "$CONTAINER_NAME")"
        if [[ "$failed_image" == "$image_uri" && "$failed_service" == cloudops ]]; then
            production_id="$(docker inspect --format '{{.Id}}' "$CONTAINER_NAME")"
        fi
    fi
    restore_previous || true
    fail 'production container could not start on port 5000; rollback was attempted and prior container/image were retained.'
fi

verify_args=(
    --expected-image "$image_uri"
    --expected-version "$app_version"
    --container-name "$CONTAINER_NAME"
    --port 5000
    --require-container-health
    --wait-seconds "${CLOUDOPS_PRODUCTION_WAIT_SECONDS:-180}"
)
if [[ -n "$target_group_arn" ]]; then
    verify_args+=(--instance-id "$instance_id" --target-group-arn "$target_group_arn" --require-target-healthy --wait-seconds "${CLOUDOPS_ALB_WAIT_SECONDS:-300}")
fi
if ! "$VERIFY_SCRIPT" "${verify_args[@]}"; then
    restore_previous || true
    fail 'production health/readiness, Docker healthcheck, API, or required ALB verification failed; rollback was attempted.'
fi

log "Canary cutover completed for version $app_version on port 5000 using the pinned ECR digest."
if [[ "$previous_moved" == true ]]; then
    log "Previous image/container remains stopped as $rollback_name for manual rollback/retirement after review."
fi
