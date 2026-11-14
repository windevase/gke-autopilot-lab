# 예산 초과 자동 차단(kill switch)용 Pub/Sub
# - 예산(modules/gke budget.tf)이 비용 갱신마다 이 토픽으로 JSON 알림 발행 (하루 여러 번)
# - 클러스터 안 CronJob(k8s/monitoring/budget-guard.yaml)이 구독에서 pull → 100% 이상이면 워크로드 0
#   (컨트롤 플레인이 이 PC IP만 허용하므로 Cloud Functions 대신 클러스터 내부에서 처리)
# - 비용: Pub/Sub 무료 등급(월 10GiB) 내

data "google_project" "this" {}

resource "google_project_service" "pubsub" {
  service            = "pubsub.googleapis.com"
  disable_on_destroy = false
}

resource "google_pubsub_topic" "budget" {
  name = "budget-alerts"

  depends_on = [google_project_service.pubsub]
}

# Cloud Billing 예산 알림 발행 주체
resource "google_pubsub_topic_iam_member" "billing_publisher" {
  topic  = google_pubsub_topic.budget.id
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:billing-budget-alert@system.gserviceaccount.com"
}

resource "google_pubsub_subscription" "guard" {
  name  = "budget-guard"
  topic = google_pubsub_topic.budget.id

  ack_deadline_seconds       = 60
  message_retention_duration = "86400s" # 1일 (CronJob이 1시간마다 소비)
  expiration_policy {
    ttl = "" # 구독 자동 삭제 안 함
  }
}

locals {
  # GKE Workload Identity Federation 직접 principal (KSA → GCP 권한, GSA 불필요)
  guard_principal = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/subject/ns/${var.namespace}/sa/${var.ksa}"
}

resource "google_pubsub_subscription_iam_member" "guard_subscriber" {
  subscription = google_pubsub_subscription.guard.id
  role         = "roles/pubsub.subscriber"
  member       = local.guard_principal
}
