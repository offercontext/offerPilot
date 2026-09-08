$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$sourceData = if ($env:OFFERPILOT_DATA) { $env:OFFERPILOT_DATA } else { Join-Path $HOME '.offerpilot' }
$cdpUrl = $env:OFFER_NEGOTIATION_CDP_URL
$tempData = Join-Path ([IO.Path]::GetTempPath()) ('offerpilot-offer-negotiation-' + [Guid]::NewGuid().ToString('N'))
$httpAudit = Join-Path $tempData 'http-audit.jsonl'
$providerAudit = Join-Path $tempData 'provider-audit.jsonl'
$browserAudit = Join-Path $tempData 'browser-network.jsonl'
$browserStop = Join-Path $tempData 'browser-network.stop'
$browserReady = Join-Path $tempData 'browser-network.ready'
$server = $null
$proxy = $null
$browserAuditor = $null
$baseUrl = $null
$offerIds = @()
$dimensionIds = @()
$baselineCounts = $null
$previousData = $env:OFFERPILOT_DATA
$previousHttpAudit = $env:OFFERPILOT_HTTP_AUDIT_FILE
$previousHttpsProxy = $env:HTTPS_PROXY
$previousHttpProxy = $env:HTTP_PROXY
$previousNoProxy = $env:NO_PROXY

function Get-FreePort {
  $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
  try { $probe.Start(); return ([Net.IPEndPoint]$probe.LocalEndpoint).Port }
  finally { $probe.Stop() }
}

function Get-ProcessTree([int]$processId) {
  $processId
  Get-CimInstance Win32_Process | Where-Object ParentProcessId -eq $processId |
    ForEach-Object { Get-ProcessTree ([int]$_.ProcessId) }
}

function Stop-Tree([object]$process) {
  if ($null -eq $process) { return }
  try {
    $ids = @(Get-ProcessTree ([int]$process.Id) | Sort-Object -Unique)
    foreach ($id in $ids) { Stop-Process -Id ([int]$id) -Force -ErrorAction SilentlyContinue }
  } catch { }
}

function Assert-ExitCode([string]$label) {
  if ($LASTEXITCODE -ne 0) { throw "$label failed." }
}

function Get-ProviderEndpoints([string]$configPath) {
  if (-not (Test-Path -LiteralPath $configPath)) { throw 'Provider config is missing.' }
  $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
  $providersById = @{}
  foreach ($provider in @($config.providers)) { if ($provider.id) { $providersById[[string]$provider.id] = $provider } }
  if ($providersById.Count -eq 0 -and $config.base_url) {
    $legacyId = if ($config.active_provider_id) { [string]$config.active_provider_id } else { 'default' }
    $providersById[$legacyId] = [pscustomobject]@{ id = $legacyId; enabled = $true; base_url = [string]$config.base_url }
  }
  $candidateIds = @()
  if ($config.active_provider_id) { $candidateIds += [string]$config.active_provider_id }
  if ($config.fallback_provider_ids) { $candidateIds += @($config.fallback_provider_ids | ForEach-Object { [string]$_ }) }
  if ($config.fallback_provider_id) { $candidateIds += [string]$config.fallback_provider_id }
  if ($candidateIds.Count -eq 0) { $candidateIds = @($providersById.Keys) }
  $seen = @{}
  foreach ($providerId in $candidateIds) {
    if (-not $providersById.ContainsKey($providerId)) { continue }
    $provider = $providersById[$providerId]
    if ($provider.enabled -and $provider.base_url) {
      $uri = [Uri]$provider.base_url
      $port = if ($uri.IsDefaultPort) { if ($uri.Scheme -eq 'https') { 443 } else { 80 } } else { $uri.Port }
      $tuple = "$($uri.Scheme)://$($uri.Host):$port"
      if (-not $seen.ContainsKey($tuple)) {
        $seen[$tuple] = $true
        [pscustomobject]@{ Scheme = $uri.Scheme; Host = $uri.Host; Port = $port; Tuple = $tuple }
      }
    }
  }
  if ($seen.Count -eq 0) { throw 'No enabled Provider endpoint is configured.' }
}

function Get-DomainCounts {
  $env:OFFER_NEGOTIATION_HARNESS_DB = Join-Path $tempData 'data.db'
  $code = @'
import hashlib, json, os, sqlite3
db = sqlite3.connect(os.environ["OFFER_NEGOTIATION_HARNESS_DB"])
names = ["applications", "application_events", "resumes", "resume_matches", "jd_analyses", "conversations", "chat_messages", "application_material_kits", "application_evidence_bundles", "material_revision_proposals", "opportunity_fit_reviews", "opportunity_fit_review_sessions", "opportunity_fit_review_stages", "interview_notes", "interview_review_proposals", "interview_preparation_proposals", "interview_knowledge_capture_attempts", "knowledge_notes", "knowledge_note_versions", "knowledge_note_evidence", "knowledge_captured_source_metadata", "knowledge_sources", "knowledge_source_origins", "knowledge_extraction_snapshots", "knowledge_evidence", "knowledge_source_assets", "knowledge_jobs", "knowledge_logs", "knowledge_source_briefs", "knowledge_brief_attempts", "knowledge_brief_attempt_steps", "knowledge_retrieval_traces", "questions", "question_reviews", "mock_interview_attempts", "mock_interview_turns", "mock_interview_feedback_proposals", "mock_interview_review_drafts", "wakeups"]
tables = {row[0] for row in db.execute("select name from sqlite_master where type = 'table'")}
snapshot = {}
for name in names:
    rows = db.execute(f"select * from {name} order by rowid").fetchall() if name in tables else []
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str)
    snapshot[name] = {"count": len(rows), "sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest()}
print(json.dumps(snapshot, separators=(",", ":")))
'@
  $json = & uv run python -c $code
  Assert-ExitCode 'domain baseline capture'
  return (($json -join '').Trim() | ConvertFrom-Json)
}

function Assert-CountsUnchanged($before, $after) {
  foreach ($property in $before.PSObject.Properties) {
    $beforeTable = $before.$($property.Name)
    $afterTable = $after.$($property.Name)
    if ([int]$beforeTable.count -ne [int]$afterTable.count -or [string]$beforeTable.sha256 -ne [string]$afterTable.sha256) {
      throw "Unexpected cross-domain write in $($property.Name)."
    }
  }
}

function Read-BrowserRecords {
  if (-not (Test-Path -LiteralPath $browserAudit)) { throw 'Browser audit output is missing.' }
  return @(Get-Content -LiteralPath $browserAudit | ForEach-Object { $_ | ConvertFrom-Json })
}

function Assert-BrowserSequence([object[]]$records, [int]$expectedOfferId, [int[]]$expectedProposalIds) {
  $localOrigin = [Uri]$baseUrl
  $bad = @($records | Where-Object {
    $uri = [Uri]$_.url
    $uri.Scheme -ne $localOrigin.Scheme -or $uri.Host -ne $localOrigin.Host -or $uri.Port -ne $localOrigin.Port
  })
  if ($bad.Count -gt 0) { throw 'Browser accessed a non-local URL.' }

  $urls = @($records | ForEach-Object { [string]$_.url })
  if (-not ($urls | Where-Object { $_ -match '/api/offers/comparison([?]|$)' })) { throw 'Browser did not read the structured comparison.' }
  $offerProposalPath = "/api/offers/$expectedOfferId/negotiation/proposals"
  $proposalRequests = @($records | Where-Object { $_.method -eq 'POST' -and $_.url -eq "$baseUrl$offerProposalPath" })
  $confirmRequests = @($records | Where-Object { $_.method -eq 'POST' -and $_.url -match "/api/offer-negotiation/proposals/[0-9]+/confirm$" })
  if ($proposalRequests.Count -lt 2) { throw 'Browser did not complete both UI and Pilot Proposal flows.' }
  if ($confirmRequests.Count -lt 2) { throw 'Browser did not complete both UI and Pilot confirmation flows.' }
  $historyRequests = @($records | Where-Object {
    $_.method -eq 'GET' -and
    $_.url -eq "$baseUrl/api/offers/$expectedOfferId/negotiation/proposals" -and
    [int]$_.response_status -eq 200
  })
  if ($historyRequests.Count -lt 2) { throw 'Browser did not read negotiation history in both flows.' }
  if ($expectedProposalIds.Count -lt 2 -or (@($expectedProposalIds | Sort-Object -Unique).Count -ne $expectedProposalIds.Count)) { throw 'UI and Pilot did not produce distinct proposals.' }
  foreach ($proposalId in $expectedProposalIds) {
    if (-not ($records | Where-Object { $_.kind -eq 'browser_response' -and [int]$_.response_proposal_id -eq $proposalId })) {
      throw "Browser did not receive proposal $proposalId from a generation response."
    }
    if (-not ($records | Where-Object { $_.kind -eq 'browser_response' -and [int]$_.response_confirmed_proposal_id -eq $proposalId })) {
      throw "Browser did not confirm proposal $proposalId."
    }
    if (-not ($historyRequests | Where-Object { @($_.response_proposal_ids) -contains $proposalId })) {
      throw "Browser history did not contain proposal $proposalId."
    }
  }
  $proposalKeys = @($proposalRequests | ForEach-Object { $_.request_context.idempotency_key_sha256 } | Where-Object { $_ }) | Sort-Object -Unique
  $confirmationKeys = @($confirmRequests | ForEach-Object { $_.request_context.confirmation_key_sha256 } | Where-Object { $_ }) | Sort-Object -Unique
  if ($proposalKeys.Count -lt 2 -or $confirmationKeys.Count -lt 2) { throw 'UI and Pilot flows did not use distinct idempotency contexts.' }
  if (-not ($proposalRequests | Where-Object { $_.request_context.entrypoint -eq 'ui' })) { throw 'Browser did not record a UI negotiation flow.' }
  if (-not ($proposalRequests | Where-Object { $_.request_context.entrypoint -eq 'pilot' })) { throw 'Browser did not record a Pilot negotiation flow.' }
  if (-not ($confirmRequests | Where-Object { $_.request_context.entrypoint -eq 'ui' })) { throw 'Browser did not record UI confirmation.' }
  if (-not ($confirmRequests | Where-Object { $_.request_context.entrypoint -eq 'pilot' })) { throw 'Browser did not record Pilot confirmation.' }
  if ($records | Where-Object { $_.url -match $chatWritePattern -and $_.method -ne 'GET' }) { throw "Pilot caused an unexpected $chatDomain write." }
}

$providerErrorCode = 'offer_negotiation_provider_error'
$unverifiableErrorCode = 'offer_negotiation_unverifiable'
$proposalDiagnosticMarker = 'offer_negotiation_proposal'
$diagnosticFields = @('failure_category', 'failure_categories', 'repair_attempted', 'repair_count', 'elapsed_ms', 'http_status', 'timeout', 'provider_request_id')
$chatDomain = 'Chat'
$chatWritePattern = '/api/(chat|conversations|messages)'

function Get-NegotiationErrorCode($body) {
  return [string]$body.error_code
}

function Assert-DiagnosticContract([object[]]$entries, [object[]]$records) {
  $matched = 0
  foreach ($entry in $entries) {
    $message = [string]$entry.message
    $diagnosticMarker = $proposalDiagnosticMarker.Replace('proposal', 'diagnostic')
    if (-not $message.StartsWith($diagnosticMarker + ' ')) { continue }
    $matched++
    $diagnostic = $message.Substring($diagnosticMarker.Length + 1) | ConvertFrom-Json
    foreach ($field in $diagnosticFields) {
      if (-not ($diagnostic.PSObject.Properties.Name -contains $field)) { throw "Offer diagnostic field $field is missing." }
      if ($field -ne 'http_status' -and $null -eq $diagnostic.$field) { throw "Offer diagnostic field $field is null." }
    }
    if ([string]$diagnostic.provider_request_id -and [string]$diagnostic.provider_request_id -match 'key|secret|token') { throw 'Provider diagnostic was not redacted.' }
  }
  $errorResponses = @($records | Where-Object {
    $_.kind -eq 'browser_response' -and
    $_.response_error_code -in @($providerErrorCode, $unverifiableErrorCode)
  })
  if ($errorResponses.Count -gt 0 -and $matched -eq 0) { throw 'Browser error responses had no matching diagnostic record.' }
}

function Assert-NegotiationErrorSemantics([object[]]$records) {
  $badGatewayStatus = 500 + 2
  $unclassified502 = @($records | Where-Object {
    $_.kind -eq 'browser_response' -and [int]$_.response_status -eq $badGatewayStatus -and
    $_.response_error_code -notin @($providerErrorCode, $unverifiableErrorCode)
  })
  if ($unclassified502.Count -gt 0) { throw 'Browser observed a 502 without a stable negotiation error code.' }
  foreach ($record in @($records | Where-Object { $_.response_error_code -eq $providerErrorCode -or $_.response_error_code -eq $unverifiableErrorCode })) {
    if ([int]$record.response_status -ne 502) { throw 'Offer negotiation error code was returned with a non-502 status.' }
    $keyHash = [string]$record.request_context.idempotency_key_sha256
    if (-not $keyHash) { throw 'Offer negotiation error was not tied to an idempotency key hash.' }
    $replays = @($records | Where-Object {
      $_.kind -eq 'browser_response' -and
      $_.request_context.idempotency_key_sha256 -eq $keyHash -and
      $_.url -eq $record.url
    })
    if ($record.response_error_code -eq $providerErrorCode) {
      $pendingReplay = $replays | Where-Object { $_.response_attempt_status -eq 'provider_unknown' } | Select-Object -First 1
      if (-not $pendingReplay) {
        throw 'Provider-unknown result did not preserve a provider_unknown retry state.'
      }
      $requests = @($records | Where-Object {
        $_.kind -eq 'browser_request' -and
        $_.method -eq 'POST' -and
        $_.url -eq $record.url -and
        $_.request_context.idempotency_key_sha256 -eq $keyHash
      })
      if ($requests.Count -lt 2) { throw 'Provider-unknown result was not retried with the original key.' }
      $payloadHashes = @($requests | ForEach-Object { $_.request_context.payload_sha256 } | Where-Object { $_ }) | Sort-Object -Unique
      if ($payloadHashes.Count -ne 1) { throw 'Provider-unknown retry changed the frozen request payload.' }
    } elseif ($replays | Where-Object { $_.response_attempt_status -in @('ready', 'generating') }) {
      throw 'Unverifiable result exposed a reusable attempt state.'
    }
  }
}

try {
  if ([string]::IsNullOrWhiteSpace($cdpUrl)) { throw 'Set OFFER_NEGOTIATION_CDP_URL before running this harness.' }
  New-Item -ItemType Directory -Force -Path $tempData | Out-Null
  Copy-Item -LiteralPath (Join-Path $sourceData 'config.json') -Destination (Join-Path $tempData 'config.json')
  $providers = @(Get-ProviderEndpoints (Join-Path $tempData 'config.json'))
  if (@($providers | Where-Object { $_.Scheme -ne 'https' }).Count -gt 0) { throw 'The configured Provider must use HTTPS.' }
  $providerAllowlist = Join-Path $tempData 'provider-allowlist.json'
  $providers | ConvertTo-Json -Compress | Set-Content -LiteralPath $providerAllowlist -Encoding utf8

  $port = Get-FreePort
  $proxyPort = Get-FreePort
  $baseUrl = "http://127.0.0.1:$port"
  $env:OFFERPILOT_DATA = $tempData
  $env:OFFERPILOT_HTTP_AUDIT_FILE = $httpAudit
  $env:HTTPS_PROXY = "http://127.0.0.1:$proxyPort"
  $env:HTTP_PROXY = "http://127.0.0.1:$proxyPort"
  $env:NO_PROXY = '127.0.0.1,localhost'

  $proxy = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
    "Set-Location '$repo'; uv run python scripts/provider-egress-proxy.py --port $proxyPort --audit '$providerAudit' --expected-endpoints-file '$providerAllowlist'"
  )
  $server = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
    "Set-Location '$repo'; uv run oc start --port $port"
  )

  $healthy = $false
  for ($i = 0; $i -lt 60; $i++) {
    if ($server.HasExited) { throw 'Isolated service exited before readiness.' }
    try {
      if (Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 2) { $healthy = $true; break }
    } catch { Start-Sleep -Milliseconds 500 }
  }
  if (-not $healthy) { throw 'Isolated service did not become healthy.' }

  $browserAuditor = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @(
    '-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command',
    "Set-Location '$repo'; uv run python scripts/browser-network-audit.py --debugging-url '$cdpUrl' --expected-url '$baseUrl' --audit '$browserAudit' --stop-file '$browserStop' --ready-file '$browserReady'"
  )
  for ($i = 0; $i -lt 120; $i++) {
    if ($browserAuditor.HasExited) { throw 'Browser auditor exited before readiness.' }
    if (Test-Path -LiteralPath $browserReady) { break }
    Start-Sleep -Milliseconds 500
  }
  if (-not (Test-Path -LiteralPath $browserReady)) { throw 'Browser auditor did not become ready.' }

  $offer = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/offers" -ContentType 'application/json' -Body '{"company_name":"\u661f\u4e91\u6570\u636e","position_name":"\u540e\u7aef\u5de5\u7a0b\u5e08","base_monthly":28000,"months_per_year":12,"signing_bonus":0,"notes":"\u7b71\u54f2\u9a8c\u6536"}'
  $second = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/offers" -ContentType 'application/json' -Body '{"company_name":"\u8fdc\u5c71\u79d1\u6280","position_name":"\u5e73\u53f0\u5de5\u7a0b\u5e08","base_monthly":30000,"months_per_year":12,"signing_bonus":0,"notes":"\u7b71\u54f2\u9a8c\u6536"}'
  $offerIds = @([int]$offer.id, [int]$second.id)
  foreach ($labelBody in @('{"label":"\u901a\u52e4"}', '{"label":"\u6210\u957f\u7a7a\u95f4"}')) {
    $dimension = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/offers/comparison-dimensions" -ContentType 'application/json' -Body $labelBody
    $dimensionIds += [int]$dimension.id
  }
  Invoke-RestMethod -Method Put -Uri "$baseUrl/api/offers/$($offerIds[0])/comparison-values/$($dimensionIds[0])" -ContentType 'application/json' -Body '{"value_text":"\u5730\u94c1 35 \u5206\u949f"}' | Out-Null
  Invoke-RestMethod -Method Put -Uri "$baseUrl/api/offers/$($offerIds[1])/comparison-values/$($dimensionIds[0])" -ContentType 'application/json' -Body '{"value_text":"\u516c\u4ea4 50 \u5206\u949f"}' | Out-Null
  $baselineCounts = Get-DomainCounts

  Write-Host 'Browser target is ready. Compare both Offers and select one Offer.'
  Write-Host 'Enter Chinese goal, concerns, and scenario, confirm AI, edit and confirm the Brief, then reopen history.'
  Write-Host 'Open Pilot, explicitly select the same Offer, and open the same negotiation flow. Do not open a second tab.'
  [void](Read-Host 'Press Enter after the browser acceptance flow is complete')

  Assert-CountsUnchanged $baselineCounts (Get-DomainCounts)
  $historyRows = @(Invoke-RestMethod -Uri "$baseUrl/api/offers/$($offerIds[0])/negotiation/proposals")
  $confirmed = @($historyRows | Where-Object { $null -ne $_.brief })
  if ($confirmed.Count -lt 2) { throw 'Both UI and Pilot negotiation Briefs are required.' }
  $confirmedProposalIds = @($confirmed | ForEach-Object { [int]$_.id })
  $records = Read-BrowserRecords
  Assert-BrowserSequence $records $offerIds[0] $confirmedProposalIds
  Assert-NegotiationErrorSemantics $records
  $currentOffer = Invoke-RestMethod -Uri "$baseUrl/api/offers/$($offerIds[0])"
  $editBody = @{
    company_name = $currentOffer.company_name
    position_name = $currentOffer.position_name
    base_monthly = $currentOffer.base_monthly
    months_per_year = $currentOffer.months_per_year
    signing_bonus = $currentOffer.signing_bonus
    equity = $currentOffer.equity
    perks = $currentOffer.perks
    deadline = $currentOffer.deadline
    notes = 'source changed after confirmation'
    assessment = $currentOffer.assessment
    status = $currentOffer.status
  } | ConvertTo-Json
  Invoke-RestMethod -Method Put -Uri "$baseUrl/api/offers/$($offerIds[0])" -ContentType 'application/json' -Body $editBody | Out-Null
  $changedHistory = Invoke-RestMethod -Uri "$baseUrl/api/offer-negotiation/proposals/$($confirmedProposalIds[0])"
  if (-not $changedHistory.source_changed) { throw 'History did not expose source_changed after Offer edit.' }
  $logEntries = @(Invoke-RestMethod -Uri "$baseUrl/api/logs?limit=200").entries
  Assert-DiagnosticContract $logEntries $records
  $proxyRecords = @(Get-Content -LiteralPath $providerAudit | ForEach-Object { $_ | ConvertFrom-Json })
  $providerConnections = @($proxyRecords | Where-Object { $_.status -eq 'connected' })
  $providerResponses = @($records | Where-Object {
    $_.kind -eq 'browser_response' -and
    $_.response_error_code -eq $providerErrorCode
  })
  if ($providerResponses.Count -gt 0 -and $providerConnections.Count -eq 0) { throw 'Provider-unknown browser result had no observed Provider egress.' }
  $providerTuples = @($providers | ForEach-Object { $_.Tuple })
  foreach ($proxyRecord in $proxyRecords) {
    $proxyTuple = "$($proxyRecord.scheme)://$($proxyRecord.host):$($proxyRecord.port)"
    if ($proxyRecord.status -eq 'connected' -and $proxyTuple -notin $providerTuples) { throw 'Provider egress did not match the configured endpoint allowlist.' }
  }
  if (-not ($proxyRecords | Where-Object { $_.status -eq 'connected' -and "$($_.scheme)://$($_.host):$($_.port)" -in $providerTuples })) { throw 'Provider egress did not match the configured endpoint allowlist.' }
  if ($proxyRecords | Where-Object { $_.status -ne 'connected' -and $_.status -ne 'rejected' }) { throw 'Provider egress audit contains an unknown status.' }
  Write-Host 'Offer negotiation browser acceptance passed.'
} catch {
  Write-Host 'Offer negotiation browser acceptance failed.'
  exit 1
} finally {
  if ($browserStop) { New-Item -ItemType File -Force -Path $browserStop | Out-Null }
  Stop-Tree $browserAuditor
  Stop-Tree $server
  Stop-Tree $proxy
  if ($previousData) { $env:OFFERPILOT_DATA = $previousData } else { Remove-Item Env:OFFERPILOT_DATA -ErrorAction SilentlyContinue }
  if ($previousHttpAudit) { $env:OFFERPILOT_HTTP_AUDIT_FILE = $previousHttpAudit } else { Remove-Item Env:OFFERPILOT_HTTP_AUDIT_FILE -ErrorAction SilentlyContinue }
  if ($previousHttpsProxy) { $env:HTTPS_PROXY = $previousHttpsProxy } else { Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue }
  if ($previousHttpProxy) { $env:HTTP_PROXY = $previousHttpProxy } else { Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue }
  if ($previousNoProxy) { $env:NO_PROXY = $previousNoProxy } else { Remove-Item Env:NO_PROXY -ErrorAction SilentlyContinue }
  Remove-Item Env:OFFER_NEGOTIATION_HARNESS_DB -ErrorAction SilentlyContinue
  if (Test-Path -LiteralPath $tempData) { Remove-Item -LiteralPath $tempData -Recurse -Force -ErrorAction SilentlyContinue }
}
