#!/usr/bin/env bash
set -Eeuo pipefail
set +x

readonly AWS_REGION=eu-north-1
readonly RUNTIME_ENV="${CLOUDOPS_RUNTIME_ENV:-/etc/cloudops/runtime.env}"
readonly ECR_REPOSITORY=apexforge-cloudops-portal
readonly LOG_GROUP=/cloudops/app

if (($# != 2)) || [[ ! "$1" =~ ^489502663059\.dkr\.ecr\.eu-north-1\.amazonaws\.com/apexforge-cloudops-portal@sha256:[0-9a-f]{64}$ ]]; then
    printf 'Usage: %s immutable-ECR-image-reference target-group-arn\n' "$0" >&2
    exit 2
fi
image_ref="$1"
image_digest="${image_ref##*@}"
target_group_arn="$2"
[[ "$target_group_arn" == arn:aws:elasticloadbalancing:eu-north-1:489502663059:targetgroup/tg-cloudops-app/* ]] || { printf 'Unexpected target group.\n' >&2; exit 2; }
# This runs via SSM on the application node, with its instance role, before approval.
# A denied API is not an unhealthy target: fail on the first unsuccessful request.
aws elbv2 describe-target-health --region "$AWS_REGION" --target-group-arn "$target_group_arn" --output json >/dev/null || {
    printf 'Application-role ALB permission check failed; refusing deployment.\n' >&2
    exit 1
}

for command_name in aws python3 stat; do
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

# Exercise image/index and layer read permissions without downloading layer bytes or changing Docker state.
manifest_file="$(mktemp /tmp/cloudops-ecr-manifest.XXXXXX)"
child_manifest_file="$(mktemp /tmp/cloudops-ecr-child-manifest.XXXXXX)"
trap 'rm -f "$manifest_file" "$child_manifest_file"' EXIT
accepted_media_types=(
    application/vnd.oci.image.index.v1+json
    application/vnd.docker.distribution.manifest.list.v2+json
    application/vnd.oci.image.manifest.v1+json
    application/vnd.docker.distribution.manifest.v2+json
)
aws ecr batch-get-image --region "$AWS_REGION" --repository-name "$ECR_REPOSITORY" \
    --image-ids "imageDigest=$image_digest" --accepted-media-types "${accepted_media_types[@]}" \
    --output json > "$manifest_file"

parse_ecr_manifest() {
    python3 - "$1" "$2" "$3" <<'PY'
import hashlib
import json
import re
import sys

mode, requested_digest, response_path = sys.argv[1:]
valid_digest = re.compile(r"sha256:[0-9a-f]{64}\Z")
index_types = {
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
}
image_types = {
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
}

def fail(message):
    raise SystemExit(message)

if not valid_digest.fullmatch(requested_digest):
    fail("Requested ECR manifest digest is malformed.")
try:
    with open(response_path, encoding="utf-8") as response_file:
        response = json.load(response_file)
except (OSError, UnicodeError, json.JSONDecodeError):
    fail("ECR returned a malformed BatchGetImage response.")
if not isinstance(response, dict) or response.get("failures"):
    fail("ECR BatchGetImage did not return the requested image.")
images = response.get("images")
if not isinstance(images, list) or len(images) != 1 or not isinstance(images[0], dict):
    fail("ECR BatchGetImage returned a missing or ambiguous image response.")
image = images[0]
image_id = image.get("imageId")
raw_manifest = image.get("imageManifest")
if not isinstance(image_id, dict) or image_id.get("imageDigest") != requested_digest:
    fail("ECR returned an image digest that does not match the requested immutable digest.")
if not isinstance(raw_manifest, str) or not raw_manifest:
    fail("ECR returned an empty or malformed image manifest.")
actual_digest = "sha256:" + hashlib.sha256(raw_manifest.encode("utf-8")).hexdigest()
if actual_digest != requested_digest:
    fail("ECR image manifest content does not match its immutable digest.")
try:
    manifest = json.loads(raw_manifest)
except json.JSONDecodeError:
    fail("ECR image manifest is not valid JSON.")
if not isinstance(manifest, dict) or manifest.get("schemaVersion") != 2:
    fail("ECR image manifest has an unsupported or malformed schema.")
media_type = manifest.get("mediaType") or image.get("imageManifestMediaType")
if not isinstance(media_type, str) or (image.get("imageManifestMediaType") and image["imageManifestMediaType"] != media_type):
    fail("ECR image manifest has a missing or inconsistent media type.")

if media_type in index_types:
    if mode == "child":
        fail("ECR platform descriptor resolved to another image index instead of an application manifest.")
    descriptors = manifest.get("manifests")
    if not isinstance(descriptors, list) or not descriptors:
        fail("ECR image index has no manifest descriptors.")
    matches = []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            fail("ECR image index contains a malformed descriptor.")
        digest = descriptor.get("digest")
        platform = descriptor.get("platform")
        if not isinstance(digest, str) or not valid_digest.fullmatch(digest) or not isinstance(platform, dict):
            fail("ECR image index contains a descriptor with a malformed digest or platform.")
        os_name, architecture = platform.get("os"), platform.get("architecture")
        if os_name == "unknown" and architecture == "unknown":
            continue
        if not isinstance(os_name, str) or not os_name or not isinstance(architecture, str) or not architecture:
            fail("ECR image index contains a missing or malformed platform entry.")
        descriptor_type = descriptor.get("mediaType")
        if descriptor_type not in image_types:
            fail("ECR image index contains an unsupported child manifest media type.")
        if os_name == "linux" and architecture == "amd64":
            matches.append(digest)
    if len(matches) != 1:
        fail("ECR image index must contain exactly one linux/amd64 application manifest.")
    print("child:" + matches[0])
elif media_type in image_types:
    config = manifest.get("config")
    layers = manifest.get("layers")
    if not isinstance(config, dict) or not valid_digest.fullmatch(str(config.get("digest", ""))):
        fail("ECR image manifest has a missing or malformed config digest.")
    if not isinstance(layers, list) or not layers:
        fail("ECR image manifest has no application layers.")
    for layer in layers:
        if not isinstance(layer, dict) or not isinstance(layer.get("digest"), str) or not valid_digest.fullmatch(layer["digest"]):
            fail("ECR image manifest contains a missing or malformed layer digest.")
    print("layer:" + layers[0]["digest"])
else:
    fail("ECR image manifest has an unsupported media type.")
PY
}

selection="$(parse_ecr_manifest image "$image_digest" "$manifest_file")"
if [[ "$selection" == child:* ]]; then
    child_digest="${selection#child:}"
    aws ecr batch-get-image --region "$AWS_REGION" --repository-name "$ECR_REPOSITORY" \
        --image-ids "imageDigest=$child_digest" --accepted-media-types \
        application/vnd.oci.image.manifest.v1+json application/vnd.docker.distribution.manifest.v2+json \
        --output json > "$child_manifest_file"
    layer_selection="$(parse_ecr_manifest child "$child_digest" "$child_manifest_file")"
else
    layer_selection="$selection"
fi
layer_digest="${layer_selection#layer:}"
[[ "$layer_selection" == layer:* && "$layer_digest" =~ ^sha256:[0-9a-f]{64}$ ]] || {
    printf 'ECR application manifest did not contain a valid layer digest.\n' >&2
    exit 1
}
aws ecr batch-check-layer-availability --region "$AWS_REGION" --repository-name "$ECR_REPOSITORY" \
    --layer-digests "$layer_digest" --query 'layers[0].layerAvailability' --output text | grep -Fx AVAILABLE >/dev/null
aws ecr get-download-url-for-layer --region "$AWS_REGION" --repository-name "$ECR_REPOSITORY" \
    --layer-digest "$layer_digest" --query downloadUrl --output text >/dev/null

printf 'Canary prerequisites passed: runtime file, stable secret, database secret, ECR image-read permissions and app log group.\n'
