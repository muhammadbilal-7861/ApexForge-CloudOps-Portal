#!/usr/bin/env bash
# Run locally after fetching all authorized refs. Never publish raw scanner files.
set -Eeuo pipefail
set +x
umask 077
for tool in git python3 gitleaks trufflehog; do command -v "$tool" >/dev/null; done
git rev-parse --git-dir >/dev/null
audit_dir="$(mktemp -d "${TMPDIR:-/tmp}/apexforge360-audit.XXXXXXXX")"
chmod 700 "$audit_dir"
export CLOUDOPS_AUDIT_DIR="$audit_dir"
gitleaks_status=0
gitleaks git --redact --no-banner --log-opts=--all --report-format json \
    --report-path "$audit_dir/gitleaks.json" . > "$audit_dir/gitleaks.log" 2>&1 || gitleaks_status=$?
# Scan every unique reachable blob, independently of a scanner's branch traversal.
python3 - <<'PY'
import os
import subprocess
from pathlib import Path

directory = Path(os.environ["CLOUDOPS_AUDIT_DIR"]) / "all-blobs"
directory.mkdir()
for line in subprocess.check_output(["git", "rev-list", "--objects", "--all"], text=True).splitlines():
    oid = line.split(" ", 1)[0]
    if subprocess.check_output(["git", "cat-file", "-t", oid], text=True).strip() == "blob":
        (directory / (oid + ".txt")).write_bytes(subprocess.check_output(["git", "cat-file", "blob", oid]))
PY
trufflehog_status=0
trufflehog filesystem "$audit_dir/all-blobs" --no-verification --no-update \
    --no-ignore-tag --fail-on-scan-errors --json > "$audit_dir/trufflehog.jsonl" \
    2> "$audit_dir/trufflehog.log" || trufflehog_status=$?
export GITLEAKS_STATUS="$gitleaks_status" TRUFFLEHOG_STATUS="$trufflehog_status"
python3 - <<'PY'
import json
import os
from pathlib import Path

directory = Path(os.environ["CLOUDOPS_AUDIT_DIR"])
try:
    leaks = json.loads((directory / "gitleaks.json").read_text())
    hog = [json.loads(line) for line in (directory / "trufflehog.jsonl").read_text().splitlines() if line]
except (OSError, ValueError):
    raise SystemExit("Secret audit failed; raw output retained privately for owner review")
summary = {"gitleaksFindings": len(leaks), "independentFindings": len(hog),
           "rawReportsPublished": False, "liveCredentialVerification": False}
(directory / "redacted-summary.json").write_text(json.dumps(summary) + "\n")
print(json.dumps(summary))
if leaks or hog or os.environ["GITLEAKS_STATUS"] != "0" or os.environ["TRUFFLEHOG_STATUS"] != "0":
    raise SystemExit("Audit requires private review; do not publish raw matches or rewrite history automatically")
PY
printf 'Private audit artifacts retained at %s; publish only reviewed redacted summaries.\n' "$audit_dir"
