# provider 설정과 버전은 루트(C:\gcp\main.tf)에서 관리
terraform {
  required_providers {
    google = {
      source = "hashicorp/google"
    }
  }
}
