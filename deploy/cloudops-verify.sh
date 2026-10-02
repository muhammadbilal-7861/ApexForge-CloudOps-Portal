#!/usr/bin/env bash
set -Eeuo pipefail

readonly CONTAINER_NAME="cloudops-app"
expected_image=""
expected_version=""
instance_id=""
target_group_arn=""
require_target_healthy=false

usage() {
    printf 'Usage: %s --expected-image ECR_URI@sha256:DIGEST --expected-version COMMIT [--instance-id ID --target-group-arn ARN [--require-target-healthy]]\n' "$0" >&2
}

while (($#)); do
    case "$1" in
        --expected-image) expected_image="${2:?missing image}"; shift 2 ;;
        --expected-version) expected_version="${2:?missing version}"; shift 2 ;;
        --instance-id) instance_id="${2:?missing instance id}"; shift 2 ;;
        --target-group-arn) target_group_arn="${2:?missing target group ARN}"; shift 2 ;;
        --require-target-healthy) require_target_healthy=true; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage; printf 'Unknown argument: %s\n' "$1" >&2; exit 2 ;;
    esac
done

if [[ ! "$expected_image" =~ ^489502663059\.dkr\.ecr\.eu-north-1\.amazonaws\.com/apexforge-cloudops-portal@sha256:[0-9a-f]{64}$ ]]; then
    printf 'Invalid immutable ECR image reference.\n' >&2
    exit 2
fi
if [[ ! "$expected_version" =~ ^[0-9a-f]{12,40}$ ]]; then
    printf 'Invalid application version.\n' >&2
    exit 2
fi

if ! docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    printf 'Expected container %s is not present.\n' "$CONTAINER_NAME" >&2
    exit 1
fi

running="$(docker inspect --format '{{.State.Running}}' "$CONTAINER_NAME")"
actual_image="$(docker inspect --format '{{.Config.Image}}' "$CONTAINER_NAME")"
actual_version="$(docker inspect --format '{{index .Config.Labels "org.apexforge.version"}}' "$CONTAINER_NAME")"
if [[ "$running" != true || "$actual_image" != "$expected_image" || "$actual_version" != "$expected_version" ]]; then
    printf 'Container verification failed (running=%s, image_matches=%s, version_matches=%s).\n' \
        "$running" "$([[ "$actual_image" == "$expected_image" ]] && printf true || printf false)" \
        "$([[ "$actual_version" == "$expected_version" ]] && printf true || printf false)" >&2
    exit 1
fi

health_status="$(curl --connect-timeout 3 --max-time 8 --silent --show-error --output /dev/null --write-out '%{http_code}' http://127.0.0.1:5000/health)"
if [[ "$health_status" != 200 ]]; then
    printf 'Liveness check failed: /health returned HTTP %s.\n' "$health_status" >&2
    exit 1
fi

ready_status="$(curl --connect-timeout 3 --max-time 8 --silent --show-error --output /dev/null --write-out '%{http_code}' http://127.0.0.1:5000/ready)"
if [[ "$ready_status" != 200 ]]; then
    printf 'Readiness check failed: /ready returned HTTP %s; database readiness is not bypassed.\n' "$ready_status" >&2
    exit 1
fi

# /api/status is authenticated. A redirect/401 confirms the protected route is reachable without exposing status data or credentials.
api_status="$(curl --connect-timeout 3 --max-time 8 --silent --show-error --output /dev/null --write-out '%{http_code}' --max-redirs 0 http://127.0.0.1:5000/api/status || true)"
case "$api_status" in
    200|301|302|303|307|308|401) ;;
    *) printf 'API route check failed: /api/status returned HTTP %s.\n' "${api_status:-no response}" >&2; exit 1 ;;
esac

printf 'Local checks passed: /health=%s /ready=%s /api/status=%s\n' "$health_status" "$ready_status" "$api_status"
printf 'Application version: %s; image reference: %s\n' "$actual_version" "$actual_image"

if [[ -n "$target_group_arn" ]]; then
    if [[ -z "$instance_id" ]]; then
        printf 'An instance ID is required when checking a target group.\n' >&2
        exit 2
    fi
    target_state="$(aws elbv2 describe-target-health \
        --region eu-north-1 \
        --target-group-arn "$target_group_arn" \
        --targets "Id=$instance_id,Port=5000" \
        --query 'TargetHealthDescriptions[0].TargetHealth.State' \
        --output text)"
    if [[ "$target_state" == None || -z "$target_state" ]]; then
        printf 'Instance is not registered in the ALB target group; no registration was performed.\n'
        if [[ "$require_target_healthy" == true ]]; then
            exit 1
        fi
    else
        printf 'Existing ALB target state: %s (left unchanged).\n' "$target_state"
        if [[ "$require_target_healthy" == true && "$target_state" != healthy ]]; then
            exit 1
        fi
    fi
fi
