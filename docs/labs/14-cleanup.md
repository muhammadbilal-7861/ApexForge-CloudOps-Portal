# 14 — Costs, cleanup and operational review

[Master guide](../MASTER-GUIDE.md).

## Architecture and purpose

Clean up only an independent, inventoried learning environment. EC2/tools/monitoring, RDS, ALB, NAT/interface endpoints, logs and ECR incur separate charges. No live cost estimate or resource ownership has been verified here.

## Prerequisites

Written resource ledger with account/region/IDs/Project tag, backups, no external consumers, owner approval. Keep audit/deployment evidence and required DB snapshots. Ending this lab must not terminate an existing healthy canary or touch RDS/secrets in somebody else's environment.

## Cleanup

1. Record current identity and ledger. Review bills/budget alerts in the owner account.
2. Drain/deregister the exact application target; remove only owned public listeners/ALB after consumer/DNS review.
3. Stop/remove only owned containers after evidence export; terminate the three recorded lab EC2s only after checking disks/backups/DeleteOnTermination.
4. Snapshot/delete the single owned DB after disabling its deletion protection explicitly; retain final snapshot. Delete DB subnet group after DB is gone.
5. Retain ECR rollback digests until no consumer remains. Schedule only owned secret deletion with recovery window.
6. Delete recorded endpoints, NATs and wait for NAT deletion before releasing recorded EIPs. Remove owned route associations, SG references, subnets and IGW, then VPC.
7. Remove owned profile associations/inline policies/roles. Keep required logs until retention requirements expire.

Copy-paste **read-only review** commands:

```bash
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}'
aws ec2 describe-instances --instance-ids "$APP_INSTANCE_ID" "$TOOLS_INSTANCE_ID" "$OBS_INSTANCE_ID" \
  --query 'Reservations[].Instances[].{ID:InstanceId,State:State.Name,Tags:Tags,Disks:BlockDeviceMappings}'
aws rds describe-db-instances --db-instance-identifier apexforge-db \
  --query 'DBInstances[0].{ID:DBInstanceIdentifier,Protected:DeletionProtection,Backups:BackupRetentionPeriod}'
aws ec2 describe-nat-gateways --filter "Name=vpc-id,Values=$VPC_ID" \
  --query 'NatGateways[].{ID:NatGatewayId,State:State,Addresses:NatGatewayAddresses}'
```

After review, one example targeted action (not a bulk script):

```bash
aws elbv2 deregister-targets --target-group-arn "$TARGET_GROUP_ARN" --targets "Id=$APP_INSTANCE_ID,Port=5000"
aws elbv2 wait target-deregistered --target-group-arn "$TARGET_GROUP_ARN" --targets "Id=$APP_INSTANCE_ID,Port=5000"
```

Use AWS service-specific delete APIs only with the recorded owned IDs. Do not provide blanket account deletion or Docker-prune commands. The guarded review step is intentional: a cleanup script cannot infer ownership safely from a similar name.

## Expected output and validation

Review output matches your ledger/account. Target transitions to unused/drained. Deleted resources disappear from their exact Describe API; billing eventually reflects retirement. Final snapshots/required evidence remain deliberately owned and may still incur charges.

## Troubleshooting and root causes

DependencyViolation: an ENI, listener, SG reference or route association still depends on the resource. Find its owner instead of forcing deletion. NAT deleting: wait before EIP release. DB deletion denied: protection or backup review incomplete. Do not disable safeguards without approval.

## Security considerations

No history rewrite, secret rotation or license addition is part of repository cleanup. Keep backups encrypted and restricted. Delete only what you independently own; preserve rollback containers/volumes on still-active hosts.

## Interview questions with answers

**Why cleanup order?** Dependencies and shared ownership make arbitrary deletion unsafe; remove consumers before providers.
**Why does an empty lab still cost money?** NAT, endpoints, storage, snapshots/logs and allocated addresses can remain billable after EC2 stops.
