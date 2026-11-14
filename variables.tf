# 환경별 값은 terraform.tfvars(Git 제외)에 둠. 형식은 terraform.tfvars.example 참고

variable "project_id" {
  description = "GCP 프로젝트 ID"
  type        = string
}

variable "region" {
  description = "Autopilot 리전 (us-central1: Spot Pod 단가 최저 그룹, Always Free 리전)"
  type        = string
  default     = "us-central1"
}

variable "billing_account_id" {
  description = "결제 계정 ID (예산 알림용)"
  type        = string
}

variable "budget_amount_krw" {
  description = "월 예산(원). 크레딧 차감 전 사용액 기준 알림 + 100% 초과 시 자동 차단"
  type        = number
  default     = 15000
}

variable "cluster_version" {
  description = "컨트롤 플레인 최소 버전. 확인: gcloud container get-server-config --region <region>"
  type        = string
}

variable "master_authorized_cidrs" {
  description = "컨트롤 플레인(쿠버네티스 API) 접근 허용 CIDR. 공인 IP가 바뀌면 갱신 후 apply"
  type = list(object({
    cidr = string
    name = string
  }))
}

# Cloudflare·호스트명 변수는 platform/ 계층으로 이동(2026-11-14)

variable "state_bucket" {
  description = "terraform state GCS 버킷 (backend.hcl 과 같은 값). Atlantis 에 platform state 경로만 쓰기 권한을 줄 때 사용"
  type        = string
}
