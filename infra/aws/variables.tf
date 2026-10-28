variable "aws_region" {
  type    = string
  default = "us-east-2"
}

variable "operations_email" {
  description = "Optional operations email. Applying it sends an SNS subscription confirmation."
  type        = string
  default     = null
  validation {
    condition     = var.operations_email == null || can(regex("^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$", var.operations_email))
    error_message = "Provide a notification email address or null."
  }
}

variable "name" {
  description = "Unique environment name in this account/region. Also used in AWS resource names."
  type        = string
  default     = "classport-prod"
  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,30}[a-z0-9]$", var.name))
    error_message = "Use 4–32 lowercase letters, digits or hyphens, starting with a letter and ending with a letter/digit."
  }
}

variable "domain_name" {
  description = "Optional public HTTPS hostname, without scheme/path. Leave null until DNS is ready; publishing is then disabled."
  type        = string
  default     = null
  validation {
    condition     = var.domain_name == null || can(regex("^([a-z0-9]([a-z0-9-]*[a-z0-9])?\\.)+[a-z]{2,}$", var.domain_name))
    error_message = "Provide a lowercase DNS hostname or leave it null."
  }
}

variable "acme_email" {
  description = "Optional email for ACME certificate account notices."
  type        = string
  default     = null
  validation {
    condition     = var.acme_email == null || can(regex("^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$", var.acme_email))
    error_message = "Provide an email address or leave it null."
  }
}

variable "instance_type" {
  description = "x86-64 instance (release images are linux/amd64)."
  type        = string
  default     = "t3.small"
}

variable "data_volume_size" {
  description = "Persistent course/upload disk size in GiB."
  type        = number
  default     = 30
  validation {
    condition     = var.data_volume_size >= 1 && floor(var.data_volume_size) == var.data_volume_size
    error_message = "Provide a positive whole GiB size."
  }
}

variable "db_instance_class" {
  type    = string
  default = "db.t4g.micro"
}

variable "deletion_protection" {
  description = "Protect RDS from deletion. A final snapshot is required even when this is disabled."
  type        = bool
  default     = true
}
