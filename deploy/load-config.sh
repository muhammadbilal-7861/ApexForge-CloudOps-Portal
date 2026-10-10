#!/usr/bin/env bash
# Source only validated, shell-quoted non-secret inventory. Never source runtime secrets.
_cloudops_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
_cloudops_exports="$(python3 "$_cloudops_dir/cloudops_config.py" --format shell)" || return 1
eval "$_cloudops_exports"
unset _cloudops_exports _cloudops_dir
