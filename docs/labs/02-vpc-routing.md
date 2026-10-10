# 02 — Six-subnet VPC and routing

[Master guide](../MASTER-GUIDE.md) · Commands are for your own reviewed lab account.

## Architecture and purpose
![Six subnets](../diagrams/02-vpc-routing.png)
[Editable SVG](../diagrams/02-vpc-routing.svg). Reference VPC `10.0.0.0/16`. Public A/B: `10.0.1.0/24`, `10.0.2.0/24`; application A/B: `10.0.11.0/24`, `10.0.12.0/24`; database A/B: `10.0.21.0/24`, `10.0.22.0/24`. The app uses one private subnet; tools and monitoring may share the other with separate security groups. Database subnets have local-only routing.

## Prerequisites
Lab 01, reviewed independent account, `AZ_A`/`AZ_B`, permission to create VPC resources, a budget. This lab creates chargeable resources when **you** run it. Save each created ID in your private ledger and tag it.

## Exact configuration steps

```bash
VPC_ID=$(aws ec2 create-vpc --cidr-block 10.0.0.0/16 --query Vpc.VpcId --output text)
aws ec2 create-tags --resources "$VPC_ID" --tags Key=Project,Value="$PROJECT"
aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-support
aws ec2 modify-vpc-attribute --vpc-id "$VPC_ID" --enable-dns-hostnames
subnet() { aws ec2 create-subnet --vpc-id "$VPC_ID" --availability-zone "$1" \
  --cidr-block "$2" --query Subnet.SubnetId --output text; }
PUBLIC_A=$(subnet "$AZ_A" 10.0.1.0/24)
PUBLIC_B=$(subnet "$AZ_B" 10.0.2.0/24)
APP_A=$(subnet "$AZ_A" 10.0.11.0/24)
APP_B=$(subnet "$AZ_B" 10.0.12.0/24)
DB_A=$(subnet "$AZ_A" 10.0.21.0/24)
DB_B=$(subnet "$AZ_B" 10.0.22.0/24)
for id in "$PUBLIC_A" "$PUBLIC_B" "$APP_A" "$APP_B" "$DB_A" "$DB_B"; do
  aws ec2 create-tags --resources "$id" --tags Key=Project,Value="$PROJECT"
  aws ec2 modify-subnet-attribute --subnet-id "$id" --no-map-public-ip-on-launch
done
IGW=$(aws ec2 create-internet-gateway --query InternetGateway.InternetGatewayId --output text)
aws ec2 attach-internet-gateway --internet-gateway-id "$IGW" --vpc-id "$VPC_ID"
route_table() { aws ec2 create-route-table --vpc-id "$VPC_ID" --query RouteTable.RouteTableId --output text; }
PUBLIC_RT=$(route_table); APP_RT_A=$(route_table); APP_RT_B=$(route_table); DB_RT=$(route_table)
aws ec2 create-route --route-table-id "$PUBLIC_RT" --destination-cidr-block 0.0.0.0/0 --gateway-id "$IGW"
for id in "$PUBLIC_A" "$PUBLIC_B"; do
  aws ec2 associate-route-table --route-table-id "$PUBLIC_RT" --subnet-id "$id"
done
aws ec2 associate-route-table --route-table-id "$APP_RT_A" --subnet-id "$APP_A"
aws ec2 associate-route-table --route-table-id "$APP_RT_B" --subnet-id "$APP_B"
for id in "$DB_A" "$DB_B"; do
  aws ec2 associate-route-table --route-table-id "$DB_RT" --subnet-id "$id"
done
```
Every table has a local VPC route automatically. The public default route reaches the internet gateway; the application default route must use NAT, not IGW. Nothing adds a database default route.

For the simplest install-capable lab, create a NAT per AZ (paid hourly plus traffic):

```bash
EIP_A=$(aws ec2 allocate-address --domain vpc --query AllocationId --output text)
EIP_B=$(aws ec2 allocate-address --domain vpc --query AllocationId --output text)
NAT_A=$(aws ec2 create-nat-gateway --subnet-id "$PUBLIC_A" --allocation-id "$EIP_A" --query NatGateway.NatGatewayId --output text)
NAT_B=$(aws ec2 create-nat-gateway --subnet-id "$PUBLIC_B" --allocation-id "$EIP_B" --query NatGateway.NatGatewayId --output text)
aws ec2 wait nat-gateway-available --nat-gateway-ids "$NAT_A" "$NAT_B"
aws ec2 create-route --route-table-id "$APP_RT_A" --destination-cidr-block 0.0.0.0/0 --nat-gateway-id "$NAT_A"
aws ec2 create-route --route-table-id "$APP_RT_B" --destination-cidr-block 0.0.0.0/0 --nat-gateway-id "$NAT_B"
```
A single NAT lab is cheaper but introduces a shared failure dependency/cross-AZ traffic; deliberately route both app tables to that NAT if choosing it. Never describe that option as resilient. An endpoint-only alternative needs interface endpoints for `ssm`, `ssmmessages`, `ecr.api`, `ecr.dkr`, `secretsmanager`, `logs`, plus an S3 gateway endpoint on the app route tables. Add EC2 API endpoint if monitoring discovery needs it. Older SSM versions/regions may also need `ec2messages`; check [AWS endpoint requirements](https://docs.aws.amazon.com/systems-manager/latest/userguide/setup-create-vpc.html). Endpoints **do not** supply Ubuntu, Docker Hub, Sonar images or GitHub downloads; retain approved egress or provide reviewed artifact mirrors.

## Expected output and validation
Created IDs look like `vpc-...` / `subnet-...` and are **your** outputs. Run:

```bash
aws ec2 describe-subnets --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'Subnets[].{CIDR:CidrBlock,AZ:AvailabilityZone,PublicIP:MapPublicIpOnLaunch}' --output table
aws ec2 describe-route-tables --filters "Name=vpc-id,Values=$VPC_ID" \
  --query 'RouteTables[].{ID:RouteTableId,Routes:Routes,Associations:Associations}'
```
Expect exactly six lab subnets, two zones, no automatic public IP assignment, local-only DB table and app defaults to NAT when selected.

## Troubleshooting and root causes
Private download timeout: missing NAT route, unavailable NAT, DNS disabled or egress/NACL denial. SSM cannot connect: endpoint private DNS/443 or role missing. RDS subnet group error: both DB subnets accidentally in one AZ. Do not fix routing failures by adding public IPs or SSH.

## Security considerations
Tag all resources, record associations, retain default stateful SG behavior and review NACLs before tightening ephemeral traffic. Use endpoint policies to restrict resources. NAT is outbound reachability, not inbound administrator access.

## Cleanup
After deleting dependent lab EC2/RDS/ALB/endpoints, delete lab default routes, then NATs and wait for deletion before releasing their **recorded** EIPs. Disassociate/delete non-main route tables, delete six recorded subnets, detach/delete the recorded IGW and delete the VPC. Never delete by name wildcard; consult [cleanup chapter](14-cleanup.md).

## Interview questions with answers
**What makes a subnet public?** Its route to an IGW, not its name; a host also needs a public address for direct IPv4 internet access.
**Why local-only DB routing?** MariaDB needs only private client traffic. It should not require general internet egress.
