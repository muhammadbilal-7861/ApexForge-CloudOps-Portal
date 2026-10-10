# Architecture and trust boundaries

The Flask application uses hashed passwords, Flask-Login, CSRF protection and SQLAlchemy parameter binding. Gunicorn runs as a non-root user. `/health` is liveness, `/ready` checks DB connectivity, and `/metrics` is private monitoring data; endpoints alone are not proof of a fully healthy deployment.

Local Compose publishes only localhost port 5000, keeps MariaDB private and uses synthetic data. Optional Prometheus scrapes the application; local monitoring must not scrape original AWS hosts.

AWS laboratory resources are supplied by the operator: private application subnets, ALB-to-app security group ingress, RDS, Secrets Manager, ECR, SSM, CloudWatch, IAM profiles, Jenkins and Sonar. Private subnets need reviewed NAT/endpoints for package repositories, GitHub, ECR, Secrets Manager, SSM and CloudWatch. Verify DNS, routing, quotas and costs before approval.

Delivery checks the same platform/image built and scanned; immutable tags are reused only when image IDs match. Deployment uses the published digest. SSM downloads commit-pinned scripts, verifies SHA256 and targets exact inventoried instances. New canary uses host networking to access IMDSv2; the old bridge service continues on port 5000 until candidate verification succeeds on 5001. Cutover retains the exact original container and Docker configuration for verified rollback.

ASG first boot refuses pre-existing runtime/container state, blocks inbound application traffic with nftables, starts diagnostics, validates secrets and digest, verifies Docker health/readiness/application route, then opens the gate and creates a protected success marker. A clean explicit launch allowlist excludes inherited Placement/SubnetId, requires IMDSv2 and is read back from AWS. Both subnets are DryRun-checked. Initial capacity is 1/1/1; failed initial rollout safely returns to zero and never starts the contaminated source AMI. The existing canary is retained.

Historical canary acceptance succeeded; Ubuntu ASG acceptance is incomplete. Simulated tests cannot establish real networking, IAM, readiness or operational availability. Treat live promotion as pending the checklist in DEPLOYMENT.md.
