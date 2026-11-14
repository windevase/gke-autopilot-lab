output "billing_export_dataset" {
  description = "콘솔 결제 내보내기에서 선택할 데이터셋"
  value       = module.billing_export.dataset
}

output "grafana_backup_bucket" {
  description = "Grafana DB 주간 백업 버킷 (k8s/monitoring/backup CronJob 이 업로드)"
  value       = module.grafana_backup.bucket
}
