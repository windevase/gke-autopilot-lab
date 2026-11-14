# Grafana API 헬퍼: 토큰은 파일에서 읽어 헤더로만 사용 (출력하지 않음)
# 사용자 port-forward(3000)와 분리된 전용 port-forward(3001)를 필요 시 자동 기동
$Global:GrafanaUrl = "http://localhost:3001"
function Test-GrafanaPort { try { (New-Object Net.Sockets.TcpClient).Connect("127.0.0.1", 3001); $true } catch { $false } }
if (-not (Test-GrafanaPort)) {
  Start-Process kubectl -ArgumentList 'port-forward','-n','monitoring','svc/kps-grafana','3001:80' -WindowStyle Hidden | Out-Null
  for ($i = 0; $i -lt 20 -and -not (Test-GrafanaPort); $i++) { Start-Sleep -Milliseconds 500 }
}
$Global:GrafanaHeaders = @{
  Authorization  = "Bearer " + (Get-Content "C:\gcp\secret\grafana_token" -Raw).Trim()
  "Content-Type" = "application/json"
}
function Invoke-Grafana([string]$Path, [string]$Method = "GET", $Body = $null) {
  $p = @{ Uri = "$GrafanaUrl$Path"; Method = $Method; Headers = $GrafanaHeaders }
  if ($Body) { $p.Body = ($Body | ConvertTo-Json -Depth 100 -Compress) }
  Invoke-RestMethod @p
}
function Invoke-PromQL([string]$Query) {
  $q = [uri]::EscapeDataString($Query)
  (Invoke-Grafana "/api/datasources/proxy/uid/prometheus/api/v1/query?query=$q").data.result
}
