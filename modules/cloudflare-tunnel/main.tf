# Cloudflare Tunnel(lab-grafana) + Grafana 호스트명 DNS 코드 관리
# - cloudflared CLI로 먼저 만든 리소스를 terraform import 로 가져옴 (2026-11-08)
# - 터널은 로컬 관리형(config_src=local): 라우팅 규칙은 k8s ConfigMap cloudflared-config에서 관리
# - 범위: Grafana 전용 리소스만. 존 보안 규칙(rate limit 등)은 다른 서비스와 공유하므로 여기서 관리하지 않음
# - 인증: 환경변수 CLOUDFLARE_API_TOKEN (tf.ps1이 주입) — 코드/state에 토큰 없음

resource "cloudflare_zero_trust_tunnel_cloudflared" "grafana" {
  account_id = var.account_id
  name       = var.tunnel_name
  config_src = "local"

  lifecycle {
    # 터널 시크릿은 생성 시에만 쓰이고 API로 읽을 수 없음 (자격증명은 k8s Secret cloudflared-credentials)
    # config_src: import 시 API가 값을 돌려주지 않아(null) "local"과 달라 교체로 판정됨 → 무시
    #   (실제 관리 방식은 remote_config=false 로 확인. 2026-11-13 lab-atlantis import 때 발견)
    ignore_changes = [tunnel_secret, config_src]
    # 삭제되면 외부 접속이 끊기고 자격증명도 무효 → 실수로 destroy 방지
    prevent_destroy = true
  }
}

resource "cloudflare_dns_record" "grafana" {
  zone_id = var.zone_id
  name    = var.hostname
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.grafana.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1 # proxied 레코드는 자동(1)
}

# 같은 터널로 노출하는 추가 호스트명 (예: Argo CD). 라우팅은 k8s ConfigMap cloudflared-config에서 관리
resource "cloudflare_dns_record" "extra" {
  for_each = var.additional_hostnames

  zone_id = var.zone_id
  name    = each.value
  type    = "CNAME"
  content = "${cloudflare_zero_trust_tunnel_cloudflared.grafana.id}.cfargotunnel.com"
  proxied = true
  ttl     = 1
}
