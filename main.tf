# bootstrap 계층 — 클러스터와 그 안전장치, GitOps 도구의 권한 (로컬 tf.ps1 로만 apply)
# - 클러스터가 있어야 Argo CD·Atlantis 가 돈다(닭과 달걀) → 이 계층은 GitOps 대상에서 제외
# - 서비스 자원(Cloudflare 터널·DNS, BigQuery 결제 내보내기, Grafana 백업 버킷)은 platform/ 계층 — Atlantis 가 PR 로 apply
#   (2026-11-14 state 분리: terraform state mv 로 리소스 변경 없이 이동)
terraform {
  required_version = ">= 1.5.0"

  # state는 GCS에 저장 (us-central1 Standard → Always Free, 버전 관리 + 자체 잠금)
  # 버킷 이름은 backend.hcl(Git 제외)에서 주입: tf.ps1 init 이 -backend-config=backend.hcl 자동 추가
  backend "gcs" {
    prefix = "terraform/state"
  }

  required_providers {
    google = {
      source = "hashicorp/google"
      # 항상 최신 사용: 상한 없음. 업데이트 시 `terraform init -upgrade`
      version = ">= 8.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  # 로컬에서 'gcloud auth application-default login' 자격증명(ADC) 사용

  # 예산(Budget) API를 ADC(사용자 계정)로 호출할 때 할당량 프로젝트 지정
  user_project_override = true
  billing_project       = var.project_id
}

module "gke" {
  source = "./modules/gke"

  project_id         = var.project_id
  region             = var.region
  cluster_name       = "lab"
  billing_account_id = var.billing_account_id
  cluster_version    = var.cluster_version

  master_authorized_cidrs = var.master_authorized_cidrs

  enable_budget       = true
  budget_amount_krw   = var.budget_amount_krw
  budget_pubsub_topic = module.budget_guard.topic_id
}

# 예산 100% 초과 시 자동 차단(kill switch)용 Pub/Sub + 클러스터 CronJob(WIF) 구독 권한
# 예산(module.gke)이 이 토픽을 참조하고, 안전장치가 GitOps 도구에 의존하지 않도록 bootstrap 에 둔다
module "budget_guard" {
  source = "./modules/budget-guard"

  project_id = var.project_id
}

# Atlantis(KSA atlantis/atlantis)가 platform 계층을 plan/apply 할 GCP 권한 — Workload Identity, 키 없음
# 최소 권한 + IAM 조건: 프로젝트 IAM 은 bigquery.jobUser 부여만, 스토리지는 백업 버킷·platform state 경로만
module "atlantis_access" {
  source = "./modules/atlantis-gcp-access"

  project_id         = var.project_id
  state_bucket       = var.state_bucket
  backup_bucket      = "${var.project_id}-grafana-backup"
  grantable_roles    = ["roles/bigquery.jobUser"]
  platform_state_dir = "terraform/platform"
}
