#!/usr/bin/env bash
set -Eeuo pipefail
set +x

readonly AWS_REGION=eu-north-1
readonly RUNTIME_ENV=/etc/cloudops/runtime.env
readonly ECR_REPOSITORY=apexforge-cloudops-portal
readonly LOG_GROUP=/cloudops/app

if (($# != 1)) || [[ ! "$1" =~ ^489502663059\.dkr\.ecr\.eu-north-1\.amazonaws\.com/apexforge-cloudops-portal@sha256:[0-9a-f]{64}$ ]]; then
    printf 'Usage: %s immutable-ECR-image-reference\n' "$0" >&2
    exit 2
fi
image_ref="$1"
image_digest="${image_ref##*@}"

for command_name in aws docker python3 stat; do
    command -v "$command_name" >/dev/null 2>&1 || { printf 'Required canary prerequisite is missing: %s\n' "$command_name" >&2; exit 1; }
done
[[ -f "$RUNTIME_ENV" ]] || { printf 'Approved /etc/cloudops/runtime.env is missing.\n' >&2; exit 1; }
[[ "$(stat -c '%U:%G:%a' "$RUNTIME_ENV")" == root:root:600 ]] || { printf 'runtime.env must be owned root:root with mode 0600.\n' >&2; exit 1; }

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
            raise SystemExit("runtime.env contains invalid, duplicate, or unapproved settings")
        values[key] = value
expected = {
    "FLASK_ENV": "production", "SESSION_COOKIE_SECURE": "false", "USE_AWS_SECRETS": "true",
    "AWS_SECRET_NAME": "cloudops/prod/mariadb", "AWS_REGION": "eu-north-1",
    "ENABLE_LAB_FAILURE_ENDPOINTS": "false",
}
if any(values.get(key) != value for key, value in expected.items()):
    raise SystemExit("runtime.env is missing an approved production setting")
if len(values.get("SECRET_KEY", "")) < 32:
    raise SystemExit("runtime.env has no valid session signing key")
print("Approved runtime environment validated.")
PY

# Compare without displaying or persisting either copy of the stable signing secret.
aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id cloudops/prod/flask-session-key --query SecretString --output text |
    python3 -c 'import sys; p=sys.argv[1]; key=next((line.rstrip("\n").partition("=")[2] for line in open(p,encoding="utf-8") if line.startswith("SECRET_KEY=")),""); value=sys.stdin.read(); value=value[:-1] if value.endswith("\n") else value; valid=bool(value) and "\n" not in value and "\r" not in value and len(value)>=32 and value==key; print("Stable Flask signing secret validated.") if valid else sys.exit(1)' "$RUNTIME_ENV" || {
        printf 'runtime.env signing key does not match the stable Secrets Manager value.\n' >&2
        exit 1
    }

aws secretsmanager get-secret-value --region "$AWS_REGION" --secret-id cloudops/prod/mariadb --query SecretString --output text |
    python3 -c 'import json,sys; d=json.load(sys.stdin); ok=all(isinstance(d.get(k),str) and d[k] for k in ("host","username","password")) and isinstance(d.get("dbname",d.get("database")),str) and bool(d.get("dbname",d.get("database"))); port=int(d.get("port",3306)); sys.exit(0 if ok and 0<port<65536 else 1)' || {
        printf 'Database secret is inaccessible or has an invalid schema.\n' >&2
        exit 1
    }

log_group="$(aws logs describe-log-groups --region "$AWS_REGION" --log-group-name-prefix "$LOG_GROUP" --query "logGroups[?logGroupName=='${LOG_GROUP}'].logGroupName | [0]" --output text)"
[[ "$log_group" == "$LOG_GROUP" ]] || { printf 'The app role cannot confirm the pre-created /cloudops/app log group.\n' >&2; exit 1; }

# Exercise all ECR image-read permissions without downloading layers or changing Docker state.
manifest="$(aws ecr batch-get-image --region "$AWS_REGION" --repository-name "$ECR_REPOSITORY" \
    --image-ids "imageDigest=$image_digest" --query 'images[0].imageManifest' --output text)"
[[ -n "$manifest" && "$manifest" != None ]] || { printf 'App role cannot read the requested digest from ECR.\n' >&2; exit 1; }
layer_digest="$(printf '%s' "$manifest" | python3 -c 'import json,sys; d=json.load(sys.stdin); layers=d.get("layers",[]); print(layers[0]["digest"] if layers else "")')"
[[ "$layer_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || { printf 'ECR image manifest did not contain an approved layer digest.\n' >&2; exit 1; }
aws ecr batch-check-layer-availability --region "$AWS_REGION" --repository-name "$ECR_REPOSITORY" \
    --layer-digests "$layer_digest" --query 'layers[0].layerAvailability' --output text | grep -Fx AVAILABLE >/dev/null
aws ecr get-download-url-for-layer --region "$AWS_REGION" --repository-name "$ECR_REPOSITORY" \
    --layer-digest "$layer_digest" --query downloadUrl --output text >/dev/null

printf 'Canary prerequisites passed: runtime file, stable secret, database secret, ECR image-read permissions and app log group.\n'
