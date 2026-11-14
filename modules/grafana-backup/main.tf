# Grafana 상태(사용자·승인 목록·서비스계정 토큰·홈 대시보드 설정 = PV의 grafana.db) 주간 백업 저장소
# - 대시보드 자체는 Git(ConfigMap)이 원본이라 백업 대상 아님. DB만 PV에 있어 PV 손실 시 복구 불가 → 백업
# - 클러스터 CronJob(k8s/monitoring/backup)이 WIF로 업로드. 권한은 objectCreator(생성만, 덮어쓰기·삭제 불가)
# - 비용: Standard us-central1 → Always Free 5GB-월 내 (백업 1개 수 MB, 60일 보관)

resource "google_storage_bucket" "backup" {
  name                        = "${var.project_id}-grafana-backup"
  location                    = "US-CENTRAL1"
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false

  lifecycle_rule {
    condition {
      age = 60 # 주 1회 × 약 8개 보관
    }
    action {
      type = "Delete"
    }
  }
}

data "google_project" "this" {}

locals {
  principal = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/subject/ns/${var.namespace}/sa/${var.ksa}"
}

resource "google_storage_bucket_iam_member" "writer" {
  bucket = google_storage_bucket.backup.name
  role   = "roles/storage.objectCreator"
  member = local.principal
}
