# Application secrets. Terraform creates the secret containers only; values are written out-of-band
# (infra/scripts/put-secrets.sh) so they never enter Terraform state or source control.

variable "name" { type = string }
variable "secret_names" { type = list(string) }
variable "kms_key_arn" { type = string }

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_secretsmanager_secret" "this" {
  for_each                = toset(var.secret_names)
  name                    = "${var.name}/${each.key}"
  recovery_window_in_days = 7
  kms_key_id              = var.kms_key_arn
  tags                    = var.tags
  #checkov:skip=CKV2_AWS_57:Values are set out-of-band; rotation is a documented manual procedure (DEPLOYMENT.md) until per-secret rotation Lambdas exist
}

output "arns" { value = { for k, s in aws_secretsmanager_secret.this : k => s.arn } }
