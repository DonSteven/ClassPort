resource "aws_s3_bucket" "materials" {
  bucket        = "${var.name}-materials-${local.account}-${var.aws_region}"
  force_destroy = false
}

resource "aws_s3_bucket_public_access_block" "materials" {
  bucket                  = aws_s3_bucket.materials.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "materials" {
  bucket = aws_s3_bucket.materials.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_versioning" "materials" {
  bucket = aws_s3_bucket.materials.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "materials" {
  bucket = aws_s3_bucket.materials.id
  rule {
    apply_server_side_encryption_by_default { sse_algorithm = "AES256" }
  }
}

resource "aws_s3_bucket_policy" "materials" {
  bucket = aws_s3_bucket.materials.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Deny", Principal = "*", Action = "s3:*"
      Resource  = [aws_s3_bucket.materials.arn, "${aws_s3_bucket.materials.arn}/*"]
      Condition = { Bool = { "aws:SecureTransport" = "false" } }
    }]
  })
}

resource "aws_s3_bucket_cors_configuration" "materials" {
  count  = var.domain_name == null ? 0 : 1
  bucket = aws_s3_bucket.materials.id
  cors_rule {
    allowed_origins = [local.public_url]
    allowed_methods = ["PUT", "GET", "HEAD"]
    allowed_headers = ["content-type", "x-amz-checksum-sha256", "range", "if-range"]
    expose_headers  = ["x-amz-version-id", "x-amz-checksum-sha256", "ETag", "Content-Range", "Accept-Ranges"]
    max_age_seconds = 300
  }
}

resource "aws_iam_role" "storage" {
  name = "${var.name}-materials"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow", Action = "sts:AssumeRole"
      Principal = { AWS = aws_iam_role.app.arn }
    }]
  })
}

resource "aws_iam_role_policy" "storage" {
  role = aws_iam_role.storage.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      { Effect = "Allow", Action = ["s3:GetBucketVersioning"], Resource = aws_s3_bucket.materials.arn },
      {
        Effect    = "Allow", Action = ["s3:ListBucket"], Resource = aws_s3_bucket.materials.arn
        Condition = { StringLike = { "s3:prefix" = ["objects/*"] } }
      },
      {
        Effect   = "Allow", Action = ["s3:PutObject", "s3:GetObjectVersion", "s3:DeleteObjectVersion"]
        Resource = "${aws_s3_bucket.materials.arn}/objects/*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "assume_storage" {
  role = aws_iam_role.app.id
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "sts:AssumeRole", Resource = aws_iam_role.storage.arn }]
  })
}

output "materials_bucket" { value = aws_s3_bucket.materials.id }
output "storage_role_arn" { value = aws_iam_role.storage.arn }
