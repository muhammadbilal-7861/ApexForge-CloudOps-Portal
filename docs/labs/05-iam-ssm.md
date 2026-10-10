# 05 — IAM and SSM-only access

[Master guide](../MASTER-GUIDE.md). Execute examples only in your own reviewed account.

## Architecture and purpose

![SSM paths](../diagrams/05-ssm.png)
[Editable SVG](../diagrams/05-ssm.svg). Humans use Identity Center sessions; Jenkins and application EC2 use separate instance profiles. SSM Agent initiates outbound HTTPS. No SSH rule or bastion is needed.

## Prerequisites

Labs 01–04; operator IAM provisioning permissions; DNS and NAT or SSM interface endpoints. Install the workstation Session Manager plugin using [AWS instructions](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html).

## Installation and configuration

On your workstation, create an EC2 trust document and three roles/profiles:

```bash
cat > .deploy-work/ec2-trust.json <<'JSON'
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}
JSON
for role in ApexForgeToolsRole ApexForgeAppRole ApexForgeObservabilityRole; do
  aws iam create-role --role-name "$role" --assume-role-policy-document file://.deploy-work/ec2-trust.json
  aws iam create-instance-profile --instance-profile-name "$role"
  aws iam add-role-to-instance-profile --instance-profile-name "$role" --role-name "$role"
  aws iam attach-role-policy --role-name "$role" \
    --policy-arn arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore
done
```

These are operator provisioning operations. Jenkins receives only the rendered policy from Lab09; it does not launch instances. The app receives the separately rendered app policy. Monitoring's discovery policy is in `observability/iam/prometheus-role-policy.json`; adapt its region before review. Add scoped log permissions for tools/monitoring if collecting their diagnostics.

Configure your Identity Center permission set using [AWS session IAM examples](https://docs.aws.amazon.com/systems-manager/latest/userguide/getting-started-restrict-access-examples.html): allow StartSession only on the recorded lab instance ARNs and approved shell/forwarding documents; scope ResumeSession/TerminateSession to the user's own sessions. Include the documented data-channel permission. Run Command authorization is separate and permits remote root execution.

After Lab06 creates the instances:

```bash
aws ssm describe-instance-information --filters "Key=InstanceIds,Values=$APP_INSTANCE_ID" \
  --query 'InstanceInformationList[].{ID:InstanceId,Status:PingStatus,Agent:AgentVersion}'
aws ssm start-session --target "$APP_INSTANCE_ID"
# Separate workstation terminal; localhost Jenkins UI, no inbound8080 rule:
aws ssm start-session --target "$TOOLS_INSTANCE_ID" --document-name AWS-StartPortForwardingSession \
  --parameters '{"portNumber":["8080"],"localPortNumber":["8080"]}'
```

Enable shell-session logging in Systems Manager → Session Manager → Preferences to an owned encrypted log destination. Port-forwarded session payloads are not ordinary shell-session logs; CloudTrail records API activity.

## Expected output and validation

Agent status is `Online`; instance CLI identity is its intended role. Metadata requires IMDSv2; EC2 has no public IP. An unauthorized operator must fail to start a session or invoke a command. Verify SGs contain no inbound22.

## Troubleshooting and root causes

Offline: agent, role association, DNS or endpoint443/egress failure. AccessDenied: distinguish operator permissions from node permissions. Ubuntu Snap uses `snap.amazon-ssm-agent.amazon-ssm-agent.service`; checking only `amazon-ssm-agent` may give a misleading result.

## Security considerations

Protect branch, Jenkins configuration, role attachments and approvers. Never execute untrusted PR code on the controller-volume/Docker-socket agent. Use short-lived identities; no AWS keys in Git or Jenkins files.

## Cleanup

Terminate your own sessions. Remove owned profile associations/policies/roles only after the lab instances are retired; preserve audit evidence.

## Interview questions with answers

**Does SSM need inbound SSH?** No; its node agent makes outbound authenticated HTTPS connections.
**Does IMDSv2 replace least privilege?** No. Tokens protect metadata access; IAM policies still limit what the resulting role can do.
