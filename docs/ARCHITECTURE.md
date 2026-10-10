# Reference architecture

![Complete architecture](diagrams/01-architecture.png)
[Editable SVG](diagrams/01-architecture.svg) · [Full visual atlas](MASTER-GUIDE.md)

The diagrams describe an independent learning account, not verified live infrastructure. VPC10.0.0.0/16 contains public10.0.1.0/24 and10.0.2.0/24, private app10.0.11.0/24 and10.0.12.0/24, and private DB10.0.21.0/24 and10.0.22.0/24 across two example AZs. One private app EC2 serves5000 behind an HTTPS ALB. Tools and observability EC2s use distinct SGs/roles in private app subnets.

One Single-AZ MariaDB RDS instance belongs to a DB subnet group with both DB subnets; there is no second DB instance. Database tables have local-only routes. App egress uses reviewed NAT or AWS endpoints plus approved package/source mirrors. Session Manager provides administrator access; Run Command provides deployment. No port22 or bastion.

Candidate-first release on localhost5001 retains the serving5000 container until verification. Cutover rechecks canonical identity/config; failures restore exact prior container/network/ports and verify HTTP plus ALB recovery. Initial installation is explicitly approved and has no prior application to restore. Runtime secrets are role-fetched, never image/Git contents. Single-instance outages and cutover interruptions remain architectural limitations.
