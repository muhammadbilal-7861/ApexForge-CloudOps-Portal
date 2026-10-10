# 01 — Account preparation and local application

[Master guide](../MASTER-GUIDE.md) · Commands are for your own reviewed lab account.

## Architecture and purpose
Start locally: Flask/Gunicorn talks to MariaDB on an isolated Compose network. Cloud identity is unnecessary. Later, use IAM Identity Center for human operators and instance roles for servers. All CIDRs, names, screenshots of output and IDs in this guide are **examples**, not verified live inventory. The diagrams are original drawings, not AWS console captures.

## Prerequisites
Linux/WSL, Git, Docker Engine with Compose v2+, curl, Python 3.12, at least 4 GB local RAM. Use an independent AWS lab account; enable root MFA, remove root access keys and configure billing contacts and a budget before creating paid resources. Keep an operator resource ledger outside Git. No license grant has been selected by the owner.

## Installation and configuration
Install Docker using [the official Ubuntu instructions](https://docs.docker.com/engine/install/ubuntu/). Configure a short-lived operator profile with `aws configure sso` after installing AWS CLI v2 and the Session Manager plugin using AWS's signed installers. Never copy operator credentials into EC2 or Jenkins.

```bash
git clone https://github.com/muhammadbilal-7861/ApexForge-CloudOps-Portal.git
cd ApexForge-CloudOps-Portal
cp .env.example .env
docker compose config --quiet
docker compose up -d --build --wait
docker compose exec app flask --app wsgi init-db
docker compose exec app flask --app wsgi seed-demo
curl --fail http://127.0.0.1:5000/health
curl --fail http://127.0.0.1:5000/ready
curl --fail http://127.0.0.1:5000/metrics
```
`config` validates substitution; `up --wait` waits for container health. `init-db` creates the application's tables; `seed-demo` is development-only and idempotent. Open localhost:5000, register a synthetic account, sign in and create/delete a record. Demo credentials are `local-demo` / `development-only-demo-password`; never use them in AWS.

For the remaining workstation examples, use Bash and keep variables in the same shell:

```bash
aws configure sso --profile cloudops-lab
aws sso login --profile cloudops-lab
export AWS_PROFILE=cloudops-lab AWS_REGION=us-east-1 AWS_DEFAULT_REGION=us-east-1
export AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
export PROJECT=apexforge360
mkdir -p .deploy-work
chmod 700 .deploy-work
umask 077
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}'
aws ec2 describe-availability-zones --filters Name=state,Values=available \
  --query 'AvailabilityZones[].ZoneName' --output table
```
Pick two returned zones, then set `AZ_A` and `AZ_B` explicitly. Example: `export AZ_A=us-east-1a AZ_B=us-east-1b`. Zone letters are account-specific. AWS commands in this guide are instructions for an authorized learner; repository preparation did not execute them.

## Expected output and validation
Examples: both health endpoints return HTTP 200, `/metrics` exposes Prometheus text, `seed-demo` reports seeded/already existing. Identity shows **your** account and SSO role; stop if it is wrong. Validate browser CSRF-backed forms, not just curl. `git status --short` must not show `.env`.

## Troubleshooting and root causes
5000 in use: identify the process; choose a separate local Compose port rather than stopping an unknown service. Ready=503: database starting, invalid credentials or missing tables; inspect `docker compose logs --tail=50 app mariadb` privately. Login loop over HTTP: `.env` must have secure cookies disabled **locally only**. Docker unavailable in WSL: configure your chosen local engine explicitly; do not start an existing production daemon blindly.

## Security considerations
Local passwords are deliberately public development values. Bind only localhost. Use synthetic records. Log output can reveal operational metadata; redact it before sharing. Enable AWS MFA and least-privilege operator sessions; Jenkins never uses this SSO profile.

## Cleanup
`docker compose down` removes this project's containers/network and preserves its database volume. Only if the data is disposable and ownership is confirmed, `docker compose down --volumes` deletes **this local project's** data. Sign out with `aws sso logout`. Do not run broad Docker prune commands.

## Interview questions with answers
**Why separate local and cloud secrets?** A public demo must work without privileged cloud access; cloud runtime credentials are fetched using the instance role and never shipped in the image.
**Why test readiness separately?** Liveness checks the process; readiness validates database access. A live process can be unready.
