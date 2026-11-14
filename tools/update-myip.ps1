# Insurance script: run only when the home public IP changes.
# Updates both places that allow-list this PC:
#   1) GKE control plane authorized network  (terraform.tfvars master_authorized_cidrs "my-pc" -> terraform apply)
#   2) Cloudflare WAF custom rule "ArgoCD admin PC only" (IPv4 /32 + IPv6 /64, zone rules are managed via API)
#
# Usage:
#   .\tools\update-myip.ps1            # show current vs configured, change nothing
#   .\tools\update-myip.ps1 -Apply     # update tfvars + terraform plan/apply (asks before apply) + WAF rule
#
# Note: kubectl stops working when the IP changes, but Terraform/Cloudflare use Google/Cloudflare APIs,
#       so this script still works from the new IP.
param([switch]$Apply)
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$tfvars = Join-Path $root "terraform.tfvars"
$wafRuleName = "ArgoCD admin PC only"
$argoHost = "argocd.example.com"

# --- current public addresses ---
$v4 = (Invoke-RestMethod -TimeoutSec 10 https://api.ipify.org).Trim()
$v6 = $null
try { $v6 = (Invoke-RestMethod -TimeoutSec 10 https://api6.ipify.org).Trim() } catch { }
$v6net = $null
if ($v6) {
  # /64 prefix (privacy extensions rotate the lower 64 bits)
  $ip = [System.Net.IPAddress]::Parse($v6).GetAddressBytes()
  for ($i = 8; $i -lt 16; $i++) { $ip[$i] = 0 }
  $v6net = ([System.Net.IPAddress]::new($ip)).ToString() + "/64"
}
Write-Host "current : IPv4 $v4/32   IPv6 $(if ($v6net) { $v6net } else { '(none)' })"

# --- configured values ---
$tf = Get-Content $tfvars -Raw
$m = [regex]::Match($tf, '\{\s*cidr\s*=\s*"([^"]+)"\s*,\s*name\s*=\s*"my-pc"\s*\}')
if (-not $m.Success) { throw "my-pc entry not found in terraform.tfvars" }
$cfgV4 = $m.Groups[1].Value
Write-Host "tfvars  : $cfgV4 (my-pc)"

$tok = (Get-Content (Join-Path $root "secret\cloudflare_api"))[8].Trim()
$zone = ([regex]::Match($tf, 'cloudflare_zone_id\s*=\s*"([^"]+)"')).Groups[1].Value
$h = @{ Authorization = "Bearer $tok"; "Content-Type" = "application/json" }
$rs = (Invoke-RestMethod -Headers $h "https://api.cloudflare.com/client/v4/zones/$zone/rulesets/phases/http_request_firewall_custom/entrypoint").result
$rule = $rs.rules | Where-Object { $_.description -eq $wafRuleName }
if (-not $rule) { throw "WAF rule '$wafRuleName' not found" }
Write-Host "WAF     : $($rule.expression)"

$needTf = ($cfgV4 -ne "$v4/32")
$ipList = (@("$v4") + @($v6net) | Where-Object { $_ }) -join " "
$newExpr = "(http.host eq `"$argoHost`" and not ip.src in {$ipList})"
$needWaf = ($rule.expression -ne $newExpr)
if (-not $needTf -and -not $needWaf) { Write-Host "OK: nothing to change"; return }
Write-Host "changes : terraform=$needTf waf=$needWaf"
if (-not $Apply) { Write-Host "dry run (use -Apply to change)"; return }

if ($needTf) {
  $tf2 = $tf.Replace($m.Value, "{ cidr = `"$v4/32`", name = `"my-pc`" }")
  [IO.File]::WriteAllText($tfvars, $tf2, (New-Object Text.UTF8Encoding($false)))
  Push-Location $root
  try {
    & .\tf.ps1 plan -out=tfplan
    if ((Read-Host "apply? (yes/no)") -eq "yes") { & .\tf.ps1 apply tfplan } else { Write-Host "skipped apply" }
  } finally { Pop-Location }
}
if ($needWaf) {
  $body = @{ description = $wafRuleName; action = "block"; enabled = $true; expression = $newExpr } | ConvertTo-Json
  $r = Invoke-RestMethod -Method Patch -Headers $h -Body $body "https://api.cloudflare.com/client/v4/zones/$zone/rulesets/$($rs.id)/rules/$($rule.id)"
  Write-Host "WAF updated: $($r.success)"
}
Remove-Variable tok
