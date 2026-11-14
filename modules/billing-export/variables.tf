variable "project_id" {
  type = string
}

variable "dataset_id" {
  type    = string
  default = "billing_export"
}

variable "location" {
  description = "BigQuery 데이터셋 위치 (결제 내보내기는 US/EU 멀티리전 권장)"
  type        = string
  default     = "US"
}

variable "grafana_namespace" {
  type    = string
  default = "monitoring"
}

variable "grafana_ksa" {
  description = "Grafana 파드의 Kubernetes ServiceAccount 이름"
  type        = string
  default     = "kps-grafana"
}
