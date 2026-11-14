output "cluster_name" {
  value = google_container_cluster.lab.name
}

output "cluster_location" {
  value = google_container_cluster.lab.location
}

output "get_credentials" {
  description = "kubectl 연결 명령"
  value       = "gcloud container clusters get-credentials ${google_container_cluster.lab.name} --region ${var.region} --project ${var.project_id}"
}
