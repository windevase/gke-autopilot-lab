# platform 계층 — 클러스터 위에 얹힌 서비스 자원 (Atlantis 가 PR 로 plan/apply)
# - bootstrap 계층(루트: GKE·네트워크·예산·kill switch·Atlantis 권한)은 로컬 tf.ps1 로만 apply
# - state: GCS {backend.hcl 의 버킷}/terraform/platform (bootstrap 은 terraform/state)
#   버킷 이름은 backend.hcl(Git 제외)로 주입: 로컬 = ..\tf.ps1 -Dir platform init / Atlantis = Secret atlantis-gcp-platform
terraform {
  required_version = ">= 1.5.0"

  backend "gcs" {
    prefix = "terraform/platform"
  }

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 8.0"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = ">= 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
  # 로컬: 사용자 ADC / Atlantis: GKE Workload Identity(KSA atlantis/atlantis) — 키 없음
  user_project_override = true
  billing_project       = var.project_id
}

# 토큰은 환경변수 CLOUDFLARE_API_TOKEN (로컬 tf.ps1 / Atlantis 는 Secret 파일을 env 단계로 주입)
provider "cloudflare" {}
