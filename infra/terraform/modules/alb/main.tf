# Internet-facing ALB: HTTPS only (HTTP redirects), the SPA as default target, /api/* and health routes to the API.

variable "name" { type = string }
variable "vpc_id" { type = string }
variable "subnet_ids" { type = list(string) }
variable "security_group_ids" { type = list(string) }
variable "certificate_arn" {
  description = "ACM certificate for the HTTPS listener."
  type        = string
}
variable "deletion_protection" {
  type    = bool
  default = false
}
variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_lb" "this" {
  #checkov:skip=CKV2_AWS_76:The attached Web ACL includes AWSManagedRulesKnownBadInputsRuleSet (Log4JRCE) via a dynamic block
  name                       = var.name
  load_balancer_type         = "application"
  internal                   = false
  subnets                    = var.subnet_ids
  security_groups            = var.security_group_ids
  drop_invalid_header_fields = true
  enable_deletion_protection = var.deletion_protection
  idle_timeout               = 300 # SSE streams for answers and agent events
  tags                       = var.tags
  #checkov:skip=CKV_AWS_150:Deletion protection is on in prod (variable)
  #checkov:skip=CKV_AWS_91:Access logs need a dedicated log bucket with an ELB delivery policy; planned (see DEPLOYMENT.md)
}

resource "aws_lb_target_group" "api" {
  #checkov:skip=CKV_AWS_378:TLS terminates at the ALB; ALB-to-task traffic stays inside private subnets
  name                 = "${var.name}-api"
  port                 = 8000
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 30
  health_check {
    path                = "/readyz"
    matcher             = "200"
    interval            = 15
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
  tags = var.tags
}

resource "aws_lb_target_group" "frontend" {
  #checkov:skip=CKV_AWS_378:TLS terminates at the ALB; ALB-to-task traffic stays inside private subnets
  name                 = "${var.name}-web"
  port                 = 8080
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = var.vpc_id
  deregistration_delay = 15
  health_check {
    path    = "/"
    matcher = "200"
  }
  tags = var.tags
}

resource "aws_lb_listener" "http" {
  #checkov:skip=CKV_AWS_378:Port 80 only issues a 301 redirect to HTTPS; no content is served over HTTP
  load_balancer_arn = aws_lb.this.arn
  port              = 80
  protocol          = "HTTP"
  default_action {
    type = "redirect"
    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.this.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = var.certificate_arn
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.frontend.arn
  }
}

resource "aws_lb_listener_rule" "api" {
  listener_arn = aws_lb_listener.https.arn
  priority     = 10
  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
  condition {
    path_pattern { values = ["/api/*", "/healthz", "/readyz"] }
  }
}

output "arn" { value = aws_lb.this.arn }
output "dns_name" { value = aws_lb.this.dns_name }
output "zone_id" { value = aws_lb.this.zone_id }
output "arn_suffix" { value = aws_lb.this.arn_suffix }
output "api_target_group_arn" { value = aws_lb_target_group.api.arn }
output "api_target_group_arn_suffix" { value = aws_lb_target_group.api.arn_suffix }
output "frontend_target_group_arn" { value = aws_lb_target_group.frontend.arn }
