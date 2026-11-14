# platform 계층 모듈 — 2026-11-14 루트(bootstrap)에서 state 이동(terraform state mv), 리소스 변경 없음

# Cloudflare Tunnel(lab-grafana) + DNS — grafana·argocd·atlantis 호스트 (라우팅은 k8s ConfigMap cloudflared-config)
# Argo CD: 존 WAF 사용자 규칙으로 관리자 PC 공인 IP 만 / Atlantis: GitHub 웹훅 IP·POST /events 만 (tools/waf-atlantis.py)
module "cloudflare_tunnel" {
  source = "../modules/cloudflare-tunnel"

  account_id = var.cloudflare_account_id
  zone_id    = var.cloudflare_zone_id
  hostname   = var.grafana_hostname

  additional_hostnames = [var.argocd_hostname, var.atlantis_hostname]
}

# 결제 데이터 → BigQuery 내보내기 대상 + Grafana(Workload Identity) 읽기 권한
module "billing_export" {
  source = "../modules/billing-export"

  project_id = var.project_id
}

# Grafana DB(PV) 주간 백업용 GCS 버킷 + 백업 CronJob(Workload Identity) 업로드 권한
module "grafana_backup" {
  source = "../modules/grafana-backup"

  project_id = var.project_id
}
