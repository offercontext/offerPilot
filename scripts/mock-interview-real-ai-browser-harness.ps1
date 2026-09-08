$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$sourceData = if ($env:OFFERPILOT_DATA) { $env:OFFERPILOT_DATA } else { Join-Path $HOME '.offerpilot' }
$tempData = Join-Path ([IO.Path]::GetTempPath()) ('offerpilot-mock-interview-' + [Guid]::NewGuid().ToString('N'))
$httpAudit = Join-Path $tempData 'http-audit.jsonl'
$providerAudit = Join-Path $tempData 'provider-audit.jsonl'
$browserAudit = Join-Path $tempData 'browser-network.jsonl'
$browserStop = Join-Path $tempData 'browser-network.stop'
$browserReady = Join-Path $tempData 'browser-network.ready'
$probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$probe.Start()
$port = ([Net.IPEndPoint]$probe.LocalEndpoint).Port
$probe.Stop()
$proxyProbe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$proxyProbe.Start()
$proxyPort = ([Net.IPEndPoint]$proxyProbe.LocalEndpoint).Port
$proxyProbe.Stop()
$baseUrl = "http://127.0.0.1:$port"
$server = $null
$proxyServer = $null
$browserAuditor = $null
$applicationId = $null
$eventId = $null
$resumeIds = @()
$previousData = $env:OFFERPILOT_DATA

function Get-ProcessTree([int]$processId) {
  $processId
  Get-CimInstance Win32_Process | Where-Object ParentProcessId -eq $processId |
    ForEach-Object { Get-ProcessTree ([int]$_.ProcessId) }
}

function Assert-ExitCode([string]$label) {
  if ($LASTEXITCODE -ne 0) { throw "$label failed with exit code $LASTEXITCODE" }
}

function Test-TransientHistoryError($errorRecord) {
  $exception = $errorRecord.Exception
  while ($null -ne $exception) {
    if ($exception -is [System.Net.WebException]) {
      return $exception.Status -in @(
        [System.Net.WebExceptionStatus]::ConnectionClosed,
        [System.Net.WebExceptionStatus]::ConnectFailure,
        [System.Net.WebExceptionStatus]::NameResolutionFailure,
        [System.Net.WebExceptionStatus]::ReceiveFailure,
        [System.Net.WebExceptionStatus]::SendFailure,
        [System.Net.WebExceptionStatus]::Timeout,
        [System.Net.WebExceptionStatus]::ProxyNameResolutionFailure,
        [System.Net.WebExceptionStatus]::RequestCanceled
      )
    }
    if ($exception -is [System.Net.Http.HttpRequestException]) {
      $statusCode = $exception.StatusCode
      if ($null -eq $statusCode) { $statusCode = $exception.Data['StatusCode'] }
      if ($null -eq $statusCode -and $null -ne $exception.Response) { $statusCode = $exception.Response.StatusCode }
      return $null -eq $statusCode
    }
    $exception = $exception.InnerException
  }
  return $false
}

function Inspect-MockInterviewAttempt([int]$attemptId) {
  Push-Location $repo
  try {
    $env:MOCK_INTERVIEW_HARNESS_ATTEMPT = [string]$attemptId
    $attemptState = & uv run python -c "import os; from pathlib import Path; from offerpilot.smoke import _mock_interview_attempt_state; print(_mock_interview_attempt_state(Path(os.environ['MOCK_INTERVIEW_HARNESS_DATA']), int(os.environ['MOCK_INTERVIEW_HARNESS_APPLICATION']), int(os.environ['MOCK_INTERVIEW_HARNESS_EVENT']), int(os.environ['MOCK_INTERVIEW_HARNESS_RESUME']), int(os.environ['MOCK_INTERVIEW_HARNESS_ATTEMPT'])))"
    Assert-ExitCode 'browser Attempt lifecycle inspection'
    $diagnostic = & uv run python -c "import json, os; from pathlib import Path; from offerpilot.smoke import _latest_mock_interview_failure_diagnostic; value = _latest_mock_interview_failure_diagnostic(Path(os.environ['MOCK_INTERVIEW_HARNESS_DATA']), int(os.environ['MOCK_INTERVIEW_HARNESS_ATTEMPT'])); raise SystemExit(2) if value is None else print(json.dumps(value, ensure_ascii=True, separators=(',', ':')))"
    Assert-ExitCode 'browser Attempt failure diagnostic'
    $diagnosticJson = ($diagnostic -join '').Trim() | ConvertFrom-Json
    $kindValue = ([string]$diagnosticJson.kind).Trim()
    $categoryValue = ([string]$diagnosticJson.category).Trim() -replace '[^\x00-\x7F]', '?'
    $stateValue = (($attemptState -join '').Trim())
    if ([string]::IsNullOrWhiteSpace($kindValue) -or [string]::IsNullOrWhiteSpace($categoryValue)) { throw 'Browser Attempt failure diagnostic is incomplete.' }
    $env:MOCK_INTERVIEW_HARNESS_KIND = $kindValue
    $env:MOCK_INTERVIEW_HARNESS_CATEGORY = $categoryValue
    $env:MOCK_INTERVIEW_HARNESS_STATE = $stateValue
    & uv run python -c "import os; from offerpilot.smoke import _assert_mock_interview_attempt_restart_state; _assert_mock_interview_attempt_restart_state(os.environ['MOCK_INTERVIEW_HARNESS_KIND'], os.environ['MOCK_INTERVIEW_HARNESS_CATEGORY'], os.environ['MOCK_INTERVIEW_HARNESS_STATE'])"
    Assert-ExitCode 'browser Attempt lifecycle assertion'
    return "attempt=$attemptId;kind=$kindValue;category=$categoryValue;state=$stateValue"
  } finally { Pop-Location }
}

function Assert-PortOwner([int]$rootProcessId, [int]$expectedPort) {
  $listeners = @(Get-NetTCPConnection -LocalPort $expectedPort -State Listen -ErrorAction SilentlyContinue)
  if ($listeners.Count -eq 0) { return $false }
  $tree = @(Get-ProcessTree $rootProcessId)
  if (@($listeners | Where-Object { $tree -notcontains [int]$_.OwningProcess }).Count -gt 0) {
    throw "Port $expectedPort is owned outside the harness process tree."
  }
  return $true
}

function Get-ProviderEndpointTuple([string]$configPath) {
  if (-not (Test-Path -LiteralPath $configPath)) { throw 'The isolated real-AI config is missing.' }
  $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
  foreach ($provider in @($config.providers)) {
    if ($provider.enabled -and $provider.base_url) {
      $uri = [Uri]$provider.base_url
      $portValue = if ($uri.IsDefaultPort) { if ($uri.Scheme -eq 'https') { 443 } else { 80 } } else { $uri.Port }
      return "$($uri.Scheme)://$($uri.Host):$portValue"
    }
  }
  throw 'No enabled configured AI provider endpoint is available.'
}

try {
  if ((@(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)).Count -gt 0) {
    throw "Selected harness port $port is already in use."
  }
  New-Item -ItemType Directory -Force -Path $tempData | Out-Null
  $sourceConfig = Join-Path $sourceData 'config.json'
  if (Test-Path -LiteralPath $sourceConfig) {
    Copy-Item -LiteralPath $sourceConfig -Destination (Join-Path $tempData 'config.json')
  }
  $providerEndpoint = [Uri](Get-ProviderEndpointTuple (Join-Path $tempData 'config.json'))
  if ($providerEndpoint.Scheme -ne 'https') {
    throw 'The mock interview harness requires an HTTPS provider endpoint.'
  }
  if (-not $env:MOCK_INTERVIEW_CDP_URL) {
    throw 'Set MOCK_INTERVIEW_CDP_URL to the in-app browser CDP debugging endpoint before running this harness.'
  }
  $env:OFFERPILOT_DATA = $tempData
  $env:OFFERPILOT_HTTP_AUDIT_FILE = $httpAudit
  $proxyServer = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
    "Set-Location '$repo'; uv run python scripts/provider-egress-proxy.py --port $proxyPort --audit '$providerAudit' --expected-scheme $($providerEndpoint.Scheme) --expected-host $($providerEndpoint.Host) --expected-port $($providerEndpoint.Port)"
  )
  for ($attempt = 0; $attempt -lt 30; $attempt++) {
    if ($proxyServer.HasExited) { throw 'Provider egress proxy exited before becoming ready.' }
    if (Assert-PortOwner ([int]$proxyServer.Id) $proxyPort) { break }
    Start-Sleep -Milliseconds 500
  }
  if (-not (Assert-PortOwner ([int]$proxyServer.Id) $proxyPort)) { throw 'Provider egress proxy did not become ready.' }
  $server = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
    "Set-Location '$repo'; `$env:OFFERPILOT_DATA = '$tempData'; `$env:HTTPS_PROXY = 'http://127.0.0.1:$proxyPort'; `$env:HTTP_PROXY = 'http://127.0.0.1:$proxyPort'; `$env:NO_PROXY = '127.0.0.1,localhost'; uv run oc start --port $port"
  )
  $healthy = $false
  for ($attempt = 0; $attempt -lt 60; $attempt++) {
    if ($server.HasExited) { throw 'Mock interview harness service exited before becoming ready.' }
    if (Assert-PortOwner ([int]$server.Id) $port) {
      try {
        $health = Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 2
        if ($health) { $healthy = $true; break }
      } catch { Start-Sleep -Milliseconds 500 }
    } else { Start-Sleep -Milliseconds 500 }
  }
  if (-not $healthy) { throw "Isolated service did not become healthy on $baseUrl." }
  $browserAuditor = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
    "Set-Location '$repo'; uv run python scripts/browser-network-audit.py --debugging-url '$($env:MOCK_INTERVIEW_CDP_URL)' --expected-url '$baseUrl' --audit '$browserAudit' --stop-file '$browserStop' --ready-file '$browserReady'"
  )
  for ($attempt = 0; $attempt -lt 240; $attempt++) {
    if ($browserAuditor.HasExited) { throw 'CDP browser auditor exited before the Network ready handshake.' }
    if (Test-Path -LiteralPath $browserReady) { break }
    Start-Sleep -Milliseconds 500
  }
  if (-not (Test-Path -LiteralPath $browserReady)) { throw 'CDP browser auditor did not complete its Network ready handshake.' }

  Write-Host "The browser-level CDP auditor created and navigated the dedicated target to $baseUrl. Continue in that target; do not open a second tab."
  $resume = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/resumes" -ContentType 'application/json' -Body (@{
    title = 'Mock Interview Browser Smoke Resume'
    text = 'Built Python services and explained rollback tradeoffs.'
    content_json = @{ raw_text = 'Built Python services and explained rollback tradeoffs.' }
  } | ConvertTo-Json -Depth 8)
  $resumeIds += [int]$resume.id
  $application = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/applications" -ContentType 'application/json' -Body (@{
    company_name = 'Mock Interview Browser Smoke'
    position_name = 'Verification Engineer'
    status = 'interview'
    source = 'isolated-smoke'
  } | ConvertTo-Json)
  $applicationId = [int]$application.id
  $event = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/application-events" -ContentType 'application/json' -Body (@{
    application_id = $applicationId
    event_type = 'interview'
    subtype = 'text-mock-interview'
    round = 1
    scheduled_at = '2026-07-28T10:00:00+08:00'
    duration_minutes = 30
  } | ConvertTo-Json)
  $eventId = [int]$event.id
  $flowBase = "/api/applications/$applicationId/events/$eventId/mock-interview/attempts"
  $maxBrowserAttempts = 3

  Push-Location $repo
  try {
    $env:MOCK_INTERVIEW_HARNESS_DATA = $tempData
    $env:MOCK_INTERVIEW_HARNESS_APPLICATION = [string]$applicationId
    $env:MOCK_INTERVIEW_HARNESS_EVENT = [string]$eventId
    $env:MOCK_INTERVIEW_HARNESS_RESUME = [string]$resumeIds[0]
    $baseline = & uv run python -c "import json, os; from pathlib import Path; from offerpilot.smoke import _capture_real_ai_browser_domain_baseline; print(json.dumps(_capture_real_ai_browser_domain_baseline(Path(os.environ['MOCK_INTERVIEW_HARNESS_DATA']), int(os.environ['MOCK_INTERVIEW_HARNESS_APPLICATION']), [int(os.environ['MOCK_INTERVIEW_HARNESS_EVENT'])], [int(os.environ['MOCK_INTERVIEW_HARNESS_RESUME'])])))"
    Assert-ExitCode 'baseline capture'
    $env:MOCK_INTERVIEW_HARNESS_BASELINE = ($baseline -join '')
  } finally { Pop-Location }

  Write-Host "Use the dedicated browser target already navigated to $baseUrl. Open Interview, choose Mock Interview Browser Smoke - Verification Engineer, then start the text mock interview."
  Write-Host 'Select the synthetic resume, paste a non-empty JD, start the text session, submit an answer, finish, select feedback if present, and use the second confirmation to save the independent review draft.'
  Write-Host 'If AI output cannot be verified, click Restart this mock interview and repeat with the same resume, JD, and event. Use at most three attempts; do not retry a terminally failed Attempt.'
  Write-Host 'Close and reopen the event to view read-only history. Browser requests must stay on local static resources and /api; Provider egress is server-side only.'
  Write-Host "Configured Provider endpoint tuple: $($providerEndpoint.Scheme)://$($providerEndpoint.Host):$($providerEndpoint.Port)"
  Write-Host 'Complete the real text mock-interview flow in the dedicated target; opening another tab is not audited.'
  Write-Host 'The harness only observes the browser result; it does not submit the mock-interview API on your behalf.'
  $history = $null
  $successfulAttemptId = $null
  $browserAttemptIds = @()
  $failureDiagnostics = @()
  $attemptLifecycleDiagnostics = @()
  $knownAttemptIds = @()
  $checkedAttemptIds = @()
  $lastCreateCount = 0
  $browserFlowFailure = $false
  $maxAttemptsObservedAt = $null
  for ($attempt = 0; $attempt -lt 360; $attempt++) {
    try {
      $history = Invoke-RestMethod -Uri "$baseUrl/api/applications/$applicationId/events/$eventId/mock-interview/attempts"
    } catch {
      if (-not (Test-TransientHistoryError $_)) { throw }
      Start-Sleep -Seconds 1
      continue
    }
    foreach ($item in @($history.items)) {
      if ($null -ne $item.attempt_id -and $browserAttemptIds -notcontains [int]$item.attempt_id) {
        $browserAttemptIds += [int]$item.attempt_id
      }
    }
    $successful = @($history.items | Where-Object {
      $browserAttemptIds -contains [int]$_.attempt_id -and
      @($_.turns).Count -ge 2 -and
      $_.proposal_status -eq 'normal' -and
      $null -ne $_.review_draft -and
      $_.review_draft.status -eq 'confirmed'
    }) | Select-Object -First 1
    if ($null -ne $successful) {
      $successfulAttemptId = [int]$successful.attempt_id
      break
    }
    if (Test-Path -LiteralPath $browserAudit) {
      $liveCreates = @(
        Get-Content -LiteralPath $browserAudit -ErrorAction SilentlyContinue |
          ForEach-Object { try { $_ | ConvertFrom-Json } catch { $null } } |
          Where-Object { $_ -and $_.method -eq 'POST' -and ([Uri]$_.url).AbsolutePath -eq $flowBase }
      )
      if ($liveCreates.Count -gt $maxBrowserAttempts) {
        throw "Browser created more than $maxBrowserAttempts mock interview Attempts in one flow."
      }
      if ($liveCreates.Count -gt $lastCreateCount) {
        foreach ($priorAttemptId in @($knownAttemptIds | Where-Object { $checkedAttemptIds -notcontains $_ })) {
          $attemptLifecycleDiagnostics += @(Inspect-MockInterviewAttempt ([int]$priorAttemptId))
          $checkedAttemptIds += [int]$priorAttemptId
        }
        Push-Location $repo
        try {
          $currentAttemptIds = & uv run python -c "import os; from pathlib import Path; from offerpilot.smoke import _mock_interview_attempt_ids; print(' '.join(str(item) for item in _mock_interview_attempt_ids(Path(os.environ['MOCK_INTERVIEW_HARNESS_DATA']), int(os.environ['MOCK_INTERVIEW_HARNESS_APPLICATION']), int(os.environ['MOCK_INTERVIEW_HARNESS_EVENT']))))"
          Assert-ExitCode 'browser Attempt id inspection'
          foreach ($currentAttemptId in (($currentAttemptIds -join '').Trim() -split '\s+' | Where-Object { $_ })) {
            if ($knownAttemptIds -notcontains [int]$currentAttemptId) { $knownAttemptIds += [int]$currentAttemptId }
          }
          $lastCreateCount = $liveCreates.Count
        } finally { Pop-Location }
      }
      if ($liveCreates.Count -eq $maxBrowserAttempts) {
        if ($null -eq $maxAttemptsObservedAt) { $maxAttemptsObservedAt = Get-Date }
        elseif (((Get-Date) - $maxAttemptsObservedAt).TotalSeconds -ge 30) { break }
      }
    }
    Start-Sleep -Seconds 1
  }
  foreach ($attemptId in @($knownAttemptIds | Where-Object { $checkedAttemptIds -notcontains $_ })) {
    if ($null -ne $successfulAttemptId -and [int]$attemptId -eq $successfulAttemptId) { continue }
    $attemptLifecycleDiagnostics += @(Inspect-MockInterviewAttempt ([int]$attemptId))
    $checkedAttemptIds += [int]$attemptId
  }
  if ($null -eq $history) { throw 'Real browser flow did not return history.' }
  if ($null -eq $successfulAttemptId) {
    if ($attemptLifecycleDiagnostics.Count -eq 0) { throw 'No mock interview Attempt failure diagnostics were recorded.' }
    $failureDiagnostics = @('stage=mock_interview') + $attemptLifecycleDiagnostics
    Write-Host ('Mock interview browser attempts failed: ' + ($failureDiagnostics -join ';'))
    $browserFlowFailure = $true
  }
  if ($attemptLifecycleDiagnostics.Count -gt 0) {
    Write-Host ('Restart lifecycle audit: ' + ($attemptLifecycleDiagnostics -join '|'))
  }
  $successfulHistoryItem = @($history.items | Where-Object { [int]$_.attempt_id -eq $successfulAttemptId }) | Select-Object -First 1
  if (-not $browserFlowFailure) {
    if ($null -eq $successfulHistoryItem) { throw 'Browser history did not contain the selected successful Attempt ID.' }
    Push-Location $repo
    try {
      $env:MOCK_INTERVIEW_HARNESS_ATTEMPT = [string]$successfulAttemptId
      & uv run python -c "import os; from pathlib import Path; from offerpilot.smoke import _assert_mock_interview_attempt_context; _assert_mock_interview_attempt_context(Path(os.environ['MOCK_INTERVIEW_HARNESS_DATA']), int(os.environ['MOCK_INTERVIEW_HARNESS_ATTEMPT']), int(os.environ['MOCK_INTERVIEW_HARNESS_APPLICATION']), int(os.environ['MOCK_INTERVIEW_HARNESS_EVENT']), int(os.environ['MOCK_INTERVIEW_HARNESS_RESUME']))"
      Assert-ExitCode 'successful Attempt source context assertion'
    } finally { Pop-Location }
  }
  if (-not (Test-Path -LiteralPath $httpAudit)) { throw 'Browser request audit is missing.' }
  New-Item -ItemType File -Force -Path $browserStop | Out-Null
  if ($browserAuditor) { $browserAuditor.WaitForExit(10000) | Out-Null }
  if ($browserAuditor -and $browserAuditor.ExitCode -ne 0) { throw "CDP browser auditor exited with code $($browserAuditor.ExitCode)." }
  if (-not (Test-Path -LiteralPath $browserAudit)) { throw 'CDP browser request audit is missing.' }
  $browserRecords = @(Get-Content -LiteralPath $browserAudit | ForEach-Object { $_ | ConvertFrom-Json })
  foreach ($record in $browserRecords) {
    if (-not $record.target_id -or -not $record.session_id -or -not $record.method) {
      throw 'CDP browser request is missing target/session/method metadata.'
    }
    $uri = [Uri]$record.url
    if ($uri.Scheme -notin @('http', 'https')) { continue }
    if ($uri.Host -ne '127.0.0.1' -or $uri.Port -ne $port) {
      throw "Browser request escaped the local origin: $($record.url)"
    }
    if ($uri.AbsolutePath -ne '/' -and $uri.AbsolutePath -notlike '/api/*' -and $uri.AbsolutePath -notlike '/*.*') {
      throw "Browser request used an unapproved local path: $($record.url)"
    }
  }
  if (@($browserRecords | Where-Object { ([Uri]$_.url).AbsolutePath -like '/api/*' }).Count -eq 0) {
    throw 'CDP audit did not record a browser API request.'
  }
  $flowRecords = @($browserRecords | Where-Object {
    ([Uri]$_.url).Host -eq '127.0.0.1' -and
    ([Uri]$_.url).Port -eq $port -and
    ([Uri]$_.url).AbsolutePath -like '/api/*'
  })
  function Find-FlowRequestIndex([object[]]$records, [int]$start, [string]$method, [string]$path, [string]$pathPattern) {
    for ($index = $start; $index -lt $records.Count; $index++) {
      $uri = [Uri]$records[$index].url
      if ($records[$index].method -ne $method) { continue }
      if ($path -and $uri.AbsolutePath -ne $path) { continue }
      if ($pathPattern -and $uri.AbsolutePath -notmatch $pathPattern) { continue }
      return $index
    }
    return -1
  }
  $createIndexes = @(
    for ($index = 0; $index -lt $flowRecords.Count; $index++) {
      $uri = [Uri]$flowRecords[$index].url
      if ($flowRecords[$index].method -eq 'POST' -and $uri.AbsolutePath -eq $flowBase) { $index }
    }
  )
  if ($createIndexes.Count -lt 1 -or $createIndexes.Count -gt $maxBrowserAttempts) {
    throw "CDP audit recorded $($createIndexes.Count) Attempt creations; expected one to three user attempts."
  }
  $createRecords = @($createIndexes | ForEach-Object { $flowRecords[$_] })
  $createContexts = @($createRecords | ForEach-Object { $_.request_context })
  if ($createContexts.Count -ne $createRecords.Count -or @($createContexts | Where-Object {
    $null -eq $_.resume_id -or [string]::IsNullOrWhiteSpace([string]$_.jd_text_sha256)
  }).Count -gt 0) {
    throw 'CDP audit did not capture the redacted resume/JD context for every Attempt creation.'
  }
  $resumeContextIds = @($createContexts | ForEach-Object { [int]$_.resume_id } | Sort-Object -Unique)
  $jdContextHashes = @($createContexts | ForEach-Object { [string]$_.jd_text_sha256 } | Sort-Object -Unique)
  if ($resumeContextIds.Count -ne 1 -or $resumeContextIds[0] -ne [int]$resumeIds[0] -or $jdContextHashes.Count -ne 1) {
    throw 'Browser restart Attempts did not reuse the same Resume and JD context.'
  }
  if ($browserFlowFailure) {
    if (-not (Test-Path -LiteralPath $providerAudit)) { throw 'Provider egress audit is missing.' }
  }
  if ($browserFlowFailure) { $answerIndex = -1 }
  else {
  $answerPathPattern = "^$([regex]::Escape($flowBase))/([0-9]+)/turns$"
  $answerIndexes = @(
    for ($index = 0; $index -lt $flowRecords.Count; $index++) {
      $uri = [Uri]$flowRecords[$index].url
      if ($flowRecords[$index].method -eq 'POST' -and $uri.AbsolutePath -match $answerPathPattern) { $index }
    }
  )
  $answerAttemptIds = @(
    $answerIndexes | ForEach-Object {
      $match = [regex]::Match(([Uri]$flowRecords[$_].url).AbsolutePath, $answerPathPattern)
      if ($match.Success) { [int]$match.Groups[1].Value }
    } | Sort-Object -Unique
  )
  if ($answerAttemptIds -notcontains $successfulAttemptId) {
    throw 'CDP audit did not record an answer for the successful Attempt ID.'
  }
  $answerIndex = $answerIndexes | Where-Object {
    $match = [regex]::Match(([Uri]$flowRecords[$_].url).AbsolutePath, $answerPathPattern)
    $match.Success -and [int]$match.Groups[1].Value -eq $successfulAttemptId
  } | Select-Object -First 1
  if ($answerIndex -lt 0) { throw 'CDP audit missed browser answer submission.' }
  $answerPathMatch = [regex]::Match(([Uri]$flowRecords[$answerIndex].url).AbsolutePath, "^$([regex]::Escape($flowBase))/([0-9]+)/turns$")
  if (-not $answerPathMatch.Success) { throw 'Browser answer path did not contain a numeric attempt id.' }
  $attemptId = [string]$successfulAttemptId
  $questionIndex = Find-FlowRequestIndex $flowRecords ($answerIndex + 1) 'POST' '' "^$([regex]::Escape($flowBase))/$attemptId/turns/[0-9]+/question$"
  if ($questionIndex -lt 0) { throw 'CDP audit missed browser next-question request.' }
  $finishIndex = Find-FlowRequestIndex $flowRecords ($questionIndex + 1) 'POST' "$flowBase/$attemptId/finish" ''
  if ($finishIndex -lt 0) { throw 'CDP audit missed browser feedback request.' }
  $draftIndex = Find-FlowRequestIndex $flowRecords ($finishIndex + 1) 'POST' "$flowBase/$attemptId/review-drafts" ''
  if ($draftIndex -lt 0) { throw 'CDP audit missed browser Review Draft confirmation.' }
  $historyIndex = Find-FlowRequestIndex $flowRecords ($draftIndex + 1) 'GET' $flowBase ''
  if ($historyIndex -lt 0) { throw 'CDP audit missed browser read-only history request.' }
  }
  if (-not (Test-Path -LiteralPath $providerAudit)) { throw 'Provider egress audit is missing.' }
  $httpRecords = @(Get-Content -LiteralPath $httpAudit | ForEach-Object { $_ | ConvertFrom-Json })
  foreach ($record in $httpRecords) {
    if ($record.host -ne '127.0.0.1' -or ($record.path -ne '/' -and $record.path -notlike '/api/*' -and $record.path -notlike '/*.*')) { throw 'Browser request escaped the local origin.' }
  }
  $browserApiRecords = @($httpRecords | Where-Object {
    $_.kind -eq 'inbound' -and
    $_.sec_fetch_mode -in @('cors', 'navigate') -and
    $_.path -like '/api/*'
  })
  if ($browserApiRecords.Count -eq 0) { throw 'No browser-originated API request was recorded.' }
  $providerRecords = @(Get-Content -LiteralPath $providerAudit | ForEach-Object { $_ | ConvertFrom-Json })
  foreach ($record in $providerRecords) {
    if ($record.kind -ne 'provider_proxy_connect' -or $record.status -ne 'connected' -or "$($record.scheme)://$($record.host):$($record.port)" -ne "$($providerEndpoint.Scheme)://$($providerEndpoint.Host):$($providerEndpoint.Port)") { throw 'Provider egress did not complete at the configured endpoint tuple.' }
  }
  if ($providerRecords.Count -eq 0) { throw 'No completed Provider request was recorded.' }
  Write-Host 'Real browser history, local request audit, and configured Provider egress checks passed.'

  Push-Location $repo
  try {
    & uv run python -c "import json, os; from pathlib import Path; from offerpilot.smoke import _assert_real_ai_browser_no_cross_domain_writes; _assert_real_ai_browser_no_cross_domain_writes(Path(os.environ['MOCK_INTERVIEW_HARNESS_DATA']), int(os.environ['MOCK_INTERVIEW_HARNESS_APPLICATION']), json.loads(os.environ['MOCK_INTERVIEW_HARNESS_BASELINE']), [int(os.environ['MOCK_INTERVIEW_HARNESS_EVENT'])], [int(os.environ['MOCK_INTERVIEW_HARNESS_RESUME'])])"
    Assert-ExitCode 'cross-domain boundary assertion'
  } finally { Pop-Location }
  if ($browserFlowFailure) { throw 'Real browser flow did not complete a confirmed Attempt after three user attempts.' }
} finally {
  if ($server) {
    $tree = @(Get-ProcessTree ([int]$server.Id) | Sort-Object -Descending)
    foreach ($processId in $tree) { Stop-Process -Id ([int]$processId) -Force -ErrorAction SilentlyContinue }
  }
  if ($proxyServer) {
    $proxyTree = @(Get-ProcessTree ([int]$proxyServer.Id) | Sort-Object -Descending)
    foreach ($processId in $proxyTree) { Stop-Process -Id ([int]$processId) -Force -ErrorAction SilentlyContinue }
  }
  if ($browserAuditor) {
    $browserTree = @(Get-ProcessTree ([int]$browserAuditor.Id) | Sort-Object -Descending)
    foreach ($processId in $browserTree) { Stop-Process -Id ([int]$processId) -Force -ErrorAction SilentlyContinue }
  }
  if ($applicationId) {
    Push-Location $repo
    try {
      $env:MOCK_INTERVIEW_HARNESS_DATA = $tempData
      $env:MOCK_INTERVIEW_HARNESS_APPLICATION = [string]$applicationId
      $env:MOCK_INTERVIEW_HARNESS_RESUME = ($resumeIds -join ',')
      & uv run python -c "import os; from pathlib import Path; from offerpilot.smoke import _cleanup_real_ai_browser_records; _cleanup_real_ai_browser_records(Path(os.environ['MOCK_INTERVIEW_HARNESS_DATA']), int(os.environ['MOCK_INTERVIEW_HARNESS_APPLICATION']), [int(v) for v in os.environ['MOCK_INTERVIEW_HARNESS_RESUME'].split(',') if v])"
      Assert-ExitCode 'isolated cleanup'
      & uv run python -c "import os; from pathlib import Path; from offerpilot.smoke import _assert_real_ai_smoke_data_clean; _assert_real_ai_smoke_data_clean(Path(os.environ['MOCK_INTERVIEW_HARNESS_DATA']))"
      Assert-ExitCode 'isolated residual assertion'
    } finally { Pop-Location }
  }
  if (Test-Path -LiteralPath $tempData) { Remove-Item -LiteralPath $tempData -Recurse -Force }
  if ($null -eq $previousData) { Remove-Item Env:OFFERPILOT_DATA -ErrorAction SilentlyContinue }
  else { $env:OFFERPILOT_DATA = $previousData }
  Remove-Item Env:MOCK_INTERVIEW_HARNESS_DATA -ErrorAction SilentlyContinue
  Remove-Item Env:MOCK_INTERVIEW_HARNESS_APPLICATION -ErrorAction SilentlyContinue
  Remove-Item Env:MOCK_INTERVIEW_HARNESS_EVENT -ErrorAction SilentlyContinue
  Remove-Item Env:MOCK_INTERVIEW_HARNESS_RESUME -ErrorAction SilentlyContinue
  Remove-Item Env:MOCK_INTERVIEW_HARNESS_ATTEMPT -ErrorAction SilentlyContinue
  Remove-Item Env:MOCK_INTERVIEW_HARNESS_BASELINE -ErrorAction SilentlyContinue
  Remove-Item Env:OFFERPILOT_HTTP_AUDIT_FILE -ErrorAction SilentlyContinue
}
