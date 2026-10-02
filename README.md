# ApexForge CloudOps Portal

A small Flask training application for practicing AWS and DevOps operations. It deliberately exposes ordinary authentication, database traffic, file uploads, HTTP metrics, health probes and logs so infrastructure behavior is easy to observe and troubleshoot.

## Architecture

### Local

```text
Browser -> Flask/Gunicorn :5000 -> MariaDB
                         -> Prometheus /metrics
```

### Intended AWS deployment

```text
Internet -> Route53 -> ALB -> Private EC2 / Auto Scaling Group -> Docker -> Flask
                                                              |-> RDS MariaDB
                                                              |-> Secrets Manager
                                                              |-> S3

Flask /metrics -> Prometheus -> Grafana
Application stdout -> Grafana Alloy -> Loki -> Grafana

Developer -> GitHub -> GitHub Actions -> OIDC -> AWS IAM Role -> ECR -> EC2 / ASG
```

The app uses an application factory, simple SQLAlchemy models and one blueprint. Database connection pool pre-ping/recycle and bounded connection timeouts support recovery after transient RDS failover. There is no schema migration dependency: `flask --app wsgi init-db` creates tables idempotently.

## Run locally

Requires Python 3.12+ and reachable MariaDB. Create a database and account matching the environment variables, then:

```powershell
Copy-Item .env.example .env
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
$env:DB_HOST='127.0.0.1'; $env:DB_NAME='cloudops'; $env:DB_USER='cloudops'; $env:DB_PASSWORD='your-local-password'
flask --app wsgi init-db
flask --app wsgi run --host 0.0.0.0 --port 5000
```

Visit http://localhost:5000/register and create a user. Registration never grants admin privileges. For the controlled failure lab, promote a user explicitly in your development database (`UPDATE user SET is_admin=1 WHERE username='...'`) and set `ENABLE_LAB_FAILURE_ENDPOINTS=true`.

## Docker Compose

Docker Compose runs the web app and MariaDB with a persistent named database volume:

```powershell
docker compose up --build -d
docker compose exec app flask --app wsgi init-db
docker compose logs -f app
docker compose down
```

Open http://localhost:5000. Use `docker compose down -v` only when you intentionally want to delete the local database volume. Compose's default credentials are for local training only; override `DB_PASSWORD` and `DB_ROOT_PASSWORD` for a shared machine.

## Configuration

Copy `.env.example` to `.env` for local use. The app reads environment variables directly; Docker Compose reads `.env` automatically.

| Variable | Purpose |
|---|---|
| `FLASK_ENV` | Displayed deployment environment |
| `SECRET_KEY` | Flask session and CSRF signing key; replace in all deployed environments |
| `SESSION_COOKIE_SECURE` | Set true when HTTPS terminates at the ALB |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | Local/direct database connection |
| `USE_AWS_SECRETS` | Set true to load DB config from Secrets Manager |
| `AWS_SECRET_NAME` | Secret ID/name holding username, password, host, port, dbname |
| `AWS_REGION` | AWS SDK region, for example `eu-north-1` |
| `S3_BUCKET_NAME` | Private upload bucket name |
| `ENABLE_LAB_FAILURE_ENDPOINTS` | Enables guarded admin-only failure simulations |
| `APP_VERSION` | Version exposed in dashboard/status |

### Secrets Manager and IAM

With `USE_AWS_SECRETS=true`, secret JSON fields are `username`, `password`, `host`, `port`, and `dbname`. boto3 uses its default credential provider chain; on EC2 this should be the attached instance profile's temporary credentials. No AWS access keys belong in this repository or on the instance. The app never logs secret content. A failed secret lookup leaves liveness available and readiness unavailable.

The eventual EC2 role needs narrowly scoped `secretsmanager:GetSecretValue` for the named DB secret (plus `kms:Decrypt` if a customer-managed KMS key protects it), and `s3:PutObject` for `arn:aws:s3:::YOUR_BUCKET/uploads/*`. ECR image-pull permissions are also required by the host/container deployment mechanism. Restrict resource ARNs and add bucket encryption/lifecycle policies as appropriate.

## Endpoints

| Path | Access | Purpose |
|---|---|---|
| `/login`, `/register`, `/logout` | Public/public/authenticated | Session authentication |
| `/dashboard` | Authenticated | User, host, version, dependency and traffic overview |
| `/users` | Authenticated | Registered user list |
| `/records`, `/records/add` | Authenticated | Read/write MariaDB training data |
| `/upload` | Authenticated | Private S3 upload; PNG/JPG/JPEG/TXT/PDF, max 10 MB |
| `/status`, `/api/status` | Authenticated | Dependency, version, environment, host and UTC snapshot |
| `/health` | Public | Process liveness only; never checks DB |
| `/ready` | Public | `SELECT 1`; 200 when DB is available, 503 otherwise |
| `/metrics` | Public | Prometheus exposition format |
| `/troubleshooting` | Authenticated | Explains lab probes and controls |
| `/lab/fail/db`, `/lab/recover/db`, `/lab/fail/error` | Admin + enabled | Controlled interview failure cases |

Health is intentionally independent of dependencies so a DB outage does not make the process appear dead. Readiness indicates whether this app instance can currently use its database. The DB simulation is per worker process; restart the container to reset it if Gunicorn routes requests to another worker.

## Observability

`/metrics` exports `cloudops_http_requests_total`, `cloudops_http_errors_total`, request duration, login attempt/success/failure, database error, and S3 success/failure counters, plus default Python process metrics. Every response includes an `X-Request-ID` (incoming header reused when supplied). Logs go to stdout with timestamp, hostname, method, path, status, duration, user ID and request ID. Passwords, cookies and AWS credentials are not logged. Docker/Gunicorn captures stdout for a collector such as Grafana Alloy.

## Tests and helper commands

```powershell
python -m pytest -q
python -m compileall -q app wsgi.py
docker build -t apexforge-cloudops:local .
```

The existing GitHub Actions workflow runs pytest and a Docker build. The Jenkins pipeline below separately publishes the validated `main` image to ECR using the EC2 instance role; production deployment remains a later step. No static AWS key secrets are used.

## Jenkins DevSecOps pipeline

`Jenkinsfile` provides a repository-side CI and security pipeline. On `main`, after all configured checks pass, it pushes the scanned image to the existing ECR repository. It does not deploy to production or create/change infrastructure. The existing GitHub Actions workflow remains independent.

### Pipeline stages

1. Checkout and record branch, commit, agent, image tag, and health endpoints.
2. Run Gitleaks against the checked-out Git history. Findings fail the build; SARIF output is redacted and archived.
3. Install the declared Python dependencies in an isolated Python 3.12 container, then run pytest and publish JUnit results.
4. Run SonarQube SAST/quality analysis and wait for the configured Quality Gate.
5. Run Trivy filesystem/SCA analysis for dependency vulnerabilities and misconfiguration. The table is printed to the build log and JSON is archived. Gitleaks is the dedicated secret scanner; its report is redacted so findings are not copied into build artifacts.
6. Detect Terraform and CloudFormation files. If present, run Trivy config validation; otherwise report that the extension point is currently unused.
7. Build the existing Dockerfile, then print and archive a Trivy image vulnerability report.
8. Archive security reports. On the `main` branch only, verify the caller is account `489502663059` and role `DevSecOpsToolsRole`, log in to ECR with `aws ecr get-login-password`, then push the scanned image tagged with its Git commit. Optional EC2/ASG deployment remains `none` by default and requires the separate preflight and approval described below.

`TRIVY_SEVERITY` and `TRIVY_EXIT_CODE` are Jenkins build parameters. Defaults are `HIGH,CRITICAL` and `1`: high/critical findings fail both filesystem and image scans. Set exit code to `0` for report-only operation during rollout; reports remain visible. Gitleaks findings and failed tests always fail. SonarQube's Quality Gate is configured to abort the pipeline when it fails. Reports are archived even when a later stage fails.

### Jenkins prerequisites and credentials

Use a Linux agent labelled `linux && docker` with Docker CLI access to the EC2 host's daemon. Jenkins must run in a container named exactly `jenkins`, with its named home/workspace volume and `/var/run/docker.sock` mounted into it. All tool containers use `--volumes-from jenkins` and `--workdir "$WORKSPACE"`, so the checked-out repository and Docker socket remain visible even when the workspace lives in a named volume. The SonarScanner and AWS CLI containers use host networking to reach SonarQube and EC2 instance metadata. Prefer an isolated agent: access to the Docker socket is effectively privileged and untrusted pull-request code must not share this privileged host. The pipeline uses pinned Gitleaks, Trivy, SonarScanner CLI, Python, and AWS CLI images; versions are declared at the top of the Jenkinsfile.

Install these Jenkins plugins:

* Pipeline (Declarative Pipeline and Pipeline: Basic Steps)
* Git plugin
* Credentials Binding
* SonarQube Scanner for Jenkins (`sonar`)
* JUnit

Configure **Manage Jenkins → System → SonarQube installations** with an installation named exactly `sonarqube`. Store its analysis token as a Jenkins **Secret text** credential with ID `sonarqube-token` (or update `SONAR_TOKEN_CREDENTIAL_ID` in the Jenkinsfile). Do not put the token in repository files, job parameters, or command-line literals. The Sonar scanner receives the token through a masked Jenkins credential environment binding. The containerized scanner uses host networking; configure the SonarQube URL to be reachable both from the Jenkins container and from host networking (for example, the EC2 private address and SonarQube port 9000). The Jenkins SonarQube installation name is what `withSonarQubeEnv` uses to attach the analysis task for `waitForQualityGate`.

Use the existing SonarQube project key `ApexForge-CloudOps-Portal`. Add a SonarQube webhook to `https://<jenkins-host>/sonarqube-webhook/`; this is required by Jenkins `waitForQualityGate`. Ensure Jenkins agents can reach the configured SonarQube URL. `sonar-project.properties` identifies the Python source and test paths and imports pytest's JUnit report.

The ECR push stage runs only for the `main` branch and uses the EC2 instance profile through the AWS CLI v2 default credential chain; it contains no AWS keys. The AWS CLI container uses host networking to reach EC2 instance metadata and verifies the caller account and role before pushing. Docker's ECR login config is temporary and removed after the stage. The existing repository must already exist at `489502663059.dkr.ecr.eu-north-1.amazonaws.com/apexforge-cloudops-portal`. Grant `DevSecOpsToolsRole` `ecr:GetAuthorizationToken` on `*` and `ecr:BatchCheckLayerAvailability`, `ecr:InitiateLayerUpload`, `ecr:UploadLayerPart`, `ecr:CompleteLayerUpload`, and `ecr:PutImage` scoped to that repository ARN. The pipeline does not create the repository or change AWS infrastructure.

The Terraform/CloudFormation stage currently finds no AWS infrastructure definitions in this repository; it runs Trivy config only when such files are added. The deployment stages described below are opt-in; the default `DEPLOY_TARGET=none` does not deploy. This Jenkins job can remain a normal Pipeline-from-SCM job: branch detection resolves `GIT_BRANCH` or remote Git refs and independently verifies `HEAD` against `origin/main` before ECR publication or deployment.

### Opt-in EC2 / ASG deployment extension

The Jenkins job exposes `DEPLOY_TARGET=none|canary|asg`; `none` is the default. Non-`none` runs are accepted only from the exact `main` commit, require the HIGH/CRITICAL Trivy blocking configuration, resolve the commit tag to an immutable ECR digest, run read-only AWS topology/IAM/RDS/SSM preflight, and pause for a 15-minute human approval. `canary` sends a checksum-pinned SSM command to the existing instance `i-02777a62f2a65bc1e`. It refuses to start a stopped instance or change unapproved containers. `asg` requires `ASG_AMI_REVIEWED=true` after a human verifies the configured AMI is sanitized, then uses the existing numeric Launch Template version as its source. The first opted-in ASG rollout raises the existing 0/0/0 group to 1/2/2; later rollouts use an explicit numeric version and Instance Refresh with health checks and rollback. A 120-minute Jenkins timeout allows the controlled rollout to finish. Neither option changes the RDS instance, VPC, subnets, listeners, or target-group registration.

The canary preflight verifies SSM Online, `/etc/cloudops/runtime.env` root ownership/mode and approved settings, that its Flask `SECRET_KEY` matches the stable Secrets Manager value, the database secret schema, app-role ECR manifest/layer read permissions, and the `/cloudops/app` log group. It does not expose secret values. Canary deployment first runs the new digest-pinned image on host-network loopback port 5001 with a matching Docker healthcheck and checks `/health` and `/ready`. Only after candidate success does it stop and rename the exact inventoried legacy `cloudops-flask:1.1` (or existing managed container) and start the managed image on port 5000. The instance must already be registered in the existing target group; after cutover the pipeline requires the ALB target to become healthy but never registers or deregisters it. Any failure stops/retains the new container and restores the previous one. No container or volume is deleted.

`deploy/` contains the SSM deploy/verify scripts, read-only preflight collector, safe renderers, user-data template, IAM policy examples, and architecture-specific operating instructions. User data references Secrets Manager values by name and writes the stable session signing key to `/etc/cloudops/runtime.env` with root ownership and mode 0600. It never prints secret values. Keep the session key stable across instances. `SESSION_COOKIE_SECURE=false` is intentional until end-to-end HTTPS at the ALB is confirmed; then coordinate changing the secure-cookie policy. The `/cloudops/app` CloudWatch log group must already exist.

Required Jenkins parameters/configuration:

* `DEPLOY_TARGET=none` remains the safe default. Only choose `canary` or `asg` after reviewing the runbook and prerequisites; the pipeline asks for a separate approval after read-only checks.
* Set `ASG_AMI_REVIEWED=true` only for ASG rollout after confirming the AMI has no embedded credentials, user data, or application state. This boolean is a deliberate human assertion, not an automated AMI certification.
* Attach the IAM policy example `deploy/iam/jenkins-cloudops-deploy-policy.json` to the existing EC2 Jenkins role only after IAM review. Keep the EC2 app policy separate (`cloudops-ec2-instance-policy.json`). No static AWS credentials are used. Read-only `Describe*` APIs may require `Resource: "*"`; SSM command output retrieval is also scoped according to AWS IAM's supported resource model.
* The CI host needs Docker CLI/socket access and AWS CLI access through the EC2 instance metadata role. The app instance profile needs scoped ECR pull, Secrets Manager read, CloudWatch log write, and target-health read permissions as shown in its policy example.
* Set Jenkins global environment variable `CLOUDOPS_DEPLOY_APPROVERS` to the exact approved Jenkins user IDs, comma-separated. The approval step uses that `submitter` allowlist and fails closed if the variable is missing.

The deployment helpers preserve prior container/image state for rollback and never prune Docker volumes or touch unknown containers. Reports under `reports/` include release/deployment evidence and continue through the existing Jenkins artifact archiving.

Before retiring a manually maintained app instance or changing capacity, operators must independently inventory its Docker container name, image and mounts; confirm its target-group registration and that the ASG deployment is healthy; then make that retirement decision separately. These pipeline scripts do not terminate EC2 instances or remove volumes. No live AWS state was changed while preparing this repository extension.

`.gitignore` and `.dockerignore` exclude `.runtime.env`, non-example `.env` files, AWS credential files, private keys, Terraform state, local scan caches, and generated reports. Keep runtime credentials outside the build context and repository.

Probe examples (PowerShell):

```powershell
Invoke-RestMethod http://localhost:5000/health
Invoke-RestMethod http://localhost:5000/ready
Invoke-WebRequest http://localhost:5000/metrics
```

With Compose running, simulate database loss using `docker compose stop mariadb`; `/health` remains 200 while `/ready` becomes 503. Restore using `docker compose start mariadb`; `/ready` recovers after the database accepts connections.

## Troubleshooting

* **Readiness 503:** inspect `docker compose logs app mariadb`, DB host/port/user/password, security group rules and RDS availability. The response does not disclose exception details; logs carry the diagnostic.
* **Secrets Manager unavailable:** check region, secret name, network egress/VPC endpoint, instance profile and `GetSecretValue` permission. The app logs a sanitized failure.
* **S3 upload errors:** confirm bucket/region, instance role `s3:PutObject`, object key prefix access, and outbound/VPC endpoint connectivity. The bucket should remain private; no public ACL is set.
* **Login/session problems:** configure a stable high-entropy `SECRET_KEY`; set `SESSION_COOKIE_SECURE=true` for HTTPS behind the ALB.
* **Recovery after RDS failover:** pool pre-ping checks stale connections, and each connection has a finite timeout. A request in flight at the instant of failover can fail; retry it after DB recovery.

## Security notes and limitations

Forms use Flask-WTF CSRF tokens, passwords use Werkzeug hashes, ORM queries are parameterized, uploads are extension checked and size bounded, and session cookies are HTTP-only/SameSite. This is an interview lab, not a finished public identity system: add email verification, rate limiting, audit retention, formal migrations/backups, stricter CSP, and production secret management before real user workloads. Admin promotion is deliberately an operator action; public registration cannot create administrators. `ENABLE_LAB_FAILURE_ENDPOINTS` should remain false outside isolated practice environments.
