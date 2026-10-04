#!/usr/bin/env bash
# The build context is committed source only. No workspace paths are mounted:
# this also works with the Jenkins named volume and the host Docker socket.
set -Eeuo pipefail
[[ $# == 1 ]] || { printf 'Usage: %s local-image-tag\n' "$0" >&2; exit 2; }
readonly cli_image=docker:29-cli@sha256:b1805116a6a86cc591b5d5f60a910a0715cdcc9d18d866ad68b1457ead25c35c
readonly builder_image=moby/buildkit:v0.28.0@sha256:37539dd4d60fc70968d164d3850d903a2c56f6402214a1953fbf9fcb81ada731
epoch="$(git show -s --format=%ct HEAD)"
[[ "$epoch" =~ ^[0-9]+$ ]] || exit 1
# Each invocation has an isolated builder with no imported/shared build cache.
# Cleanup is scoped to this invocation's generated name, never a global prune.
builder="cloudops-build-$(cat /proc/sys/kernel/random/uuid)"
# Fixed platform, frontend, builder, timestamp and export behavior are part of
# the build contract. Security scanners still run independently in Jenkins.
git archive --format=tar HEAD Dockerfile requirements.txt app wsgi.py |
    docker run --rm --interactive --volume /var/run/docker.sock:/var/run/docker.sock \
        --entrypoint sh "$cli_image" -ec '
            builder="$1"; epoch="$2"; tag="$3"; builder_image="$4"
            cleanup() { docker buildx rm --force "$builder" >/dev/null || true; }
            trap cleanup EXIT
            docker buildx create --name "$builder" --driver docker-container --driver-opt "image=$builder_image" >/dev/null
            docker buildx build --builder "$builder" --platform linux/amd64 --no-cache --pull \
                --build-arg "SOURCE_DATE_EPOCH=$epoch" --provenance=false --sbom=false \
                --output "type=docker,rewrite-timestamp=true" --tag "$tag" -
        ' build-cloudops "$builder" "$epoch" "$1" "$builder_image"
