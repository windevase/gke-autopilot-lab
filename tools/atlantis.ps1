# Atlantis on/off (terraform PR automation for windevase/infra-terraform and windevase/gcp platform/)
# Usage: .\tools\atlantis.ps1 on | off | status | replan
# - Atlantis is kept at 0 replicas; turn it on only while working on infra PRs.
#   The 20:00 off-hours job turns it off automatically (forgetting it on is the common case).
# - Argo CD ignores replicas for this app, so manual scale is kept.
# - Webhooks come through the shared tunnel lab-grafana (monitoring/cloudflared).
#   'on' also starts that connector if it is off (off-hours); the 20:00 off-hours job stops it again.
# - OpenBao (ns openbao, temporary cloud credentials) runs only when needed: 'on'/'off' start/stop it together with
#   Atlantis (local AWS admin profile 'test' needs it), and the 20:00 off-hours job stops it. Argo CD ignores its replicas.
# - Webhooks sent while Atlantis is off are lost: 'on' (and 'replan') comments "atlantis plan" on open PRs
#   whose current head commit has no atlantis/plan status yet. Needs gh logged in.
param([Parameter(Mandatory = $true)][ValidateSet('on', 'off', 'status', 'replan')][string]$Action)

$ns = 'atlantis'
$repos = @('windevase/infra-terraform', 'windevase/gcp')

function Request-Replan {
  foreach ($repo in $repos) {
    $prs = gh pr list -R $repo --state open --json number,headRefOid,files | ConvertFrom-Json
    foreach ($pr in $prs) {
      $tf = @($pr.files | Where-Object { $_.path -match '\.tf$|\.terraform\.lock\.hcl$' })
      if ($tf.Count -eq 0) { continue }   # no terraform change -> nothing for Atlantis
      $ctx = gh api "repos/$repo/commits/$($pr.headRefOid)/status" --jq '[.statuses[].context | select(startswith(\"atlantis/plan\"))] | length'
      if ([int]$ctx -gt 0) { Write-Host "$repo #$($pr.number): plan status exists - skip"; continue }
      gh pr comment $pr.number -R $repo --body 'atlantis plan' | Out-Null
      Write-Host "$repo #$($pr.number): requested 'atlantis plan' (webhook missed while off)"
    }
  }
}

switch ($Action) {
  'on' {
    $cf = kubectl get deployment cloudflared -n monitoring -o jsonpath='{.spec.replicas}'
    if ($cf -eq '0') { kubectl scale deployment cloudflared -n monitoring --replicas=1 }
    kubectl scale statefulset atlantis -n $ns --replicas=1
    kubectl scale statefulset openbao -n openbao --replicas=1
    kubectl rollout status statefulset atlantis -n $ns --timeout=300s
    # OpenBao: static seal unseals automatically; wait until the pod is Ready (unsealed, plugins downloaded)
    kubectl wait pod/openbao-0 -n openbao --for=condition=Ready --timeout=180s
    kubectl rollout status deployment cloudflared -n monitoring --timeout=120s
    Start-Sleep -Seconds 10   # let the tunnel register before GitHub sends the comment webhooks
    Request-Replan
  }
  'replan' { Request-Replan }
  'off' {
    # Do not stop while a plan/apply is running (locks stay in the PVC, but an interrupted apply is unsafe).
    # The shared cloudflared is left as is (Grafana/Argo CD use it; the off-hours job manages it).
    kubectl scale statefulset atlantis -n $ns --replicas=0
    kubectl scale statefulset openbao -n openbao --replicas=0
  }
}
kubectl get statefulset,pods -n $ns
kubectl get statefulset openbao -n openbao
kubectl get deployment cloudflared -n monitoring
