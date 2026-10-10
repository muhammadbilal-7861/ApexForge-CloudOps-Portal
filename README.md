# ApexForge360 — Single-EC2 AWS DevSecOps reference

A Flask/MariaDB learning project with a guarded Jenkins security pipeline and opt-in SSM deployment to **one private application EC2**. Jenkins/SonarQube and observability have their own private EC2 hosts. The database is **one Single-AZ RDS MariaDB instance**, with a two-subnet DB subnet group.

![AWS reference architecture](docs/diagrams/01-architecture.png)
[Editable SVG](docs/diagrams/01-architecture.svg)

## Start here

Read the **[illustrated learner master guide](docs/MASTER-GUIDE.md)**: fourteen labs covering account/local setup, six-subnet networking, IAM/SSM, clean EC2, MariaDB/secrets, ECR/ALB, CloudWatch, Jenkins/Sonar/security, monitoring, tests and deployment/rollback. Each lab includes commands, explanations, expected results, validation, troubleshooting, security, cleanup and interview answers.

## Local quick start

```bash
cp .env.example .env
docker compose config --quiet
docker compose up -d --build --wait
docker compose exec app flask --app wsgi init-db
docker compose exec app flask --app wsgi seed-demo
curl --fail http://127.0.0.1:5000/health
curl --fail http://127.0.0.1:5000/ready
```

Open http://127.0.0.1:5000. Register a synthetic account or use the development-only demo described in Lab01. Local startup requires no AWS account, original inventory or secrets. `docker compose down` retains the owned local database volume.

## Guarded AWS workflow

Defaults: `AWS_OPERATIONS=false`, `DEPLOY_TARGET=none`. The only deployment option is `canary`, a candidate-first strategy on the single app EC2. A reviewed private inventory, exact main/account/role checks, blocking security gates and designated approvals precede publication/deployment. Immutable ECR tags are never overwritten. The candidate runs on localhost5001; only after verification does production5000 change. Original containers/images/volumes remain available for exact rollback.

[Configuration](docs/CONFIGURATION.md) · [Deployment](docs/DEPLOYMENT.md) · [Build/update contract](deploy/REPRODUCIBLE-BUILDS.md) · [Validation evidence](docs/VALIDATION.md) · [Security audit](docs/SECURITY-AUDIT.md) · [Reporting](SECURITY.md) · [Contributing](CONTRIBUTING.md)

## Scope and limits

All diagrams use **reference examples**, including VPC10.0.0.0/16 and six example subnet CIDRs. No original live AWS values are needed or claimed verified. Administrators use SSM Session Manager; Jenkins uses SSM Run Command. No SSH22 or bastion is required. This is a single-instance learning design with a cutover interruption and no database failover guarantee.

Repository validation is local/simulated. Independent AWS provisioning, IAM, network, agents, ALB/RDS, Jenkins plugins/webhooks and operational recovery require an owner-controlled lab acceptance run. No live AWS/Jenkins resource was accessed or changed for this revision. No production certification, SLA or license terms are implied.
