output "gke_get_credentials" {
  description = "kubectl 연결 명령"
  value       = module.gke.get_credentials
}

output "atlantis_principal" {
  description = "Atlantis Workload Identity principal (platform 계층 apply 주체)"
  value       = module.atlantis_access.principal
}

# billing_export_dataset·grafana_backup_bucket 출력은 platform/ 계층으로 이동(2026-11-14)
