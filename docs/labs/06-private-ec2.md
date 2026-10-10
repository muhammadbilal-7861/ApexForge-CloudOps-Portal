# 06 — Clean private EC2 and runtime preparation

[Master guide](../MASTER-GUIDE.md). These are operator instructions, not executed provisioning.

## Architecture and purpose

One app EC2 in APP_A; Jenkins/SonarQube and observability EC2 in APP_B with separate SGs/profiles. Start with a reviewed official Ubuntu24.04 AMD64 image, without existing app containers or environment files.

## Prerequisites

Labs01–05; quotas, budget, profiles and private routing. App `t3.micro` is a learning size. Jenkins/Sonar need substantially more memory: review current Sonar requirements and allocate memory for builds as well. No live EC2 resources were inspected or launched during repository preparation.

## Exact installation and configuration

Resolve an official image on the workstation, then pin the concrete reviewed ID:

```bash
PARAM=/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id
AMI_ID=$(aws ssm get-parameter --name "$PARAM" --query Parameter.Value --output text)
aws ec2 describe-images --owners 099720109477 --image-ids "$AMI_ID" \
  --query 'Images[].{ID:ImageId,Owner:OwnerId,Arch:Architecture,State:State,Name:Name,Root:RootDeviceName,Created:CreationDate}'
```

Expect exactly one available x86_64 Ubuntu Noble24.04 image from Canonical owner `099720109477`. Review name, age and provenance against [Canonical documentation](https://ubuntu.com/aws/docs/aws-how-to/instances/find-ubuntu-images/); save ID/date privately. Do not launch using a moving parameter expression.

Prepare an owned SSM-only bootstrap before launching; it contains no secrets:

```bash
cat > .deploy-work/ssm-only-bootstrap.sh <<'BASH'
#!/usr/bin/env bash
set -Eeuo pipefail
set +x
apt-get update
apt-get install -y snapd
if ! snap list amazon-ssm-agent >/dev/null 2>&1; then
  snap install amazon-ssm-agent --classic
fi
snap start --enable amazon-ssm-agent
systemctl is-active --quiet snap.amazon-ssm-agent.amazon-ssm-agent.service
BASH
chmod 600 .deploy-work/ssm-only-bootstrap.sh
APP_INSTANCE_ID=$(aws ec2 run-instances --image-id "$AMI_ID" --instance-type t3.micro \
  --iam-instance-profile Name=ApexForgeAppRole \
  --network-interfaces "DeviceIndex=0,SubnetId=$APP_A,Groups=$APP_SG,AssociatePublicIpAddress=false" \
  --metadata-options HttpTokens=required,HttpEndpoint=enabled,HttpPutResponseHopLimit=1 \
  --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":20,"VolumeType":"gp3","Encrypted":true,"DeleteOnTermination":true}}]' \
  --user-data file://.deploy-work/ssm-only-bootstrap.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Project,Value=apexforge360},{Key=Role,Value=app},{Key=Monitoring,Value=enabled}]' \
  --query 'Instances[0].InstanceId' --output text)
aws ec2 wait instance-running --instance-ids "$APP_INSTANCE_ID"
```

Use the verified root-device name if different from `/dev/sda1`. For tools and monitoring, repeat this owned-instance pattern with their profiles/SGs in APP_B, appropriate class/storage and Role tags `tools`/`observability`. Record `TOOLS_INSTANCE_ID` and `OBS_INSTANCE_ID`. Never reuse an unknown host.

Through an authorized SSM session, install Docker's signed Ubuntu repository:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg unzip python3 python3-venv git nftables
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
sudo tee /etc/apt/sources.list.d/docker.sources >/dev/null <<'EOF'
Types: deb
URIs: https://download.docker.com/linux/ubuntu
Suites: noble
Components: stable
Signed-By: /etc/apt/keyrings/docker.asc
EOF
sudo apt-get update
apt-cache madison docker-ce
read -r -p 'Reviewed Docker package version from that list: ' DOCKER_VERSION
sudo apt-get install -y "docker-ce=$DOCKER_VERSION" "docker-ce-cli=$DOCKER_VERSION" \
  containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo docker version
sudo snap start --enable amazon-ssm-agent
sudo systemctl is-active snap.amazon-ssm-agent.amazon-ssm-agent.service
```

The repository key/signature check protects APT packages; select and record a reviewed version. Follow [Docker's official installation procedure](https://docs.docker.com/engine/install/ubuntu/) for current supported host packages.

Install AWS CLI v2 using [AWS signature verification](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html). Save the official PGP key from that page as `aws-cli-public-key.asc`, check its documented fingerprint independently, then:

```bash
curl --fail --location https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o awscliv2.zip
curl --fail --location https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip.sig -o awscliv2.sig
gpg --import aws-cli-public-key.asc
gpg --fingerprint 'AWS CLI'
gpg --verify awscliv2.sig awscliv2.zip
unzip -q awscliv2.zip
sudo ./aws/install
aws --version
aws sts get-caller-identity --query '{Account:Account,Arn:Arn}'
```

Record package hash/version for repeatable host provisioning. If signature verification fails, stop; do not run the installer.

Clone **your fork** privately at `/opt/apexforge360`. After Lab09 prepares inventory and attaches app IAM, create `/etc/cloudops/environment.json` via a private SSM editor, root-owned0600, then:

```bash
sudo install -d -m 700 /etc/cloudops
sudo chmod 600 /etc/cloudops/environment.json
sudo env CLOUDOPS_CONFIG_FILE=/etc/cloudops/environment.json \
  python3 /opt/apexforge360/deploy/prepare-runtime.py
sudo stat -c '%U:%G:%a' /etc/cloudops/runtime.env
sudo docker ps --all --no-trunc
```

The preparer validates app role/account, retrieves secrets without displaying them, writes only approved production settings and refuses an existing runtime file. It starts no container. Install CloudWatch Agent in Lab08. For existing hosts inspect privately; do not overwrite runtime state.

## Expected output and validation

Docker active, SSM Online, app role identity, no public IP, empty Docker inventory, runtime root:root:600. No healthy ALB target exists until approved image/schema startup succeeds.

## Troubleshooting and root causes

SSM bootstrap failure: missing Snap/package egress or role. Use authorized recovery/diagnostics; do not expose SSH. Signing mismatch: wrong secret/old runtime; inspect privately. Pull failure: ECR+S3 reachability or role, not a reason to use mutable tags.

## Security considerations

No SSH key pair is required. Docker permission equals host administration. Do not reuse private images, runtime files or unknown containers. Host setup versions are recorded independently from pinned application build inputs.

## Cleanup

After target deregistration, backups and approval, terminate only recorded lab instances. Inspect DeleteOnTermination and preserve diagnostic volumes if investigating a failure. Do not prune active rollback images/volumes.

## Interview questions with answers

**Why host networking?** It supports IMDSv2 hop-limit1 role access; candidate Gunicorn still binds only loopback5001.
**Can a first installation restore an old app?** No. Explicit initial-install approval permits an empty host; failures stop/retain only new owned containers. Subsequent releases retain the exact prior application.
