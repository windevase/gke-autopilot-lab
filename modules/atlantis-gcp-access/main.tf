# Atlantis → GCP 권한 (platform 계층 plan/apply 용)
# - 인증: GKE Workload Identity 직접 principal(KSA atlantis/atlantis). GSA·키 없음
# - 원칙: platform 계층 리소스에 필요한 만큼만, 넓은 역할은 IAM 조건으로 범위를 묶는다
#   · 프로젝트 IAM: grantable_roles 의 부여·회수만 (스스로 owner 등을 줄 수 없음)
#   · 스토리지: 백업 버킷 + state 버킷의 platform 경로만 (bootstrap state 는 읽기·쓰기 불가)
# 이 모듈 자체는 bootstrap 계층이라 Atlantis 가 자기 권한을 넓힐 수 없다

data "google_project" "this" {}

locals {
  principal = "principal://iam.googleapis.com/projects/${data.google_project.this.number}/locations/global/workloadIdentityPools/${var.project_id}.svc.id.goog/subject/ns/${var.namespace}/sa/${var.ksa}"
  roles_csv = join(", ", [for r in var.grantable_roles : "'${r}'"])
}

# 프로젝트 조회(data google_project 등)
resource "google_project_iam_member" "browser" {
  project = var.project_id
  role    = "roles/browser"
  member  = local.principal
}

# API 사용 설정(google_project_service) + user_project_override 할당량 프로젝트
resource "google_project_iam_member" "service_usage" {
  project = var.project_id
  role    = "roles/serviceusage.serviceUsageAdmin"
  member  = local.principal
}

# BigQuery 결제 내보내기 데이터셋·데이터셋 ACL
resource "google_project_iam_member" "bigquery" {
  project = var.project_id
  role    = "roles/bigquery.dataOwner"
  member  = local.principal
}

# 프로젝트 IAM — grantable_roles 만 부여·회수 가능 (조건부 역할 부여 위임)
resource "google_project_iam_member" "iam_limited" {
  project = var.project_id
  role    = "roles/resourcemanager.projectIamAdmin"
  member  = local.principal

  condition {
    title       = "atlantis-grant-only-listed-roles"
    description = "Atlantis 는 ${join(",", var.grantable_roles)} 만 부여·회수"
    expression  = "api.getAttribute('iam.googleapis.com/modifiedGrantsByRole', []).hasOnly([${local.roles_csv}])"
  }
}

# platform 이 관리하는 버킷(Grafana 백업)만 — 버킷 설정·IAM 포함
resource "google_project_iam_member" "backup_bucket" {
  project = var.project_id
  role    = "roles/storage.admin"
  member  = local.principal

  condition {
    title       = "atlantis-backup-bucket-only"
    description = "${var.backup_bucket} 버킷만"
    expression  = "resource.name.startsWith('projects/_/buckets/${var.backup_bucket}')"
  }
}

# state 버킷: 목록 조회(잠금·workspace 확인)는 버킷 단위, 객체 읽기·쓰기는 platform 경로만
resource "google_storage_bucket_iam_member" "state_list" {
  bucket = var.state_bucket
  role   = "roles/storage.legacyBucketReader"
  member = local.principal
}

resource "google_storage_bucket_iam_member" "state_platform_rw" {
  bucket = var.state_bucket
  role   = "roles/storage.objectAdmin"
  member = local.principal

  condition {
    title       = "atlantis-platform-state-only"
    description = "${var.platform_state_dir}/ 경로만"
    expression  = "resource.name.startsWith('projects/_/buckets/${var.state_bucket}/objects/${var.platform_state_dir}/')"
  }
}
