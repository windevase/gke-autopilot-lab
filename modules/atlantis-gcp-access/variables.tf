variable "project_id" {
  type = string
}

variable "namespace" {
  description = "Atlantis 네임스페이스"
  type        = string
  default     = "atlantis"
}

variable "ksa" {
  description = "Atlantis Kubernetes ServiceAccount"
  type        = string
  default     = "atlantis"
}

variable "state_bucket" {
  description = "terraform state 버킷"
  type        = string
}

variable "platform_state_dir" {
  description = "Atlantis 가 쓸 수 있는 state 경로(접두어). bootstrap state(terraform/state)는 못 건드리게"
  type        = string
}

variable "backup_bucket" {
  description = "platform 계층이 관리하는 GCS 버킷 (Grafana 백업)"
  type        = string
}

variable "grantable_roles" {
  description = "Atlantis 가 프로젝트 IAM 으로 부여·회수할 수 있는 역할 (IAM 조건으로 제한)"
  type        = list(string)
}
