# 값은 terraform.tfvars(Git 제외)에 둠. 형식은 terraform.tfvars.example 참고
# Atlantis 는 Secret atlantis-gcp-platform 의 terraform.tfvars 를 -var-file 로 받는다

variable "project_id" {
  description = "GCP 프로젝트 ID"
  type        = string
}

variable "region" {
  description = "기본 리전"
  type        = string
  default     = "us-central1"
}

variable "cloudflare_account_id" {
  description = "Cloudflare 계정 ID"
  type        = string
}

variable "cloudflare_zone_id" {
  description = "호스트명이 속한 Cloudflare 존 ID"
  type        = string
}

variable "grafana_hostname" {
  description = "Cloudflare Tunnel 로 노출할 Grafana 호스트명 (예: grafana.example.com)"
  type        = string
}

variable "argocd_hostname" {
  description = "Cloudflare Tunnel 로 노출할 Argo CD 호스트명 (예: argocd.example.com)"
  type        = string
}

variable "atlantis_hostname" {
  description = "Atlantis GitHub 웹훅 수신 호스트명 (/events 경로만 노출. 예: atlantis.example.com)"
  type        = string
}
