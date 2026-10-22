output "alb_dns_name" { value = module.alb.dns_name }
output "alb_zone_id" { value = module.alb.zone_id }
output "ecr_repositories" { value = module.registry.repository_urls }
output "ecs_cluster" { value = aws_ecs_cluster.this.name }
output "migrate_task_definition" { value = module.migrate.task_definition_arn }
output "migrate_subnets" { value = module.network.private_subnet_ids }
output "migrate_security_group" { value = aws_security_group.task["migrate"].id }
output "service_names" {
  value = concat(
    [module.api.service_name, module.web.service_name, module.worker.service_name, module.agent_worker.service_name],
    [for m in module.mcp : m.service_name], [for m in module.demo : m.service_name],
  )
}
output "secret_arns" { value = module.secrets.arns }
output "github_deploy_role_arn" { value = module.github_oidc.role_arn }
output "alarm_topic_arn" { value = module.monitoring.alarm_topic_arn }
