# Argo CD bootstrap / upgrade (Argo CD itself is NOT managed by Argo CD)
# - namespace argocd with Pod Security "restricted"
# - Secret argocd-google-oauth: copied from Grafana's Google OAuth secret (values never printed)
# - helm upgrade --install with pinned chart version
$ErrorActionPreference = "Stop"
$ns = "argocd"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$chartVersion = "10.18.0"

kubectl create namespace $ns --dry-run=client -o yaml | kubectl apply -f -
kubectl label namespace $ns --overwrite `
  pod-security.kubernetes.io/enforce=restricted `
  pod-security.kubernetes.io/warn=restricted `
  pod-security.kubernetes.io/audit=restricted | Out-Null

# OAuth client (shared with Grafana). Label part-of=argocd lets argocd-cm reference it as $argocd-google-oauth:<key>
$src = kubectl get secret grafana-google-oauth -n monitoring -o json | ConvertFrom-Json
$id = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($src.data.GF_AUTH_GOOGLE_CLIENT_ID))
$sec = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($src.data.GF_AUTH_GOOGLE_CLIENT_SECRET))
kubectl create secret generic argocd-google-oauth -n $ns `
  --from-literal=clientID=$id --from-literal=clientSecret=$sec `
  --dry-run=client -o yaml | kubectl apply -f - | Out-Null
kubectl label secret argocd-google-oauth -n $ns --overwrite app.kubernetes.io/part-of=argocd | Out-Null
Remove-Variable id, sec, src
Write-Host "secret argocd-google-oauth ready"

# PS 5.1 treats native stderr (Autopilot warnings) as errors under "Stop" -> check exit code instead
$ErrorActionPreference = "Continue"
helm repo add argo https://argoproj.github.io/argo-helm --force-update 2>$null | Out-Null
helm repo update argo 2>$null | Out-Null
helm upgrade --install argocd argo/argo-cd --version $chartVersion -n $ns `
  -f "$here\values.yaml" --wait --timeout 10m 2>&1 | Where-Object { $_ -notmatch 'warnings.go' }
if ($LASTEXITCODE -ne 0) { throw "helm upgrade failed ($LASTEXITCODE)" }
