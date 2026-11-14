# Patch panel id=1 (monthly cost): A = last-7d actual average, B = current requests x 24h
# (ASCII only in this script: PS 5.1 reads BOM-less .ps1 as ANSI; Korean via \u escapes)
$f = "C:\gcp\k8s\monitoring\dashboards\lab-cost-capacity.json"
$j = [IO.File]::ReadAllText($f, [Text.Encoding]::UTF8) | ConvertFrom-Json

$sel = 'namespace=~"$namespace",namespace!~"kube-system|gke-.*"'
$run = 'max by (namespace,pod) (kube_pod_status_phase{phase="Running"} == 1)'
$cpu = 'sum(max by (namespace,pod,container) (kube_pod_container_resource_requests{resource="cpu",' + $sel + '}) * on(namespace,pod) group_left() ' + $run + ')'
$mem = 'sum(max by (namespace,pod,container) (kube_pod_container_resource_requests{resource="memory",' + $sel + '}) * on(namespace,pod) group_left() ' + $run + ')'
$pvc = '(sum(max by (namespace,persistentvolumeclaim) (kube_persistentvolumeclaim_resource_requests_storage_bytes{' + $sel + '})) or vector(0)) / 2^30 * $pd_rate'

$exprA = '(avg_over_time(' + $cpu + '[7d:10m]) * $cpu_rate + avg_over_time(' + $mem + '[7d:10m]) / 2^30 * $mem_rate) * 730 + ' + $pvc
$exprB = '(' + $cpu + ' * $cpu_rate + ' + $mem + ' / 2^30 * $mem_rate) * 730 + ' + $pvc

$legA = '"\uCD5C\uADFC 7\uC77C \uC2E4\uAC00\uB3D9"' | ConvertFrom-Json        # recent 7d actual
$legB = '"24\uC2DC\uAC04 \uAC00\uB3D9 \uAC00\uC815"' | ConvertFrom-Json        # assume 24h
$desc = '"A: \uCD5C\uADFC 7\uC77C \uB3D9\uC548 \uC2E4\uC81C Running \uD30C\uB4DC requests\uC758 \uD3C9\uADE0 (\uC57C\uAC04 \uC815\uC9C0 \uBC18\uC601) / B: \uD604\uC7AC requests\uB97C 24\uC2DC\uAC04 \uC720\uC9C0\uD55C\uB2E4\uACE0 \uAC00\uC815. \uB2E8\uAC00\uB294 \uB300\uC2DC\uBCF4\uB4DC \uBCC0\uC218 cpu_rate/mem_rate/pd_rate."' | ConvertFrom-Json

$p = $j.panels | Where-Object { $_.id -eq 1 }
$p.description = $desc
$p.targets = @(
  [pscustomobject]@{ refId = "A"; legendFormat = $legA; expr = $exprA },
  [pscustomobject]@{ refId = "B"; legendFormat = $legB; expr = $exprB }
)
$p.options | Add-Member -NotePropertyName textMode -NotePropertyValue "value_and_name" -Force
$p.options | Add-Member -NotePropertyName graphMode -NotePropertyValue "none" -Force

$out = $j | ConvertTo-Json -Depth 100
[IO.File]::WriteAllText($f, $out, (New-Object Text.UTF8Encoding $false))
"patched panel 1: targets=" + $p.targets.Count
