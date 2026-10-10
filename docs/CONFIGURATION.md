# Configuration contract

[Step-by-step inventory and policies](labs/09-inventory-policies.md) · [Schema](../deploy/config.schema.json)

Copy `deploy/config.example.json` into an ignored private file and replace every example using independently reviewed account metadata. Public example_only=true fails authorization. Jenkins defaults to AWS_OPERATIONS=false / DEPLOY_TARGET=none and needs no inventory for CI. Keep application and Jenkins inventory copies private0600; no credentials or secret values belong in either.

Required fields: account/region/tools role/app role/profile name+ARN; ECR repository; VPC/app SG; exact CANARY_INSTANCE_ID and OBSERVABILITY_INSTANCE_ID; two distinct private application subnet IDs; ALB/TG name+ARN; one RDS instance identifier; DB/signing secret names; log group; source GitHub fork; secure-cookie flag; exact legacy name/image; ALLOW_INITIAL_INSTALL.

ALLOW_INITIAL_INSTALL defaults false. True permits an explicitly approved clean empty-host first install only. Approval displays it; reset false afterward. Initial failure stops/retains the failed new containers but cannot restore a nonexistent prior app. Existing releases always preserve exact prior identity/configuration.

Jenkins global configuration: CLOUDOPS_CONFIG_FILE points inside jenkins named volume; CLOUDOPS_DEPLOY_APPROVERS contains designated IDs; agent labels linux docker. Sonar server sonarqube and secret-text credential sonarqube-token. Do not mount static AWS credential files: AWS containers use host networking and the tools EC2 role. Application containers use the separate app role.

Use cloudops_config.py for semantic validation and render-iam-policy.py for private JSON output. Tools cannot launch/terminate instances or modify the database. Account/region/profile/TG mismatches and unknown fields fail closed. Policies are rendered offline; owner/operator applies them separately. Customer-managed secret keys require separately reviewed KMS decrypt permissions.
