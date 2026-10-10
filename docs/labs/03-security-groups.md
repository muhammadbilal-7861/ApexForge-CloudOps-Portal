# 03 — Security groups and private communication

[Master guide](../MASTER-GUIDE.md) · Commands are for your own reviewed lab account.

## Architecture and purpose
![Communication](../diagrams/06-security-groups.png)
[Editable SVG](../diagrams/06-security-groups.svg). Separate ALB, application, database, tools, observability and optional endpoint SGs. Only ALB edge accepts public traffic. Administrators use SSM; **no inbound 22 anywhere**.

## Prerequisites
Lab 02 variables retained, owned VPC. Review egress requirements and current DNS/NACL behavior before narrowing default egress.

## Configuration steps

```bash
sg() { aws ec2 create-security-group --vpc-id "$VPC_ID" --group-name "$1" \
  --description "$2" --query GroupId --output text; }
ALB_SG=$(sg apexforge-alb 'HTTPS edge')
APP_SG=$(sg apexforge-app 'Private application')
DB_SG=$(sg apexforge-db 'Private MariaDB')
TOOLS_SG=$(sg apexforge-tools 'SSM managed Jenkins and Sonar')
OBS_SG=$(sg apexforge-observability 'Private metrics and logs')
ENDPOINT_SG=$(sg apexforge-endpoints 'Private AWS API endpoints')
aws ec2 authorize-security-group-ingress --group-id "$ALB_SG" --protocol tcp --port 443 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id "$ALB_SG" --protocol tcp --port 80 --cidr 0.0.0.0/0
aws ec2 authorize-security-group-ingress --group-id "$APP_SG" --protocol tcp --port 5000 --source-group "$ALB_SG"
aws ec2 authorize-security-group-ingress --group-id "$APP_SG" --protocol tcp --port 5000 --source-group "$OBS_SG"
aws ec2 authorize-security-group-ingress --group-id "$DB_SG" --protocol tcp --port 3306 --source-group "$APP_SG"
# Optional Alloy ingestion; no public Loki access.
aws ec2 authorize-security-group-ingress --group-id "$OBS_SG" --protocol tcp --port 3100 --source-group "$APP_SG"
for source in "$APP_SG" "$TOOLS_SG" "$OBS_SG"; do
  aws ec2 authorize-security-group-ingress --group-id "$ENDPOINT_SG" --protocol tcp --port 443 --source-group "$source"
done
```
Source groups follow attached ENIs without hard-coded host IPs. TCP 80 is only for the redirect configured in Lab 07. No rule admits candidate 5001, MariaDB from clients or management UI ports. Existing defaults allow outbound traffic; for restrictive egress, add exact ALB→app, app→DB, app→Loki and nodes→AWS endpoint/NAT HTTPS rules before removing defaults. Account for DNS and package downloads.

Optional interface endpoints (review per-hour charges before executing):

```bash
for service in ssm ssmmessages ecr.api ecr.dkr secretsmanager logs ec2; do
  aws ec2 create-vpc-endpoint --vpc-id "$VPC_ID" --vpc-endpoint-type Interface \
    --service-name "com.amazonaws.$AWS_REGION.$service" --subnet-ids "$APP_A" "$APP_B" \
    --security-group-ids "$ENDPOINT_SG" --private-dns-enabled
done
aws ec2 create-vpc-endpoint --vpc-id "$VPC_ID" --vpc-endpoint-type Gateway \
  --service-name "com.amazonaws.$AWS_REGION.s3" --route-table-ids "$APP_RT_A" "$APP_RT_B"
```
Use regional service availability checks first (`describe-vpc-endpoint-services`); record endpoint IDs and replace default full-access endpoint policies with your reviewed least-privilege policies.

## Expected output and validation

```bash
aws ec2 describe-security-groups --group-ids "$ALB_SG" "$APP_SG" "$DB_SG" "$TOOLS_SG" "$OBS_SG" \
  --query 'SecurityGroups[].{ID:GroupId,Ingress:IpPermissions}'
```
Expected: app 5000 sources exactly ALB+observability; DB3306 source app only; tools no ingress; obs optional3100 from app. Preflight rejects SSH and public application access. Validate denied connections from unauthorized disposable lab sources and successful ALB readiness after deployment.

## Troubleshooting and root causes
ALB timeout: wrong source group, app not listening on private interface, NACL or wrong target port. SSM UI forwarding needs no inbound UI rule. Loki timeout: allowed source not actual app ENI or collector endpoint wrong.

## Security considerations
Do not use VPC-wide CIDR ingress for application/DB. A security group is stateful; allowed return traffic does not need a reverse ingress rule. Public HTTPS still needs valid TLS and authentication; SGs do not sanitize requests.

## Cleanup
Delete recorded endpoints/ENIs and dependent services first, revoke inter-SG references, then delete only recorded lab groups. Never modify shared default SGs.

## Interview questions with answers
**Why SG-to-SG rules?** They describe the authorized workload boundary without stale IP allowlists.
**Why not open 5001?** Candidate traffic is internal verification on loopback while production stays on5000; it must not become a second public service.
