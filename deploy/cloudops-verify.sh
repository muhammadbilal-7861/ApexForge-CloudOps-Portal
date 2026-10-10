#!/usr/bin/env bash
set -Eeuo pipefail
set +x
: "${AWS_REGION:?}" "${AWS_ACCOUNT_ID:?}" "${ECR_URI:?}" "${ECR_REGISTRY:?}" "${TARGET_GROUP_ARN:?}" "${DB_SECRET_NAME:?}" "${SESSION_SECRET_NAME:?}" "${LOG_GROUP_NAME:?}" "${SESSION_COOKIE_SECURE:?}"

expected_image=""
expected_version=""
container_name="cloudops-app"
port=5000
instance_id=""
target_group_arn=""
require_container_health=false
require_target_healthy=false
require_bootstrap_complete=false
wait_seconds=0

usage() {
    printf 'Usage: %s --expected-image ECR_URI@sha256:DIGEST --expected-version COMMIT [--container-name NAME --port PORT --require-container-health --wait-seconds SEC --instance-id ID --target-group-arn ARN --require-target-healthy]\n' "$0" >&2
}

while (($#)); do
    case "$1" in
        --expected-image) expected_image="${2:?missing image}"; shift 2 ;;
        --expected-version) expected_version="${2:?missing version}"; shift 2 ;;
        --container-name) container_name="${2:?missing container name}"; shift 2 ;;
        --port) port="${2:?missing port}"; shift 2 ;;
        --instance-id) instance_id="${2:?missing instance id}"; shift 2 ;;
        --target-group-arn) target_group_arn="${2:?missing target group ARN}"; shift 2 ;;
        --require-container-health) require_container_health=true; shift ;;
        --require-target-healthy) require_target_healthy=true; shift ;;
        --require-bootstrap-complete) require_bootstrap_complete=true; shift ;;
        --wait-seconds) wait_seconds="${2:?missing wait seconds}"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage; printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

[[ -z "$target_group_arn" || "$target_group_arn" == "$TARGET_GROUP_ARN" ]] || { printf 'Unexpected target group identity.\n' >&2; exit 2; }
[[ "$expected_image" =~ ^"${ECR_URI}"@sha256:[0-9a-f]{64}$ ]] || { printf 'Invalid immutable ECR image reference.\n' >&2; exit 2; }
[[ "$expected_version" =~ ^[0-9a-f]{12,40}$ ]] || { printf 'Invalid application version.\n' >&2; exit 2; }
[[ "$container_name" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ ]] || { printf 'Invalid container name.\n' >&2; exit 2; }
if [[ ! "$port" =~ ^[0-9]{1,5}$ ]] || ((port == 0 || port >= 65536)); then
    printf 'Invalid application port.\n' >&2
    exit 2
fi
if [[ ! "$wait_seconds" =~ ^[0-9]{1,5}$ ]] || ((wait_seconds > 1800)); then
    printf 'Invalid verification timeout.\n' >&2
    exit 2
fi
if [[ "$require_target_healthy" == true && ( -z "$target_group_arn" || -z "$instance_id" ) ]]; then
    printf 'Target group and instance ID are required for an ALB health gate.\n' >&2
    exit 2
fi
if [[ "$require_bootstrap_complete" == true ]]; then
    [[ "$(stat -c '%U:%G:%a' /var/lib/cloudops/bootstrap-complete.json)" == root:root:600 ]] || { printf 'Missing or unsafe bootstrap success marker.\n' >&2; exit 1; }
    python3 - "$expected_image" "$expected_version" <<'PY'
import json, re, sys
with open("/var/lib/cloudops/bootstrap-complete.json", encoding="utf-8") as source:
    marker = json.load(source)
if marker.get("image") != sys.argv[1] or marker.get("version") != sys.argv[2] or not re.fullmatch(r"[0-9a-f]{40}", marker.get("commit", "")) or not marker["commit"].startswith(sys.argv[2]):
    raise SystemExit("Bootstrap success marker does not match the approved release")
PY
fi

deadline=$((SECONDS + wait_seconds))
while :; do
    if ! docker inspect "$container_name" >/dev/null 2>&1; then
        printf 'Expected container %s is not present.\n' "$container_name" >&2
        exit 1
    fi

    running="$(docker inspect --format '{{.State.Running}}' "$container_name")"
    actual_image="$(docker inspect --format '{{.Config.Image}}' "$container_name")"
    actual_image_id="$(docker inspect --format '{{.Image}}' "$container_name")"
    expected_image_id="$(docker image inspect --format '{{.Id}}' "$expected_image")"
    [[ "$actual_image_id" =~ ^sha256:[0-9a-f]{64}$ && "$actual_image_id" == "$expected_image_id" ]] || {
        printf 'Container runtime image ID does not match the approved ECR digest.\n' >&2; exit 1;
    }
    actual_version="$(docker inspect --format '{{index .Config.Labels "org.apexforge.version"}}' "$container_name")"
    container_health="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$container_name")"
    health_status="$(curl --connect-timeout 3 --max-time 8 --silent --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:${port}/health" || true)"
    ready_status="$(curl --connect-timeout 3 --max-time 8 --silent --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:${port}/ready" || true)"
    api_status="$(curl --connect-timeout 3 --max-time 8 --silent --output /dev/null --write-out '%{http_code}' --max-redirs 0 "http://127.0.0.1:${port}/api/status" || true)"

    target_state=not-required
    if [[ -n "$target_group_arn" ]]; then
        target_state="$(aws elbv2 describe-target-health \
            --region "$AWS_REGION" \
            --target-group-arn "$target_group_arn" \
            --targets "Id=$instance_id,Port=5000" \
            --query 'TargetHealthDescriptions[0].TargetHealth.State' \
            --output text)" || { printf 'ALB verification API failed; refusing to poll an unsuccessful request.\n' >&2; exit 1; }
        [[ "$target_state" == None ]] && target_state=not-registered
    fi

    api_ok=false
    case "$api_status" in 200|301|302|303|307|308|401) api_ok=true ;; esac
    container_health_ok=true
    if [[ "$require_container_health" == true && "$container_health" != healthy ]]; then
        container_health_ok=false
    fi
    target_ok=true
    if [[ "$require_target_healthy" == true && "$target_state" != healthy ]]; then
        target_ok=false
    fi

    if [[ "$running" == true && "$actual_image" == "$expected_image" && "$actual_version" == "$expected_version" \
        && "$health_status" == 200 && "$ready_status" == 200 && "$api_ok" == true \
        && "$container_health_ok" == true && "$target_ok" == true ]]; then
        printf 'Checks passed: container=%s port=%s Docker-health=%s /health=%s /ready=%s /api/status=%s ALB=%s\n' \
            "$container_name" "$port" "$container_health" "$health_status" "$ready_status" "$api_status" "$target_state"
        printf 'Application version: %s; image reference: %s\n' "$actual_version" "$actual_image"
        exit 0
    fi

    if ((SECONDS >= deadline)); then
        printf 'Verification failed before timeout: running=%s image_matches=%s version_matches=%s Docker-health=%s /health=%s /ready=%s /api/status=%s ALB=%s\n' \
            "$running" "$([[ "$actual_image" == "$expected_image" ]] && printf true || printf false)" \
            "$([[ "$actual_version" == "$expected_version" ]] && printf true || printf false)" \
            "$container_health" "${health_status:-no-response}" "${ready_status:-no-response}" \
            "${api_status:-no-response}" "$target_state" >&2
        exit 1
    fi
    sleep 5
done
