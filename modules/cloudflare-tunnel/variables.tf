variable "account_id" {
  description = "Cloudflare 계정 ID"
  type        = string
}

variable "zone_id" {
  description = "호스트명이 속한 Cloudflare 존 ID"
  type        = string
}

variable "tunnel_name" {
  type    = string
  default = "lab-grafana"
}

variable "hostname" {
  description = "터널로 노출할 호스트명 (예: grafana.example.com)"
  type        = string
}

variable "additional_hostnames" {
  description = "같은 터널로 노출할 추가 호스트명 (예: argocd.example.com)"
  type        = set(string)
  default     = []
}
