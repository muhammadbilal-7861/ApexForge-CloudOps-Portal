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

The CI workflow runs pytest and a Docker build. Future deployment is intentionally a TODO: GitHub OIDC -> AWS IAM role -> ECR -> EC2/ASG. No static AWS key secrets are used.

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
