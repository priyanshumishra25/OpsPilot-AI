# Document artifacts (S3) and the ingestion queue (SQS with a dead-letter queue).

variable "name" { type = string }
variable "kms_key_arn" { type = string }
variable "force_destroy" {
  description = "Allow deleting a non-empty bucket on destroy (dev only)."
  type        = bool
  default     = false
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${var.name}-artifacts-"
  force_destroy = var.force_destroy
  tags          = var.tags
  #checkov:skip=CKV_AWS_144:Single-region deployment; versioning provides recovery from deletion
  #checkov:skip=CKV2_AWS_62:No consumer for bucket events; ingestion is driven by SQS jobs created by the API
  #checkov:skip=CKV_AWS_18:Object access is audited in the application audit log; enable CloudTrail data events for S3 if required
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule { object_ownership = "BucketOwnerEnforced" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration { status = "Enabled" }
}

resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration { noncurrent_days = 30 }
    abort_incomplete_multipart_upload { days_after_initiation = 1 }
  }
}

data "aws_iam_policy_document" "tls_only" {
  statement {
    sid     = "DenyInsecureTransport"
    effect  = "Deny"
    actions = ["s3:*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    resources = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/*"]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = data.aws_iam_policy_document.tls_only.json
}

resource "aws_sqs_queue" "ingestion_dlq" {
  name                      = "${var.name}-ingestion-dlq"
  message_retention_seconds = 1209600
  kms_master_key_id         = var.kms_key_arn
  tags                      = var.tags
}

resource "aws_sqs_queue" "ingestion" {
  name                       = "${var.name}-ingestion"
  visibility_timeout_seconds = 900
  receive_wait_time_seconds  = 20
  kms_master_key_id          = var.kms_key_arn
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.ingestion_dlq.arn
    maxReceiveCount     = 10
  })
  tags = var.tags
}

output "bucket_name" { value = aws_s3_bucket.artifacts.bucket }
output "bucket_arn" { value = aws_s3_bucket.artifacts.arn }
output "queue_url" { value = aws_sqs_queue.ingestion.url }
output "queue_arn" { value = aws_sqs_queue.ingestion.arn }
output "dlq_url" { value = aws_sqs_queue.ingestion_dlq.url }
output "dlq_arn" { value = aws_sqs_queue.ingestion_dlq.arn }
output "dlq_name" { value = aws_sqs_queue.ingestion_dlq.name }
output "queue_name" { value = aws_sqs_queue.ingestion.name }
