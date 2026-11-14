output "tunnel_id" {
  value = cloudflare_zero_trust_tunnel_cloudflared.grafana.id
}

output "hostname" {
  value = cloudflare_dns_record.grafana.name
}
