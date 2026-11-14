# kube-prometheus-stack 배포 (GKE Autopilot + Spot Pod)
$ErrorActionPreference = "Stop"
$ns = "monitoring"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

# Bootstrap only (normally Argo CD syncs app "kps" from Git)
kubectl apply -f "$here\..\platform\storageclass.yaml"
kubectl create namespace $ns --dry-run=client -o yaml | kubectl apply -f -

# Grafana 관리자 계정: 최초 1회만 랜덤 비밀번호로 생성 (값은 화면에 출력하지 않음)
$ErrorActionPreference = "Continue"
kubectl get secret grafana-admin -n $ns 2>$null | Out-Null
$exists = ($LASTEXITCODE -eq 0)
$ErrorActionPreference = "Stop"
if (-not $exists) {
  $pw = -join ((48..57) + (65..90) + (97..122) | Get-Random -Count 24 | ForEach-Object { [char]$_ })
  kubectl create secret generic grafana-admin -n $ns --from-literal=admin-user=admin --from-literal=admin-password=$pw | Out-Null
  Remove-Variable pw
  Write-Host "grafana-admin secret created"
}

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo update prometheus-community
# 차트 버전 고정: 업그레이드는 버전을 바꿔 의도적으로 수행 (호환성 깨짐 방지)
$chartVersion = "93.3.0"
helm upgrade --install kps prometheus-community/kube-prometheus-stack `
  --version $chartVersion -n $ns -f "$here\values.yaml" --wait --timeout 20m

# Operator is kept at 0 replicas normally (cost). The chart hardcodes replicas: 1,
# so helm upgrade brings it up -> let it reconcile Prometheus, then scale back to 0.
Start-Sleep -Seconds 30
kubectl rollout status statefulset/prometheus-kps-kube-prometheus-stack -n $ns --timeout=10m
kubectl scale deploy kps-kube-prometheus-stack-operator -n $ns --replicas=0
