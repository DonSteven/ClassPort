output "aws_region" {
  value = var.aws_region
}

output "instance_id" {
  value = aws_instance.app.id
}
output "public_ip" {
  value = aws_eip.app.public_ip
}
output "public_url" {
  description = "HTTPS application origin; empty until domain_name is configured."
  value       = local.public_url
}
output "backend_repository" {
  value = aws_ecr_repository.images["backend"].repository_url
}
output "frontend_repository" {
  value = aws_ecr_repository.images["frontend"].repository_url
}
output "config_secret_arn" {
  value = aws_secretsmanager_secret.config.arn
}
output "backup_bucket" {
  value = aws_s3_bucket.backups.id
}
output "database_endpoint" {
  value = aws_db_instance.postgres.address
}
output "data_volume_id" {
  value = aws_ebs_volume.data.id
}
output "log_group" {
  value = aws_cloudwatch_log_group.containers.name
}

output "operations_topic_arn" {
  description = "Subscribe the approved operations destination to this topic."
  value       = aws_sns_topic.operations.arn
}

output "recovery_policy_arn" {
  description = "Read source backup versions/configuration/images from an isolated recovery identity."
  value       = aws_iam_policy.recovery.arn
}

output "github_deploy_role_arn" {
  description = "GitHub Actions AWS_DEPLOY_ROLE_ARN variable; null until OIDC is configured."
  value       = try(aws_iam_role.github[0].arn, null)
}
