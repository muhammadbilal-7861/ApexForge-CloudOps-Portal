# 08 — CloudWatch diagnostics

[Master guide](../MASTER-GUIDE.md).

## Architecture and purpose

The app role writes controlled deployment diagnostics to a pre-created CloudWatch group. Optional Alloy/Loki application collection is separate. Do not stream environment files or unrestricted journals.

## Prerequisites

Owned app EC2/SSM, app IAM policy, Logs endpoint or HTTPS egress. Set the same log-group name in inventory, agent and IAM.

## Installation and configuration

Workstation:

```bash
LOG_GROUP_NAME=/apexforge360/app
aws logs create-log-group --log-group-name "$LOG_GROUP_NAME"
aws logs put-retention-policy --log-group-name "$LOG_GROUP_NAME" --retention-in-days 14
```

App SSM shell: download the official Ubuntu AMD64 package and signature. Save AWS's official signing key as `amazon-cloudwatch-agent-public-key.asc`, independently check the documented fingerprint using [AWS verification instructions](https://docs.aws.amazon.com/AmazonCloudWatch/latest/monitoring/verify-CloudWatch-Agent-Package-Signature.html), then verify before installing:

```bash
export AWS_REGION=us-east-1  # replace with your reviewed region
curl --fail --location "https://amazoncloudwatch-agent-$AWS_REGION.s3.$AWS_REGION.amazonaws.com/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb" -o amazon-cloudwatch-agent.deb
curl --fail --location "https://amazoncloudwatch-agent-$AWS_REGION.s3.$AWS_REGION.amazonaws.com/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb.sig" -o amazon-cloudwatch-agent.deb.sig
gpg --import amazon-cloudwatch-agent-public-key.asc
gpg --verify amazon-cloudwatch-agent.deb.sig amazon-cloudwatch-agent.deb
sudo dpkg -i amazon-cloudwatch-agent.deb
sudo tee /opt/aws/amazon-cloudwatch-agent/etc/cloudops.json >/dev/null <<'JSON'
{"agent":{"run_as_user":"root"},"logs":{"logs_collected":{"files":{"collect_list":[{"file_path":"/var/log/cloudops-deploy.log","log_group_name":"/apexforge360/app","log_stream_name":"{instance_id}/deployment"}]}}}}
JSON
sudo /opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
  -a fetch-config -m ec2 -s -c file:/opt/aws/amazon-cloudwatch-agent/etc/cloudops.json
sudo systemctl enable amazon-cloudwatch-agent
sudo systemctl is-active amazon-cloudwatch-agent
```

Use your actual inventory group in the JSON. Record agent package version/hash for host rebuilds. This collects controlled deployment logs only; application collection is in Lab11. Keep tracing disabled when handling secrets.

## Expected output and validation

```bash
aws logs describe-log-streams --log-group-name "$LOG_GROUP_NAME" \
  --query 'logStreams[].{Stream:logStreamName,Recent:lastEventTimestamp}'
aws logs tail "$LOG_GROUP_NAME" --since 15m
```

Expect stage/failure descriptions without passwords, signing keys or raw environment values. A stream may not exist before the first deployment creates its diagnostic file.

## Troubleshooting and root causes

No stream: log file absent, group mismatch, CreateLogStream/PutLogEvents denied, agent stopped or Logs endpoint unreachable. Signature mismatch: stop installation. Never disable verification or publish raw cloud-init/journal dumps.

## Security considerations

Restrict readers, encrypt logs and set retention. Diagnostics contain infrastructure metadata even when secrets are withheld. Never add runtime.env or AWS credential directories to collection.

## Cleanup

Export required redacted evidence, then delete only owned groups after incident/retention requirements. Stop agents only when retiring their recorded host.

## Interview questions with answers

**Why pre-create groups?** The app can write scoped streams without permission to create arbitrary log groups.
**Why logs and metrics?** Metrics show rates/state; controlled logs explain failed operations. Both must avoid high-cardinality/private labels.
