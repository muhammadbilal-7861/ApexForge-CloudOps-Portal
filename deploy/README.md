# Single-instance deployment helpers

[Illustrated master guide](../docs/MASTER-GUIDE.md) · [Configuration](../docs/CONFIGURATION.md) · [Deployment](../docs/DEPLOYMENT.md)

`cloudops_config.py` validates non-secret private inventory; `render-iam-policy.py` renders role policies offline. `prepare-runtime.py` creates a private runtime file through an authorized app-role SSM shell, without starting or changing containers. `assert-deploy-context.sh` checks main, account and role. `publish-ecr-image.py` enforces immutable idempotent publication and image-ID equivalence.

`collect-cloudops-preflight.sh` snapshots read-only topology and validates one app/one Single-AZ MariaDB; `cloudops-canary-preflight.sh` uses app-role permissions before approval. `render-ssm-command.py` pins script source/checksums and one recipient; `cloudops-ssm-deploy.sh` submits the approved Run Command. `cloudops-deploy.sh` performs candidate-first cutover/exact rollback; `cloudops-verify.sh` validates image/Docker/HTTP/ALB health. `build-image.sh`, dependency locking and audit helpers preserve reproducibility/security.

No helper launches instances, edits RDS, deletes volumes or creates infrastructure. Private `.deploy-work` files are not artifacts. Reports contain commit/digest/instance/result metadata only; restrict readers. Runtime files, secrets and raw environment contents are never archived. The only Jenkins targets are none and canary; defaults do not invoke AWS APIs.
