variable "project_id" {
  type = string
}

variable "namespace" {
  type    = string
  default = "monitoring"
}

variable "ksa" {
  description = "백업 CronJob의 Kubernetes ServiceAccount"
  type        = string
  default     = "grafana-backup"
}
