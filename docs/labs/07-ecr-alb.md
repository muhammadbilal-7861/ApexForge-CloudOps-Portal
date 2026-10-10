# 07 — Immutable ECR and HTTPS ALB

[Master guide](../MASTER-GUIDE.md). Operator commands for your owned lab only.

## Architecture and purpose

![Deployment](../diagrams/07-deployment-rollback.png)
[Editable SVG](../diagrams/07-deployment-rollback.svg). Immutable commit tags map to content digests. One private EC25000 receives ALB traffic; `/ready` gates target health.

## Prerequisites

Labs01–06, domain/DNS control for ACM TLS, owned app ID and SGs. Registering a clean host initially yields an unhealthy target; do not bypass readiness.

## Configuration steps

```bash
ECR_REPOSITORY=apexforge-cloudops-portal
aws ecr create-repository --repository-name "$ECR_REPOSITORY" \
  --image-tag-mutability IMMUTABLE --image-scanning-configuration scanOnPush=true
ECR_URI="$AWS_ACCOUNT_ID.dkr.ecr.$AWS_REGION.amazonaws.com/$ECR_REPOSITORY"
ALB_ARN=$(aws elbv2 create-load-balancer --name apexforge-alb --scheme internet-facing \
  --type application --subnets "$PUBLIC_A" "$PUBLIC_B" --security-groups "$ALB_SG" \
  --query 'LoadBalancers[0].LoadBalancerArn' --output text)
TARGET_GROUP_ARN=$(aws elbv2 create-target-group --name apexforge-app --protocol HTTP --port 5000 \
  --vpc-id "$VPC_ID" --target-type instance --health-check-path /ready \
  --health-check-protocol HTTP --matcher HttpCode=200 \
  --query 'TargetGroups[0].TargetGroupArn' --output text)
aws elbv2 register-targets --target-group-arn "$TARGET_GROUP_ARN" --targets "Id=$APP_INSTANCE_ID,Port=5000"
read -r -p 'Application DNS name you own: ' APP_DOMAIN
CERTIFICATE_ARN=$(aws acm request-certificate --domain-name "$APP_DOMAIN" --validation-method DNS \
  --query CertificateArn --output text)
aws acm describe-certificate --certificate-arn "$CERTIFICATE_ARN" \
  --query 'Certificate.DomainValidationOptions[].ResourceRecord'
```

Create the returned validation CNAME at your DNS provider, then:

```bash
aws acm wait certificate-validated --certificate-arn "$CERTIFICATE_ARN"
aws elbv2 create-listener --load-balancer-arn "$ALB_ARN" --protocol HTTPS --port 443 \
  --certificates "CertificateArn=$CERTIFICATE_ARN" \
  --default-actions "Type=forward,TargetGroupArn=$TARGET_GROUP_ARN"
aws elbv2 create-listener --load-balancer-arn "$ALB_ARN" --protocol HTTP --port 80 \
  --default-actions 'Type=redirect,RedirectConfig={Protocol=HTTPS,Port=443,StatusCode=HTTP_301}'
aws elbv2 describe-load-balancers --load-balancer-arns "$ALB_ARN" \
  --query 'LoadBalancers[0].DNSName' --output text
```

Point your app DNS CNAME/alias at the returned ALB name. Review the current ALB TLS policy before use. HTTP80 redirects; production secure cookies remain true. Jenkins publishes the scanned image in Lab10, using its role. Existing tags are reused only after pulled digest/platform image ID matches the actual build. Only ImageNotFound means absent; authorization/API failures stop publication.

## Expected output and validation

```bash
aws ecr describe-repositories --repository-names "$ECR_REPOSITORY" --query 'repositories[0].imageTagMutability'
aws elbv2 describe-target-health --target-group-arn "$TARGET_GROUP_ARN"
curl --fail "https://$APP_DOMAIN/health"
curl --fail "https://$APP_DOMAIN/ready"
```

After deployment: IMMUTABLE, exactly one healthy instance target, both HTTP200. Verify browser login/CSRF/cookies over HTTPS. Initial target unhealthy is expected before app/schema startup.

## Troubleshooting and root causes

Persistent unhealthy: wrong target/port, SG, app listener, DB access or missing schema. Tag conflict: image inputs changed; repair reproducibility or use a new reviewed commit. Never delete/overwrite a tag to force success.

## Security considerations

TLS and authentication protect edge access; SGs alone are insufficient. Review complete unfixed-CVE reports. Registry scanning supplements, rather than replaces, Jenkins image gates.

## Cleanup

Deregister the owned target before retirement. Delete recorded listeners/ALB/TG/certificate after DNS/dependencies review. Keep every digest used by running or rollback containers.

## Interview questions with answers

**Why tag plus digest?** Tags identify source; immutable digests bind deployment to content.
**Is health enough?** No; readiness checks DB reachability, and business flows require separate validation.
