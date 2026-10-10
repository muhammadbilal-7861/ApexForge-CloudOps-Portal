# 12 — Testing, reproducibility and evidence

[Master guide](../MASTER-GUIDE.md).

## Architecture and purpose

Validate application and deployment helpers locally before AWS opt-in. Fake Docker/AWS tests exercise canonical identity, dual-stack ports, candidate/cutover/rollback and permission failures. They do not establish real IAM, RDS, ALB or SSM acceptance.

## Prerequisites

Python3.12 on Linux amd64, Docker, ShellCheck, reviewed Gitleaks/TruffleHog releases on PATH; Git checkout. Root-dependent simulated tests should run in a disposable Linux container, not on an actual application host. No AWS credentials are required.

## Commands and explanation

```bash
python3 -m venv .venv
.venv/bin/pip install --only-binary=:all: --require-hashes -r requirements.txt
.venv/bin/pip check
.venv/bin/pytest -q
python3 -m compileall -q app deploy tests docs wsgi.py
for script in deploy/*.sh tests/integration/*.sh observability/scripts/*.sh observability/alloy/bootstrap.sh; do
  bash -n "$script"
done
shellcheck deploy/*.sh tests/integration/*.sh observability/scripts/*.sh observability/alloy/bootstrap.sh
docker compose config --quiet
```

Non-root pytest reports skips for the simulated root-only cases. For the **complete suite**, build an owned test image and run without network, credentials or Docker socket:

```bash
docker build -t apexforge-tests:local .
git ls-files -z | tar --null -T - -cf .git/test-context.tar
docker run --rm --network none --user 0:0 \
  --mount "type=bind,source=$PWD/.git/test-context.tar,target=/context.tar,readonly" \
  --entrypoint bash apexforge-tests:local -ec \
  'mkdir /work; tar -xf /context.tar -C /work; cd /work; pytest -q'
```

The archive contains tracked source, not `.env`/private inventory. Jenkins runs equivalent pytest in its pinned Python image. Compare **committed** images using `bash tests/integration/compare_builds.sh`: this creates only owned disposable builders, does not publish/deploy and records both image IDs. See [build contract/update process](../../deploy/REPRODUCIBLE-BUILDS.md): pinned Python/frontend/BuildKit, hashed wheel lock, commit timestamp, linux/amd64, no uncontrolled package upgrade. Update base/dependencies in a reviewed PR, rerun full reports/reproducibility and assess fixed/unfixed CVEs.

Repeat secret audit with:

```bash
bash deploy/audit-history.sh
```

It requires reviewed Gitleaks/TruffleHog binaries; scans all reachable refs plus the union of reachable blobs, disables credential verification/network probing and preserves raw results privately. Also scan a tracked-source archive of the current tree; history and current documentation are distinct surfaces. Never print raw findings. Fix a secret by revoking/rotating through the owner; this phase never rewrites history.

## Expected output and validation

Full pytest passes with no skips in the isolated root container. Syntax/lint succeed; repeated committed image IDs match. Reports retain complete fixed/unfixed HIGH/CRITICAL evidence. See [actual validation record](../VALIDATION.md) for this revision's counts and tools.

For Groovy syntax: run a reviewed `groovy:4.0-jdk17-alpine` image, mount only Jenkinsfile and parse it with `new GroovyShell().parse(new File(args[0]))`. Parse every embedded shebang shell string with its declared shell. Jenkins plugin/Declarative validation needs the owner's configured Jenkins linter; local compilation does not prove plugin availability. This repository work does not access a live Jenkins controller.

## Troubleshooting and root causes

Host tests skip: use isolated root container. Windows bind-mount fixture tests slow: unpack the archive into native Linux container storage. Repeat IDs differ: inspect Git timestamp, archive inputs, platform, base/frontend/BuildKit digests and dependency lock; never weaken ECR comparison. Groovy dollar error: literal regex `$` in interpolated strings must be escaped.

## Security considerations

Never mount production sockets/credentials into tests. Privileged optional real-Docker rehearsal is explicit (`tests/integration/rehearse_canary.py`) and uses mock AWS/private disposable daemon; inspect ownership first. Tests must preserve exact-container and original-network semantics.

## Cleanup

Remove your private test archive and only explicitly owned disposable test containers/builders; retain evidence/images needed for review. Avoid blanket prune/volume deletion.

## Interview questions with answers

**Why image-ID comparison on repeated commits?** Immutable tags must refer to the exact image tested and scanned; a silent difference would deploy unreviewed content.
**Are mocked tests live acceptance?** No. They verify code contracts; endpoint reachability/IAM/ALB readiness need a controlled independent lab run.
