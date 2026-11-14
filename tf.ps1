# Terraform wrapper:
#  - injects the Cloudflare API token from the secret file (never printed)
#  - adds -backend-config=backend.hcl on "init" (state bucket name is kept out of Git)
#  - layers: root = bootstrap (GKE, network, budget, kill switch, Atlantis access) / platform\ = Atlantis-managed
# Usage: .\tf.ps1 init    /    .\tf.ps1 plan -out=tfplan    /    .\tf.ps1 apply tfplan
#        .\tf.ps1 -Dir platform init / plan   (platform is applied by Atlantis via PR; local plan for checks only)
$tokenFile = Join-Path $PSScriptRoot "secret\cloudflare_api"
if (-not (Test-Path $tokenFile)) { Write-Error "missing $tokenFile"; exit 1 }
# line 9 of the file holds the API token (file also contains R2 keys - do not use them)
$env:CLOUDFLARE_API_TOKEN = (Get-Content $tokenFile)[8].Trim()

$tfArgs = @($args)
$workDir = $PSScriptRoot
if ($tfArgs.Count -ge 2 -and $tfArgs[0] -eq "-Dir") {
  $workDir = Join-Path $PSScriptRoot $tfArgs[1]
  $tfArgs = @($tfArgs | Select-Object -Skip 2)
}
if ($tfArgs.Count -gt 0 -and $tfArgs[0] -eq "init") {
  $backend = Join-Path $workDir "backend.hcl"
  if (-not (Test-Path $backend)) { $backend = Join-Path $PSScriptRoot "backend.hcl" }
  if (-not (Test-Path $backend)) { Write-Error "missing $backend (copy backend.hcl.example)"; exit 1 }
  $tfArgs += "-backend-config=$backend"
}

try {
  Push-Location $workDir
  & terraform @tfArgs
  $code = $LASTEXITCODE
} finally {
  Pop-Location
  Remove-Item Env:\CLOUDFLARE_API_TOKEN -ErrorAction SilentlyContinue
}
exit $code
