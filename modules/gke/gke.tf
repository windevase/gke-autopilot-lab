# Autopilot 노드용 최소 권한 서비스 계정 (기본 Compute SA 대신)
resource "google_service_account" "gke_nodes" {
  account_id   = "${var.cluster_name}-gke-nodes"
  display_name = "GKE ${var.cluster_name} nodes"
}

resource "google_project_iam_member" "gke_nodes" {
  for_each = toset([
    "roles/container.defaultNodeServiceAccount",
    "roles/artifactregistry.reader",
  ])

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.gke_nodes.email}"
}

# Autopilot: 노드풀 없음, 파드 requests 기준 과금
# - 클러스터 관리비: GKE 무료 티어($74.40/월)로 Autopilot 1개 면제
# - 워크로드는 nodeSelector cloud.google.com/gke-spot: "true" 로 Spot Pod 사용
resource "google_container_cluster" "lab" {
  name     = var.cluster_name
  location = var.region # Autopilot은 리전 클러스터

  enable_autopilot = true

  # 컨트롤 플레인 최소 버전 (채널 내 유효 버전만 가능). 이후 패치는 REGULAR 채널이 자동 적용
  min_master_version = var.cluster_version

  network    = google_compute_network.gke.id
  subnetwork = google_compute_subnetwork.gke.id

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  release_channel {
    channel = "REGULAR"
  }

  # 컨트롤 플레인(쿠버네티스 API) 접근을 허용된 IP로만 제한
  master_authorized_networks_config {
    gcp_public_cidrs_access_enabled = false

    dynamic "cidr_blocks" {
      for_each = var.master_authorized_cidrs
      content {
        cidr_block   = cidr_blocks.value.cidr
        display_name = cidr_blocks.value.name
      }
    }
  }

  # Autopilot 기본값은 GMP(관리형 Prometheus)로 kube-state-metrics/cAdvisor/kubelet/DCGM 패키지와
  # Advanced Datapath 지표를 수집 → "Prometheus Samples Ingested"로 과금됨.
  # Helm(kube-prometheus-stack)으로 동일 지표를 직접 수집하므로 무료인 SYSTEM_COMPONENTS만 유지
  # (관리형 Prometheus 자체는 Autopilot 1.25+에서 끌 수 없음 → 켜두되 수집 패키지를 꺼서 수집 대상 0으로)
  monitoring_config {
    enable_components = ["SYSTEM_COMPONENTS"]
    managed_prometheus {
      enabled = true
    }
    advanced_datapath_observability_config {
      enable_metrics = false
      enable_relay   = false
    }
  }

  cluster_autoscaling {
    auto_provisioning_defaults {
      service_account = google_service_account.gke_nodes.email
      oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    }
  }

  deletion_protection = false

  lifecycle {
    # Autopilot 1.37은 advanced datapath enable_metrics를 강제로 true 유지 (API/gcloud로 꺼도 무시됨, 2026-11-08 확인)
    # GMP 과금 지표(billing/samples_ingested)에 datapath 지표가 없어 비용 영향 없음 → 영구 diff만 무시
    ignore_changes = [monitoring_config[0].advanced_datapath_observability_config]
  }

  depends_on = [
    google_project_service.services,
    google_project_iam_member.gke_nodes,
  ]
}
