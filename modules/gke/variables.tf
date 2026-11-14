variable "project_id" {
  description = "GCP 프로젝트 ID"
  type        = string
}

variable "region" {
  description = "Autopilot 리전. us-central1이 Spot Pod 단가가 가장 낮은 그룹"
  type        = string
  default     = "us-central1"
}

variable "cluster_name" {
  type    = string
  default = "lab"
}

variable "billing_account_id" {
  description = "결제 계정 ID (예산 알림용)"
  type        = string
}

variable "enable_budget" {
  description = "월 예산 알림 생성 여부"
  type        = bool
  default     = true
}

variable "budget_amount_krw" {
  description = "월 예산(원). 크레딧 차감 전 총 사용액 기준으로 알림"
  type        = number
  default     = 15000
}

variable "budget_pubsub_topic" {
  description = "예산 알림을 발행할 Pub/Sub 토픽 ID (projects/<p>/topics/<t>). null이면 이메일 알림만"
  type        = string
  default     = null
}

variable "master_authorized_cidrs" {
  description = "컨트롤 플레인 접근 허용 CIDR 목록 (공인 IP가 바뀌면 여기 갱신)"
  type = list(object({
    cidr = string
    name = string
  }))
}

variable "cluster_version" {
  description = "컨트롤 플레인 최소 버전 (null이면 채널 기본값). Autopilot 노드는 컨트롤 플레인을 자동으로 따라감"
  type        = string
  default     = null
}
