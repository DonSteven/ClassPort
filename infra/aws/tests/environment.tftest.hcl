# All resources/data are mocked. These tests cannot create AWS resources.
mock_provider "aws" {
  override_during = plan
  mock_data "aws_availability_zones" {
    defaults = { names = ["us-east-1a", "us-east-1b"] }
  }
  mock_data "aws_ami" {
    defaults = { id = "ami-0123456789abcdef0", name = "ubuntu-noble-24.04-amd64-server-test" }
  }
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_partition" {
    defaults = { partition = "aws" }
  }
  mock_resource "aws_db_instance" {
    defaults = {
      address            = "database.example.rds.amazonaws.com"
      master_user_secret = [{ secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:rds-master-test", secret_status = "active", kms_key_id = "test" }]
    }
  }
  mock_resource "aws_secretsmanager_secret" {
    defaults = { arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:application-test" }
  }
  mock_resource "aws_ecr_repository" {
    defaults = {
      arn            = "arn:aws:ecr:us-east-1:123456789012:repository/classport"
      repository_url = "123456789012.dkr.ecr.us-east-1.amazonaws.com/classport"
    }
  }
  mock_resource "aws_ebs_volume" {
    defaults = { id = "vol-0123456789abcdef0" }
  }
  mock_resource "aws_instance" {
    defaults = { id = "i-0123456789abcdef0" }
  }
  mock_resource "aws_iam_openid_connect_provider" {
    defaults = { arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com" }
  }
  mock_resource "aws_cloudwatch_log_group" {
    defaults = { arn = "arn:aws:logs:us-east-1:123456789012:log-group:/classport/containers" }
  }
  mock_resource "aws_s3_bucket" {
    defaults = { arn = "arn:aws:s3:::classport-backups" }
  }
  override_resource {
    target          = aws_security_group.app
    override_during = plan
    values          = { id = "sg-0123456789abcdef0" }
  }
}

run "github_deployment_is_scoped_to_one_production_subject" {
  command = plan
  variables {
    github_oidc_subject = "repo:example@123/ClassPort@456:environment:production"
  }
  assert {
    condition = (
      length(aws_iam_openid_connect_provider.github) == 1 &&
      jsondecode(aws_iam_role.github[0].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:sub"] == var.github_oidc_subject &&
      jsondecode(aws_iam_role.github[0].assume_role_policy).Statement[0].Condition.StringEquals["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"
    )
    error_message = "GitHub trust must use the exact production subject and STS audience."
  }
  assert {
    condition = (
      length(jsondecode(aws_iam_role_policy.github[0].policy).Statement[1].Resource) == 2 &&
      jsondecode(aws_iam_role_policy.github[0].policy).Statement[2].Resource == [
        "arn:aws:ssm:us-east-1::document/AWS-RunShellScript",
        "arn:aws:ec2:us-east-1:123456789012:instance/i-0123456789abcdef0"
      ] &&
      !strcontains(aws_iam_role_policy.github[0].policy, "secretsmanager:") &&
      !strcontains(aws_iam_role_policy.github[0].policy, "iam:")
    )
    error_message = "Release permissions must target this image pair and instance without configuration or IAM access."
  }
}

run "existing_github_provider_is_reused" {
  command = plan
  variables {
    github_oidc_subject      = "repo:example/ClassPort:environment:production"
    github_oidc_provider_arn = "arn:aws:iam::123456789012:oidc-provider/token.actions.githubusercontent.com"
  }
  assert {
    condition = (
      length(aws_iam_openid_connect_provider.github) == 0 &&
      jsondecode(aws_iam_role.github[0].assume_role_policy).Statement[0].Principal.Federated == var.github_oidc_provider_arn
    )
    error_message = "An existing account-wide GitHub identity provider must not be recreated."
  }
}

variables {
  domain_name = "study.example.com"
  acme_email  = "admin@example.com"
}

run "minimum_private_environment" {
  command = plan

  assert {
    condition = (
      length(aws_instance.app.user_data_base64) <= 21848 &&
      length(yamldecode(local.cloud_init).write_files) == 5 &&
      !strcontains(local.cloud_init, "/opt/classport/aws/")
    )
    error_message = "Cloud-init must be valid YAML and fit EC2's 16 KiB compressed user-data limit."
  }
  assert {
    condition = (
      !aws_db_instance.postgres.publicly_accessible &&
      aws_db_instance.postgres.storage_encrypted &&
      aws_db_instance.postgres.manage_master_user_password &&
      aws_db_instance.postgres.backup_retention_period == 7 &&
      !aws_db_instance.postgres.skip_final_snapshot
    )
    error_message = "RDS must be private, encrypted, backed up and use an AWS-managed administrator secret."
  }
  assert {
    condition = (
      aws_vpc_security_group_ingress_rule.postgres.referenced_security_group_id == aws_security_group.app.id &&
      aws_vpc_security_group_ingress_rule.postgres.cidr_ipv4 == null &&
      aws_vpc_security_group_ingress_rule.postgres.from_port == 5432 &&
      length(aws_vpc_security_group_ingress_rule.web) == 2
    )
    error_message = "Only the app security group may reach PostgreSQL; public inbound ports must be 80/443."
  }
  assert {
    condition = (
      aws_s3_bucket_public_access_block.backups.block_public_acls &&
      aws_s3_bucket_public_access_block.backups.block_public_policy &&
      aws_s3_bucket_public_access_block.backups.ignore_public_acls &&
      aws_s3_bucket_public_access_block.backups.restrict_public_buckets &&
      !aws_s3_bucket.backups.force_destroy
    )
    error_message = "Backups must remain private and cannot be emptied automatically on destroy."
  }
  assert {
    condition = (
      aws_instance.app.metadata_options[0].http_tokens == "required" &&
      aws_instance.app.metadata_options[0].http_put_response_hop_limit == 1 &&
      aws_ebs_volume.data.encrypted
    )
    error_message = "Require IMDSv2, deny bridge-container role access and encrypt the data disk."
  }
  assert {
    condition = (
      length(jsondecode(aws_iam_role_policy.app.policy).Statement[2].Resource) == 2 &&
      jsondecode(aws_iam_role_policy.app.policy).Statement[2].Action == ["secretsmanager:GetSecretValue"] &&
      length(jsondecode(aws_iam_role_policy.app.policy).Statement[1].Resource) == 2
    )
    error_message = "Scope configuration reads and ECR pulls to this environment's resources."
  }
}

run "domain_can_be_unconfigured" {
  command = plan
  variables {
    domain_name = null
    acme_email  = null
  }
  assert {
    condition = (
      output.public_url == "" &&
      strcontains(local.caddyfile, "auto_https off") &&
      !strcontains(local.caddyfile, "reverse_proxy") &&
      strcontains(local.host_env, "PUBLIC_URL=\n")
    )
    error_message = "No configured domain means no ACME certificate requests or public application proxy."
  }
}

run "operations_and_recovery_are_scoped" {
  command = plan
  variables {
    operations_email = "alerts@example.test"
  }
  assert {
    condition = (
      length(aws_cloudwatch_metric_alarm.operations) == 8 &&
      aws_cloudwatch_metric_alarm.operations["jobs_waiting"].threshold == 300 &&
      aws_cloudwatch_metric_alarm.operations["dispatch_stuck"].metric_name == "JobOutboxAgeSeconds" &&
      aws_cloudwatch_metric_alarm.operations["recovery_stuck"].metric_name == "JobExpiredLeases" &&
      aws_cloudwatch_metric_alarm.operations["backup_overdue"].threshold == 93600 &&
      aws_cloudwatch_metric_alarm.operations["api_unavailable"].treat_missing_data == "breaching" &&
      aws_sns_topic_subscription.operations_email[0].protocol == "email" &&
      aws_sns_topic_subscription.operations_email[0].endpoint == var.operations_email
    )
    error_message = "Operations must detect missing metrics and overdue backups and use the chosen email subscription."
  }
  assert {
    condition = (
      !strcontains(aws_iam_role_policy.app.policy, "s3:GetObject") &&
      strcontains(aws_iam_policy.recovery.policy, "s3:GetObjectVersion") &&
      strcontains(aws_iam_policy.recovery.policy, "s3:ListBucketVersions") &&
      strcontains(aws_iam_role_policy.app.policy, "cloudwatch:namespace")
    )
    error_message = "Source backup writes, recovery reads and namespaced metrics must retain separate permissions."
  }
}
