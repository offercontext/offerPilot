$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$sourceData = if ($env:OFFERPILOT_DATA) { $env:OFFERPILOT_DATA } else { Join-Path $HOME '.offerpilot' }
$tempData = Join-Path ([IO.Path]::GetTempPath()) ('offerpilot-interview-preparation-' + [Guid]::NewGuid().ToString('N'))
$probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$probe.Start()
$port = ([Net.IPEndPoint]$probe.LocalEndpoint).Port
$probe.Stop()
$baseUrl = "http://127.0.0.1:$port"
$server = $null
$applicationId = $null
$resumeIds = @()
$previousData = $env:OFFERPILOT_DATA

function Get-TreeIds([int]$processId) {
  $processId
  Get-CimInstance Win32_Process | Where-Object ParentProcessId -eq $processId |
    ForEach-Object { Get-TreeIds ([int]$_.ProcessId) }
}

function Assert-PortOwner([int]$rootProcessId, [int]$expectedPort) {
  $listeners = @(Get-NetTCPConnection -LocalPort $expectedPort -State Listen -ErrorAction SilentlyContinue)
  if ($listeners.Count -eq 0) { return $false }
  $treeIds = @(Get-TreeIds $rootProcessId)
  if (@($listeners | Where-Object { $treeIds -notcontains [int]$_.OwningProcess }).Count -gt 0) {
    throw "The harness port is owned by a process outside the harness tree."
  }
  return $true
}

function Invoke-Json([string]$method, [string]$uri, [hashtable]$body) {
  return Invoke-RestMethod -Method $method -Uri $uri -ContentType 'application/json' -Body ($body | ConvertTo-Json -Depth 12)
}

try {
  New-Item -ItemType Directory -Force -Path $tempData | Out-Null
  $sourceConfig = Join-Path $sourceData 'config.json'
  if (Test-Path -LiteralPath $sourceConfig) {
    Copy-Item -LiteralPath $sourceConfig -Destination (Join-Path $tempData 'config.json')
  }
  $env:OFFERPILOT_DATA = $tempData
  $server = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
    "Set-Location '$repo'; `$env:OFFERPILOT_DATA = '$tempData'; uv run oc start --port $port"
  )
  $healthy = $false
  for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if ($server.HasExited) { throw "Isolated service exited before binding port $port." }
    if (Assert-PortOwner ([int]$server.Id) $port) {
      try {
        $health = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 2
        if ($health) { $healthy = $true; break }
      } catch { Start-Sleep -Milliseconds 500 }
    } else { Start-Sleep -Milliseconds 500 }
  }
  if (-not $healthy -or -not (Assert-PortOwner ([int]$server.Id) $port)) {
    throw "Isolated service did not become healthy with a verified process owner."
  }

  $resume = Invoke-Json POST "$baseUrl/api/resumes" @{
    title = 'Interview Preparation Browser Smoke Resume'
    text = 'Built reliable API services and led a migration.'
    content_json = @{ raw_text = 'Built reliable API services and led a migration.'; experience = @(@{ highlights = @('Built reliable API services') }) }
  }
  $resumeIds += [int]$resume.id
  $application = Invoke-Json POST "$baseUrl/api/applications" @{
    company_name = 'Interview Preparation Browser Smoke'
    position_name = 'Verification Engineer'
    status = 'applied'
    source = 'smoke'
  }
  $applicationId = [int]$application.id
  $event = Invoke-Json POST "$baseUrl/api/application-events" @{
    application_id = $applicationId; event_type = 'interview'; subtype = 'technical'; round = 1
    scheduled_at = '2026-07-24T10:00:00+08:00'; duration_minutes = 45
  }

  Write-Host "Isolated interview-preparation browser harness: $baseUrl"
  Write-Host "Application=$applicationId Resume=$($resumeIds -join ',') InterviewEvent=$($event.id)"
  Write-Host 'Open the base URL in the in-app browser, open the synthetic application, choose 面试准备建议, select the resume, paste JD, and confirm generation.'
  Write-Host 'Review the five evidence-backed sections and history. Confirm that no application/event/resume/Knowledge/Question/Memory/status writes occur and that network requests stay local /api plus the configured AI provider.'
  [void](Read-Host 'Press Enter after browser acceptance')
}
finally {
  if ($server) {
    @(Get-TreeIds ([int]$server.Id) | Sort-Object -Descending) | ForEach-Object {
      Stop-Process -Id ([int]$_) -Force -ErrorAction SilentlyContinue
    }
  }
  $cleanupFailure = $null
  if ($applicationId) {
    $env:INTERVIEW_PREPARATION_HARNESS_DATA = $tempData
    $env:INTERVIEW_PREPARATION_HARNESS_APPLICATION = [string]$applicationId
    $env:INTERVIEW_PREPARATION_HARNESS_RESUMES = ($resumeIds -join ',')
    Push-Location $repo
    try {
      & uv run python -c "import os; from pathlib import Path; from offerpilot.smoke import _cleanup_real_ai_browser_records; _cleanup_real_ai_browser_records(Path(os.environ['INTERVIEW_PREPARATION_HARNESS_DATA']), int(os.environ['INTERVIEW_PREPARATION_HARNESS_APPLICATION']), [int(v) for v in os.environ['INTERVIEW_PREPARATION_HARNESS_RESUMES'].split(',') if v])"
      if ($LASTEXITCODE -ne 0) { throw "Harness cleanup failed with exit code $LASTEXITCODE." }
      & uv run python -c "import os; from pathlib import Path; from offerpilot.smoke import _assert_real_ai_smoke_data_clean; _assert_real_ai_smoke_data_clean(Path(os.environ['INTERVIEW_PREPARATION_HARNESS_DATA']))"
      if ($LASTEXITCODE -ne 0) { throw "Harness residual assertion failed with exit code $LASTEXITCODE." }
    } catch { $cleanupFailure = $_ }
    finally {
      Pop-Location
      Remove-Item Env:INTERVIEW_PREPARATION_HARNESS_DATA -ErrorAction SilentlyContinue
      Remove-Item Env:INTERVIEW_PREPARATION_HARNESS_APPLICATION -ErrorAction SilentlyContinue
      Remove-Item Env:INTERVIEW_PREPARATION_HARNESS_RESUMES -ErrorAction SilentlyContinue
    }
  }
  if (Test-Path -LiteralPath $tempData) { Remove-Item -LiteralPath $tempData -Recurse -Force }
  if ($null -eq $previousData) { Remove-Item Env:OFFERPILOT_DATA -ErrorAction SilentlyContinue }
  else { $env:OFFERPILOT_DATA = $previousData }
  if ($cleanupFailure) { throw $cleanupFailure }
}
