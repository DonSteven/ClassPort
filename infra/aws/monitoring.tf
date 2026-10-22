resource "aws_sns_topic" "operations" {
  name = "${var.name}-operations"
}

resource "aws_sns_topic_subscription" "operations_email" {
  count     = var.operations_email == null ? 0 : 1
  topic_arn = aws_sns_topic.operations.arn
  protocol  = "email"
  endpoint  = var.operations_email
}

# No email is sent until an approved apply; the recipient must confirm the subscription.
resource "aws_cloudwatch_metric_alarm" "operations" {
  for_each = {
    api_unavailable = { metric = "APIReady", comparison = "LessThanThreshold", threshold = 1 }
    disk_full       = { metric = "DiskUsedPercent", comparison = "GreaterThanThreshold", threshold = 85 }
    backup_overdue  = { metric = "BackupAgeSeconds", comparison = "GreaterThanThreshold", threshold = 93600 }
    backup_failed   = { metric = "BackupFailed", comparison = "GreaterThanThreshold", threshold = 0 }
  }
  alarm_name          = "${var.name}-${each.key}"
  namespace           = "ClassPort/Operations"
  metric_name         = each.value.metric
  comparison_operator = each.value.comparison
  threshold           = each.value.threshold
  period              = 60
  evaluation_periods  = 5
  statistic           = "Maximum"
  treat_missing_data  = "breaching"
  dimensions          = { Environment = var.name }
  alarm_actions       = [aws_sns_topic.operations.arn]
  ok_actions          = [aws_sns_topic.operations.arn]
}

resource "aws_cloudwatch_log_metric_filter" "api_errors" {
  name           = "${var.name}-api-errors"
  log_group_name = aws_cloudwatch_log_group.containers.name
  pattern        = "{ $.event = \"request.completed\" && $.status >= 500 }"
  metric_transformation {
    name          = "APIErrors"
    namespace     = "ClassPort/${var.name}"
    value         = "1"
    default_value = 0
  }
}

resource "aws_cloudwatch_metric_alarm" "api_errors" {
  alarm_name          = "${var.name}-api-errors"
  namespace           = "ClassPort/${var.name}"
  metric_name         = "APIErrors"
  comparison_operator = "GreaterThanOrEqualToThreshold"
  threshold           = 5
  period              = 300
  evaluation_periods  = 1
  statistic           = "Sum"
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.operations.arn]
}

# Attach to an approved recovery identity/isolated restore host, not the source host.
resource "aws_iam_policy" "recovery" {
  name = "${var.name}-read-recovery-data"
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow", Action = ["s3:ListBucket", "s3:ListBucketVersions"]
        Resource  = aws_s3_bucket.backups.arn
        Condition = { StringLike = { "s3:prefix" = "backups/*" } }
      },
      {
        Effect   = "Allow", Action = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = "${aws_s3_bucket.backups.arn}/backups/*"
      },
      {
        Effect   = "Allow", Action = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.config.arn
      },
      {
        Effect = "Allow", Action = ["ecr:GetAuthorizationToken"], Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["ecr:BatchCheckLayerAvailability", "ecr:GetDownloadUrlForLayer", "ecr:BatchGetImage"]
        Resource = [for repository in aws_ecr_repository.images : repository.arn]
      }
    ]
  })
}
