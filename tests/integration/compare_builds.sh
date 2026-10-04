#!/usr/bin/env bash
set -Eeuo pipefail
repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"
mkdir -p reports/reproducibility
commit="$(git rev-parse HEAD)"
ids=()
for attempt in 1 2; do
    [[ "$(git rev-parse HEAD)" == "$commit" ]] || { printf 'HEAD changed during comparison.\n' >&2; exit 1; }
    tag="apexforge-repro:${commit}-${attempt}"
    bash deploy/build-image.sh "$tag" >"reports/reproducibility/build-${attempt}.log" 2>&1
    # Docker Desktop can refresh a WSL bind-mount inode after importing an image.
    # Re-enter the same checkout before reading HEAD; never accept a changed commit.
    cd "$repo_root"
    ids+=("$(docker image inspect --format '{{.Id}}' "$tag")")
done
[[ "$(git rev-parse HEAD)" == "$commit" ]] || { printf 'HEAD changed during comparison.\n' >&2; exit 1; }
printf 'commit=%s\nplatform=linux/amd64\nfirst=%s\nsecond=%s\n' "$commit" "${ids[0]}" "${ids[1]}" | tee reports/reproducibility/result.txt
[[ "${ids[0]}" == "${ids[1]}" ]] || { printf 'ERROR: clean builds differ.\n' >&2; exit 1; }
printf 'Two independent clean builds produced identical Docker image IDs.\n'
