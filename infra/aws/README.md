# Minimal AWS deployment

This creates one Ubuntu 24.04 x86-64 EC2 host, an encrypted persistent EBS disk,
private single-AZ PostgreSQL 16 RDS, two immutable ECR repositories, a private
versioned backup bucket, Secrets Manager configuration, and CloudWatch logs.
The VPC has one public app subnet and two private database subnets. Only TCP
80/443 are public; PostgreSQL accepts only the app security group. Administration
uses Systems Manager Session Manager; no SSH key or inbound SSH port is needed.
Caddy obtains and renews a public HTTPS certificate once a real domain is
configured, and forwards to the existing frontend on `127.0.0.1:8080`. With no
domain it serves only a configuration notice on HTTP, requests no certificates
and blocks application releases. Existing app routes and MCP streaming are preserved.
The AWS region defaults to `us-east-1` and can be overridden with `aws_region`.
DNS is managed in Cloudflare; this module creates no Route 53 resources.

This is a small single-host installation, without high availability or a NAT
gateway. EC2, EBS, RDS, public IPv4, Secrets Manager, logs and S3 usage incur AWS
charges. Creating resources, uploading secrets/images, changing DNS and deploying
must be approved by the operator before execution.

## Files and dependencies

| Files | Responsibility |
|---|---|
| `main.tf`, `iam.tf`, `variables.tf`, `outputs.tf` | AWS resources and restricted instance permissions |
| `cloud-init.yaml`, `scripts/setup-host.sh` | Install Docker Compose/Caddy/AWS CLI, identify and mount EBS, configure HTTPS/logging |
| `scripts/create-config.py` | Generate initial secrets locally with hidden password input |
| `scripts/bootstrap.py` | Provision DB identities, apply existing migrations and initialize operator/release checks |
| `scripts/release.sh`, `scripts/publish-images.sh` | Publish committed source to ECR and invoke the existing release process |
| `tests/environment.tftest.hcl`, `../../tests/test_aws_bootstrap.py` | Mock AWS tests and real local PostgreSQL permissions/bootstrap tests |

The host embeds the repository's `docker-compose.prod.yml` and `deploy-prod.sh`.
It downloads images rather than building application source on EC2. No app API or
schema migrations are changed by this infrastructure module.

## Local validation (no AWS account)

These commands install provider dependencies and check the local configuration.
Tests use mocked AWS responses and cannot contact AWS or create resources.
No domain, certificate request or AWS credentials are needed.

```bash
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws init -backend=false -input=false
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws test
```

## 1. Future AWS credentials and provisioning

Prerequisites: an AWS account with authenticated CLI credentials and permission
to create the declared VPC, EC2, IAM, RDS, ECR, S3, Secrets Manager and logs
resources; Terraform 1.13.5+; local Docker Buildx and Python 3
for publishing. For interactive SSM access install the AWS Session Manager plugin
locally. This module targets standard commercial AWS regions with two AZs.

Prefer an AWS CLI profile using IAM Identity Center/SSO or another short-lived
credential source. For example, configure and log in with `aws configure sso
--profile classport`, then set `AWS_PROFILE=classport` in the shell running
Terraform, publishing and SSM. Terraform and AWS CLI use the normal credential
chain; never put access keys in `.tfvars`, user data or Git. `aws sts
get-caller-identity` can confirm the intended account without printing keys.
The provisioning identity needs IAM/PassRole permissions and access to the listed
services; the publisher needs ECR push rights, the configuration operator needs
`secretsmanager:PutSecretValue` on this application secret, and the administrator
needs SSM session rights. EC2 receives its own separate instance role.

Once the account and real domain are ready, from the repository root:

```bash
cp infra/aws/terraform.tfvars.example infra/aws/terraform.tfvars
# Set a unique environment name and your real domain_name; acme_email is optional.
# Override aws_region if needed (default us-east-1).
terraform -chdir=infra/aws init
terraform -chdir=infra/aws fmt -check -recursive
terraform -chdir=infra/aws validate
terraform -chdir=infra/aws test
terraform -chdir=infra/aws plan -out=environment.tfplan
# After operator approval of the plan and ongoing charges:
terraform -chdir=infra/aws apply environment.tfplan
```

State/plan files and local variable files are ignored by Git. Keep state in a
restricted directory, or configure a separately provisioned encrypted remote
backend with locking before the first apply. The backup bucket here is for app
backups, not a bootstrap backend. Commit `.terraform.lock.hcl` for reproducible
provider installation. Secret values never enter Terraform: RDS manages its
administrator password, and Terraform creates only application-secret metadata.

`domain_name` defaults to null. It is safe to leave it unset while preparing the
code or provisioning a host; no example hostname is configured for ACME. A real
application release requires it. Setting/changing it updates cloud-init and thus
replaces EC2 on the next approved apply; EBS and the public EIP are retained.

## Cloudflare DNS, HTTPS and PUBLIC_URL

After an approved apply, create an **A** record in the Cloudflare zone: name
`study`, content equal to Terraform's `public_ip`, initially **DNS only** (gray
cloud). Remove conflicting A/AAAA records. This does not require a Cloudflare API
token on EC2 or in Terraform. Wait until `study.<your-domain>` resolves publicly
to that IP. Leave inbound TCP 80 and 443 reachable; Caddy uses ACME HTTP/TLS
challenges and renews its certificate automatically. Do not substitute an example
hostname in a real apply.

Set `domain_name` to that exact real hostname, without `https://` or a trailing
slash, before the approved apply that will enable HTTPS. `acme_email` is optional.
The host's `/etc/caddy/Caddyfile` is generated from these values. Initial DNS
propagation may delay issuance; check `sudo journalctl -u caddy`. Verify public
HTTPS before publishing the app. DNS can remain in DNS-only mode. If Cloudflare's
proxy is enabled later, set SSL/TLS mode to **Full (strict)** so Cloudflare verifies
Caddy's origin certificate, and rerun the public release checks.
See [Cloudflare proxy status](https://developers.cloudflare.com/dns/proxy-status/)
and [Full (strict)](https://developers.cloudflare.com/ssl/origin-configuration/ssl-modes/full-strict/).

Terraform derives the `public_url` output as `https://<domain_name>` (empty while
unset). The host puts this same origin in backend `PUBLIC_URL`, `CORS_ORIGINS` and
smoke `SMOKE_BASE_URL`. The publisher bakes it into frontend `VITE_SITE_URL`.
This keeps login cookies, OAuth redirects, verification links and frontend metadata
on one origin. A later domain change requires a frontend rebuild and a release
to regenerate backend settings; editing only the DNS record is insufficient.

## 2. Supply application configuration

Generate this **once for a new installation**, then upload to the secret created
by Terraform. Use a different email for the dedicated release-check account.
The operator password is entered without echoing it or putting it in arguments.

```bash
python3 infra/aws/scripts/create-config.py \
  --operator-email admin@example.com --smoke-email checks@example.com \
  --output infra/aws/application-secret.json
export AWS_REGION
AWS_REGION=$(terraform -chdir=infra/aws output -raw aws_region)
aws secretsmanager put-secret-value \
  --secret-id "$(terraform -chdir=infra/aws output -raw config_secret_arn)" \
  --secret-string file://infra/aws/application-secret.json \
  --query ARN --output text
rm infra/aws/application-secret.json
```

The instance role can read only this application secret and its RDS administrator
secret; it cannot change either. Both use AWS-managed service encryption keys.
Do not add secret-version data sources to Terraform. Never regenerate the
Fernet encryption key or session/database passwords on normal releases. Keep the
application secret for recovery; changing the Fernet key would make stored user
integration secrets unreadable. Automatic app credential rotation is not configured.

## 3. Publish and deploy

Publish a committed source revision after operator approval. The helper builds a
clean archive of that commit, sets the frontend's public HTTPS origin and pushes
the two `linux/amd64` images. It prints one command with immutable image digests.
Use a new commit for subsequent releases; ECR tags are immutable.

```bash
bash infra/aws/scripts/publish-images.sh HEAD
aws ssm start-session \
  --target "$(terraform -chdir=infra/aws output -raw instance_id)" \
  --region "$AWS_REGION"
```

In the SSM session, wait for host initialization, then run the printed command:

```bash
sudo cloud-init status --wait
sudo test -f /opt/classport/.host-ready
sudo classport-release BACKEND_ECR_URL@sha256:DIGEST FRONTEND_ECR_URL@sha256:DIGEST COMMIT_SHA
```

The host pulls using its IAM role, reads secrets into a root-only temporary
directory under `/run`, provisions separate runtime/migration DB accounts, applies
the image's existing migrations, seeds the operator, and initializes a separate
release-check user and a 30-day MCP token. Each AWS release refreshes only that
check account/token. Existing operator password hashes are preserved by the seed
script. The host writes `.env`, `.env.migration` and `.env.smoke` with mode `0600`;
plaintext operator/master passwords are not written to those files. Temporary
secret files are removed when the command exits.

The runtime login has DML grants, no DDL/role administration or migration-table
access, and no membership in the migration role. To preserve the current app's
application-level tenant isolation on RDS, a role-specific permissive policy is
added to existing RLS tables. This does not enforce database-level tenant RLS;
see [the existing RLS design](../../docs/RLS.md). RDS certificate/hostname
verification is enabled for both runtime and maintenance connections.

The backend/frontend run on the Docker bridge. IMDSv2 is required with hop limit
1 so these containers cannot retrieve the host's Secrets Manager permissions.
The short-lived maintenance container uses host networking for initial setup;
the host and its administrators are trusted. No AWS static keys are stored on it.

## Acceptance and operations

The existing release script must finish successfully: it checks readiness, the
public **HTTPS** login page and assets, real password login, authenticated course
API, MCP initialization and a course-list tool call. TLS verification remains
enabled. Then open the `public_url` output, log in with the operator email/password,
create a course, upload/read a file and refresh to confirm persistence. These user
writes should be verified against the deployed AWS environment before sign-off.
An apply alone does not demonstrate end-to-end acceptance.

Container stdout/stderr goes to the `log_group` output with 14-day retention.
Host setup diagnostics: `sudo journalctl -u cloud-final` and
`sudo journalctl -u caddy`. No access logging of OAuth query strings is enabled.
All releases must use `classport-release` to renew ECR authentication and read
current configuration. The existing GitHub workflow still publishes to GHCR;
it is not the publisher for this ECR/SSM path.

Rollback in an SSM session:

```bash
sudo bash /opt/classport/releases/current/deploy-prod.sh --rollback
```

RDS keeps seven days of automated backups. The S3 backup bucket is private,
versioned, encrypted and HTTPS-only. The instance can upload backup objects but
cannot read/delete them. This change creates the destination; it does not schedule
file backups or copy RDS backups to S3. Set a backup/recovery procedure appropriate
to your data before relying on this installation for irreplaceable files.

EBS is separate from EC2 and has `prevent_destroy`; Docker requires its mount
before starting on reboot. On initial boot only an unsigned empty disk matching
the exact EBS volume ID is formatted; an existing ext4 volume is mounted intact.
Cloud-init changes replace EC2 while preserving that volume and EIP. The new host
needs `classport-release` again to restore the running containers and host config.
Instance resize and disk growth/filesystem expansion are operator maintenance.

RDS deletion protection defaults on, final snapshots are mandatory, and S3/ECR
contents are never force-deleted. `terraform destroy` is intentionally blocked by
EBS protection; planned teardown requires backup verification and an explicit edit
to remove that protection. The final RDS snapshot name is `<name>-final`; preserve
or rename an existing snapshot before reusing that name for another teardown.
Do not use destroy as an application rollback.

Reference installation behavior: [Docker on Ubuntu](https://docs.docker.com/engine/install/ubuntu/),
[Caddy packages](https://caddyserver.com/docs/install#debian-ubuntu-raspbian),
[RDS administrator privileges](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Appendix.PostgreSQL.CommonDBATasks.Roles.rds_superuser.html).
