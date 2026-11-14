# Cloud Billing → BigQuery 내보내기 대상 데이터셋 + Grafana 읽기 권한
# - 내보내기 활성화 자체는 콘솔(결제 → 결제 내보내기)에서만 가능 (공개 API 없음)
# - Grafana는 Workload Identity Federation(직접 principal)으로 키 파일 없이 접근
# - 비용: BigQuery 스토리지 10GiB/월, 쿼리 1TiB/월 무료 한도 내

data "google_project" "this" {}

resource "google_project_service" "services" {
  for_each = toset([
    "bigquery.googleapis.com",
    "bigquerydatatransfer.googleapis.com", # 결제 내보내기에 필요
  ])
  service            = each.value
  disable_on_destroy = false
}

resource "google_bigquery_dataset" "billing" {
  dataset_id    = var.dataset_id
  friendly_name = "Cloud Billing export"
  description   = "Standard/Detailed usage cost export (콘솔에서 내보내기 활성화)"
  location      = var.location

  # 실습 환경: destroy 시 테이블까지 삭제 허용
  delete_contents_on_destroy = true

  depends_on = [google_project_service.services]
}

locals {
  # GKE Workload Identity Federation 직접 principal (KSA → GCP 권한, GSA 불필요)
  grafana_principal = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/subject/ns/${var.grafana_namespace}/sa/${var.grafana_ksa}"
}

# 데이터셋 단위 읽기 권한
# ※ google_bigquery_dataset_iam_member는 principal:// 형식을 파싱하지 못함
#   ("Failed to parse BigQuery Dataset IAM member type") → dataset_access의 iam_member 사용
resource "google_bigquery_dataset_access" "grafana_viewer" {
  dataset_id = google_bigquery_dataset.billing.dataset_id
  role       = "roles/bigquery.dataViewer"
  iam_member = local.grafana_principal
}

# 쿼리 실행(Job 생성) 권한 – 프로젝트 단위로만 부여 가능
resource "google_project_iam_member" "grafana_job_user" {
  project = var.project_id
  role    = "roles/bigquery.jobUser"
  member  = local.grafana_principal
}
