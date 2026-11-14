output "dataset" {
  value = "${var.project_id}.${google_bigquery_dataset.billing.dataset_id}"
}

output "grafana_principal" {
  value = local.grafana_principal
}
