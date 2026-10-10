# 04 — One MariaDB RDS and runtime secrets

[Master guide](../MASTER-GUIDE.md) · Commands are for your own reviewed lab account.

## Architecture and purpose
![One database](../diagrams/03-rds.png)
[Editable SVG](../diagrams/03-rds.svg). One private **Single-AZ** MariaDB DB instance; one DB subnet group with DB A and DB B. Two group members are **not two databases**. RDS requires subnet-group coverage of two AZs even for this deployment ([AWS documentation](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/USER_VPC.WorkingWithRDSInstanceinaVPC.html)).

## Prerequisites
Labs01–03, DB subnet and SG IDs. Review region/class/engine availability and budget. RDS creation may take several minutes. Use the RDS-managed master secret; never put a password in CLI history.

## Configuration steps

```bash
aws rds create-db-subnet-group --db-subnet-group-name apexforge-db \
  --db-subnet-group-description 'Two private DB subnets; one Single-AZ instance' \
  --subnet-ids "$DB_A" "$DB_B"
aws rds describe-orderable-db-instance-options --engine mariadb --db-instance-class db.t3.micro \
  --query 'OrderableDBInstanceOptions[].EngineVersion' --output text
# Set a reviewed version from the result; do not copy a stale version number.
read -r -p 'Reviewed MariaDB engine version: ' DB_ENGINE_VERSION
aws rds create-db-instance --db-instance-identifier apexforge-db --engine mariadb \
  --engine-version "$DB_ENGINE_VERSION" --db-instance-class db.t3.micro --allocated-storage 20 \
  --storage-type gp3 --storage-encrypted --db-name cloudops --master-username cloudopsadmin \
  --manage-master-user-password --db-subnet-group-name apexforge-db --vpc-security-group-ids "$DB_SG" \
  --no-multi-az --no-publicly-accessible --backup-retention-period 7 --deletion-protection \
  --tags Key=Project,Value="$PROJECT"
aws rds wait db-instance-available --db-instance-identifier apexforge-db
aws rds describe-db-instances --db-instance-identifier apexforge-db \
  --query 'DBInstances[0].{Endpoint:Endpoint.Address,MultiAZ:MultiAZ,Public:PubliclyAccessible,MasterSecret:MasterUserSecret.SecretArn}'
```
The master secret is for initial database administration, **not** the app role. This administration step waits until Lab06 supplies a private SSM-managed host. In that shell install the client with `sudo apt-get update && sudo apt-get install -y mariadb-client`, set `DB_HOST` to the verified RDS DNS endpoint and create a least-privilege `cloudopsapp` user for database `cloudops`. Connect using `mariadb --host "$DB_HOST" --user cloudopsadmin --password --ssl --ssl-verify-server-cert --ssl-ca /path/to/reviewed/rds-global-bundle.pem`; `--password` prompts, it does not put a password in history. Download the current AWS RDS CA bundle from [AWS truststore](https://truststore.pki.rds.amazonaws.com/global/global-bundle.pem) and verify the server certificate. Retrieve the RDS-managed master secret privately through an authorized operator; do not grant that secret to the app role. Use a private SQL file (0600, editor, no terminal echo) for:

```sql
CREATE USER 'cloudopsapp'@'%' IDENTIFIED BY '<your privately generated app password>';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, INDEX, ALTER, REFERENCES ON cloudops.* TO 'cloudopsapp'@'%';
```
`CREATE/ALTER` support explicit initial schema setup; reduce DDL privileges after schema creation if you implement reviewed migrations. `%` is limited by the DB SG; use a reviewed narrower database host policy if suitable. Do not paste the placeholder SQL into a shared terminal or reuse that literal value.

Create the app DB secret in a **private editor** as JSON: `host`, `port` (3306), `username` (`cloudopsapp`), `password`, `dbname` (`cloudops`). The loader requires those fields. This reference's application driver does not configure CA verification through its secret schema: the verified admin connection above does not establish app transport guarantees. Review and implement a dedicated app TLS configuration change before imposing RDS TLS-only enforcement; do not claim application certificate verification has been live-tested.

```bash
# Private workstation files, never under a shared or public directory.
install -d -m 700 "$HOME/.cloudops-lab"
# Use your editor to create $HOME/.cloudops-lab/database.json with the described real values.
chmod 600 "$HOME/.cloudops-lab/database.json"
aws secretsmanager create-secret --name apexforge/prod/mariadb \
  --secret-string "file://$HOME/.cloudops-lab/database.json"
# Generate one stable signing secret without displaying it.
python3 -c 'import secrets,sys; sys.stdout.write(secrets.token_urlsafe(48))' > "$HOME/.cloudops-lab/signing.txt"
chmod 600 "$HOME/.cloudops-lab/signing.txt"
aws secretsmanager create-secret --name apexforge/prod/flask-signing \
  --secret-string "file://$HOME/.cloudops-lab/signing.txt"
```
Delete local secret copies after approved persistence verification. Do not rotate the signing key each build: that invalidates sessions and breaks runtime preflight.

## Expected output and validation
`MultiAZ=false`, `Public=false`, engine MariaDB and one available DB instance. Verify the DB subnet group lists two distinct AZs. Secret outputs contain ARN/version metadata only; never run `get-secret-value` to a visible terminal. Runtime preparation in Lab06 validates secret shapes and stable signing key without printing them. Create tables using the exact application image under the app role before requiring `/ready` to succeed.

## Troubleshooting and root causes
Ready503: DB unreachable, username grant missing, wrong DB name or schema absent. Public accessibility is not a fix. Secret access denied: wrong secret ARN wildcard, region, IAM/KMS policy or role. Invalid shape: `database`/`dbname` missing, values not strings, bad port.

## Security considerations
Encrypt storage, retain backups, use deletion protection and restrict master-secret access to the administrator. If customer-managed KMS keys protect secrets, add exact key decrypt permission and key policy for the relevant role, never `kms:*`.

## Cleanup
Back up required lab data; disable deletion protection only after explicit owner review. Delete the **recorded** DB with a final snapshot; delete the subnet group after the DB is gone. Schedule only owned secrets for deletion with a recovery window. Do not rotate or delete shared secrets.

## Interview questions with answers
**Is a two-subnet group high availability?** No. With `MultiAZ=false` there is one DB process; subnet-group membership supplies placement options, not a standby.
**Why stable Flask signing?** Sessions must remain verifiable across deployments; a newly generated key on every boot logs users out.
