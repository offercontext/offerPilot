$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$sourceData = if ($env:OFFERPILOT_DATA) { $env:OFFERPILOT_DATA } else { Join-Path $HOME '.offerpilot' }
$tempData = Join-Path ([IO.Path]::GetTempPath()) ('offerpilot-pilot-real-ai-' + [Guid]::NewGuid().ToString('N'))
$portProbe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$portProbe.Start()
$port = ([Net.IPEndPoint]$portProbe.LocalEndpoint).Port
$portProbe.Stop()

if (@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue).Count -gt 0) {
  throw "Selected browser harness port $port is already in use."
}

New-Item -ItemType Directory -Force -Path $tempData | Out-Null
$sourceConfig = Join-Path $sourceData 'config.json'
if (Test-Path -LiteralPath $sourceConfig) {
  Copy-Item -LiteralPath $sourceConfig -Destination (Join-Path $tempData 'config.json')
}

$previousData = $env:OFFERPILOT_DATA
$env:OFFERPILOT_DATA = $tempData
$server = $null
$applicationId = $null
$resumeIds = @()
$baseUrl = "http://127.0.0.1:$port"

function Get-TreeIds([int]$processId) {
  $processId
  Get-CimInstance Win32_Process | Where-Object ParentProcessId -eq $processId |
    ForEach-Object { Get-TreeIds ([int]$_.ProcessId) }
}

function Assert-HarnessPortOwner([int]$rootProcessId, [int]$expectedPort) {
  $listeners = @(Get-NetTCPConnection -LocalPort $expectedPort -State Listen -ErrorAction SilentlyContinue)
  if ($listeners.Count -eq 0) { return $false }
  $treeIds = @(Get-TreeIds $rootProcessId)
  $foreign = @($listeners | Where-Object { $treeIds -notcontains [int]$_.OwningProcess })
  if ($foreign.Count -gt 0) {
    throw "Harness port $expectedPort is owned by a process outside the harness tree."
  }
  return $true
}

try {
  $server = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
    "Set-Location '$repo'; `$env:OFFERPILOT_DATA = '$tempData'; uv run oc start --port $port"
  )
  $healthy = $false
  for ($attempt = 0; $attempt -lt 60; $attempt++) {
    $ownerVerified = Assert-HarnessPortOwner ([int]$server.Id) $port
    if (-not $ownerVerified) {
      if ($server.HasExited) { throw "Isolated OfferPilot exited before binding harness port $port." }
      Start-Sleep -Milliseconds 500
      continue
    }
    try {
      $health = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 2
      if ($health) { $healthy = $true; break }
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  if (-not $healthy) { throw "Isolated OfferPilot service did not become healthy." }
  Assert-HarnessPortOwner ([int]$server.Id) $port

  $resume = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/resumes" -ContentType 'application/json' -Body (@{
    title = 'Pilot Browser Smoke Resume'
    text = 'Built API services and led migration.'
    content_json = @{ raw_text = 'Built API services and led migration.'; skills = @('Python') }
  } | ConvertTo-Json -Depth 8)
  $resumeIds += [int]$resume.id
  $application = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/applications" -ContentType 'application/json' -Body (@{
    company_name = 'Pilot Browser Smoke'
    position_name = 'Verification Engineer'
    status = 'applied'
    source = 'smoke'
  } | ConvertTo-Json)
  $applicationId = [int]$application.id
  $interviewEvent = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/application-events" -ContentType 'application/json' -Body (@{
    application_id = $applicationId
    event_type = 'interview'
    subtype = 'technical'
    round = 1
    scheduled_at = '2026-07-22T10:00:00+08:00'
    duration_minutes = 45
    location = 'Pilot browser smoke'
  } | ConvertTo-Json)
  $interviewEventId = [int]$interviewEvent.id
  $interviewNote = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/applications/$applicationId/notes" -ContentType 'application/json' -Body (@{
    application_event_id = $interviewEventId
    company = 'Pilot Browser Smoke'
    position = 'Verification Engineer'
    round = 'technical'
    date = '2026-07-22'
    questions = 'I explained the rollback plan and the observable safety signal.'
    self_reflection = 'I should have stated the tradeoff before the implementation detail.'
    difficulty_points = 'I needed a moment to structure the tradeoff.'
    mood = 'focused'
  } | ConvertTo-Json)
  $interviewNoteId = [int]$interviewNote.id

  $env:PILOT_BROWSER_HARNESS_DATA = $tempData
  $env:PILOT_BROWSER_HARNESS_APPLICATION_ID = [string]$applicationId
  $env:PILOT_BROWSER_HARNESS_EVENT_ID = [string]$interviewEventId
  $env:PILOT_BROWSER_HARNESS_RESUME_IDS = ($resumeIds -join ',')
  Push-Location $repo
  try {
    $baselineJson = & uv run python -c "import json, os; from pathlib import Path; from offerpilot.smoke import _capture_real_ai_browser_domain_baseline; print(json.dumps(_capture_real_ai_browser_domain_baseline(Path(os.environ['PILOT_BROWSER_HARNESS_DATA']), int(os.environ['PILOT_BROWSER_HARNESS_APPLICATION_ID']), [int(os.environ['PILOT_BROWSER_HARNESS_EVENT_ID'])], [int(v) for v in os.environ['PILOT_BROWSER_HARNESS_RESUME_IDS'].split(',') if v])))"
    if ($LASTEXITCODE -ne 0) { throw "Isolated browser domain baseline capture failed with exit code $LASTEXITCODE." }
    $env:PILOT_BROWSER_HARNESS_BASELINE_JSON = ($baselineJson -join '')
  }
  finally {
    Pop-Location
  }

  Write-Host "Isolated browser harness is ready: $baseUrl"
  Write-Host "Synthetic Application ID: $applicationId; Resume ID: $($resumeIds -join ', '); Interview Event ID: $interviewEventId; Interview Note ID: $interviewNoteId"
  Write-Host 'Interview-preparation acceptance is a separate path: open the top-level 面试 view, locate Pilot Browser Smoke 路 Verification Engineer, and click the row action “准备面试”. Do not substitute the application-detail 材料包 action; the expected destination is the native 面试准备建议 drawer.'
  Write-Host 'In that drawer, choose the synthetic resume, paste a non-empty JD, confirm the AI disclosure, generate the real preparation proposal, then reopen the same interview and use history to view the frozen result. Assert no Material Kit, Application, Event, Resume, Knowledge, Question, Memory, reminder, or application-status write occurred.'
  [void](Read-Host 'Press Enter after the isolated interview-preparation boundary acceptance')
  Push-Location $repo
  try {
    & uv run python -c "import json, os; from pathlib import Path; from offerpilot.smoke import _assert_real_ai_browser_no_cross_domain_writes; _assert_real_ai_browser_no_cross_domain_writes(Path(os.environ['PILOT_BROWSER_HARNESS_DATA']), int(os.environ['PILOT_BROWSER_HARNESS_APPLICATION_ID']), json.loads(os.environ['PILOT_BROWSER_HARNESS_BASELINE_JSON']), [int(os.environ['PILOT_BROWSER_HARNESS_EVENT_ID'])], [int(v) for v in os.environ['PILOT_BROWSER_HARNESS_RESUME_IDS'].split(',') if v])"
    if ($LASTEXITCODE -ne 0) { throw "Isolated interview-preparation boundary assertion failed with exit code $LASTEXITCODE." }
  }
  finally {
    Pop-Location
  }
  Write-Host 'Interview-preparation boundary passed. Only now continue with the separately allowed Opportunity Fit and interview knowledge flows.'
  Write-Host 'Open the base URL in the in-app browser. For the Opportunity Fit path, navigate to the application list/board, open Pilot Browser Smoke · Verification Engineer, click 在 Pilot 中评估, and complete the Triage → Deep Review flow.'
  Write-Host 'Then open the interview event, save a review, select original fragments, optionally generate an AI note preview, edit it, and confirm saving to Knowledge. Reopen Knowledge to verify the frozen evidence; do not create practice, Memory, or other follow-up assets.'
  Write-Host 'Verify requests stay on local /api and the configured AI provider, then return here.'
  [void](Read-Host 'Press Enter after browser acceptance')
}
finally {
  if ($server) {
    $treeIds = @(Get-TreeIds ([int]$server.Id) | Sort-Object -Descending)
    foreach ($processId in $treeIds) {
      Stop-Process -Id ([int]$processId) -Force -ErrorAction SilentlyContinue
    }
  }

  $cleanupFailure = $null
  if ($applicationId -and $resumeIds.Count -gt 0) {
    $env:PILOT_BROWSER_HARNESS_DATA = $tempData
    $env:PILOT_BROWSER_HARNESS_APPLICATION_ID = [string]$applicationId
    $env:PILOT_BROWSER_HARNESS_EVENT_ID = [string]$interviewEventId
    $env:PILOT_BROWSER_HARNESS_RESUME_IDS = ($resumeIds -join ',')
    Push-Location $repo
    try {
      & uv run python -c "import os; from pathlib import Path; from offerpilot.smoke import _cleanup_real_ai_browser_records; _cleanup_real_ai_browser_records(Path(os.environ['PILOT_BROWSER_HARNESS_DATA']), int(os.environ['PILOT_BROWSER_HARNESS_APPLICATION_ID']), [int(value) for value in os.environ['PILOT_BROWSER_HARNESS_RESUME_IDS'].split(',') if value])"
      if ($LASTEXITCODE -ne 0) { throw "Isolated browser harness record cleanup failed with exit code $LASTEXITCODE." }
      & uv run python -c "import os; from pathlib import Path; from offerpilot.smoke import _assert_real_ai_smoke_data_clean; _assert_real_ai_smoke_data_clean(Path(os.environ['PILOT_BROWSER_HARNESS_DATA']))"
      if ($LASTEXITCODE -ne 0) { throw "Isolated browser harness residual assertion failed with exit code $LASTEXITCODE." }
    } catch {
      $cleanupFailure = $_
    }
    finally {
      Pop-Location
      Remove-Item Env:PILOT_BROWSER_HARNESS_DATA -ErrorAction SilentlyContinue
      Remove-Item Env:PILOT_BROWSER_HARNESS_APPLICATION_ID -ErrorAction SilentlyContinue
      Remove-Item Env:PILOT_BROWSER_HARNESS_EVENT_ID -ErrorAction SilentlyContinue
      Remove-Item Env:PILOT_BROWSER_HARNESS_RESUME_IDS -ErrorAction SilentlyContinue
      Remove-Item Env:PILOT_BROWSER_HARNESS_BASELINE_JSON -ErrorAction SilentlyContinue
    }
  }

  if (Test-Path -LiteralPath $tempData) {
    Remove-Item -LiteralPath $tempData -Recurse -Force
  }
  if ($null -eq $previousData) { Remove-Item Env:OFFERPILOT_DATA -ErrorAction SilentlyContinue }
  else { $env:OFFERPILOT_DATA = $previousData }
  if ($cleanupFailure) { throw $cleanupFailure }
}
