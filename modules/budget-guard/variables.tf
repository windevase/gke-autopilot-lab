variable "project_id" {
  type = string
}

variable "namespace" {
  description = "budget-guard CronJob 네임스페이스"
  type        = string
  default     = "monitoring"
}

variable "ksa" {
  description = "budget-guard CronJob의 Kubernetes ServiceAccount"
  type        = string
  default     = "budget-guard"
}
