param(
  [ValidateSet('all', 'jd-only', 'triage-only')]
  [string]$Stage = 'all',
  [string]$CdpUrl = $env:APPLICATION_JD_CDP_URL,
  [string]$CompletionDirectory = $env:APPLICATION_JD_COMPLETION_DIR
)
$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent $PSScriptRoot
$sourceData = if ($env:OFFERPILOT_DATA) { $env:OFFERPILOT_DATA } else { Join-Path $HOME '.offerpilot' }
$tempData = Join-Path ([IO.Path]::GetTempPath()) ('offerpilot-application-jd-' + [Guid]::NewGuid().ToString('N'))
$httpAudit = Join-Path $tempData 'http-audit.jsonl'
$providerAudit = Join-Path $tempData 'provider-audit.jsonl'
$providerRequestAudit = Join-Path $tempData 'provider-request-audit.jsonl'
$operationAudit = Join-Path $tempData 'full-verify-operation-audit.jsonl'
$browserAudit = Join-Path $tempData 'browser-network.jsonl'
$browserDiagnostic = Join-Path $tempData 'browser-diagnostic.json'
$browserStdout = Join-Path $tempData 'browser-auditor.stdout.log'
$browserStderr = Join-Path $tempData 'browser-auditor.stderr.log'
$browserStop = Join-Path $tempData 'browser-network.stop'
$browserReady = Join-Path $tempData 'browser-network.ready'
$browserFlush = Join-Path $tempData 'browser-network.flush'
$browserFlushed = Join-Path $tempData 'browser-network.flushed'
$triageReplayContextPath = Join-Path $tempData 'triage-replay-context.json'
$server = $null
$proxy = $null
$auditor = $null
$browser = $null
$baseUrl = $null
$browserProfile = $null
$browserCdpPort = $null
$applicationId = $null
$resumeId = $null
$eventId = $null
$jdVersionId = $null
$beforeCleanup = $null
$stageDiagnosticRoot = if ($env:OFFERPILOT_APPLICATION_JD_DIAGNOSTIC_DIR) {
  $env:OFFERPILOT_APPLICATION_JD_DIAGNOSTIC_DIR
} else {
  Join-Path ([IO.Path]::GetTempPath()) 'offerpilot-application-jd-stage-diagnostics'
}
$stageDiagnosticReport = Join-Path $stageDiagnosticRoot (
  'stage-all-' + (Get-Date -Format 'yyyyMMdd-HHmmssfff') + '-' + [Guid]::NewGuid().ToString('N') + '.jsonl'
)
$providerAuditOffset = 0
$operationAuditOffset = 0
$triageReplayCount = 0
$previousData = $env:OFFERPILOT_DATA
$previousHttpAudit = $env:OFFERPILOT_HTTP_AUDIT_FILE
$previousProviderRequestAudit = $env:OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE
$previousOperationAudit = $env:OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE
$previousFullVerifyOperation = $env:OFFERPILOT_FULL_VERIFY_OPERATION
$previousFullVerifyStage = $env:OFFERPILOT_FULL_VERIFY_ACTIVE_STAGE
$previousHttpsProxy = $env:HTTPS_PROXY
$previousHttpProxy = $env:HTTP_PROXY
$previousNoProxy = $env:NO_PROXY
$previousLiteLlmLocalCostMap = $env:LITELLM_LOCAL_MODEL_COST_MAP

function Get-FreePort {
  $probe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
  try { $probe.Start(); return ([Net.IPEndPoint]$probe.LocalEndpoint).Port }
  finally { $probe.Stop() }
}

function Get-BrowserExecutable {
  $candidates = @(
    $env:OFFERPILOT_BROWSER_PATH,
    (Join-Path $env:ProgramFiles 'Google\Chrome\Application\chrome.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:LOCALAPPDATA 'Google\Chrome\Application\chrome.exe'),
    (Join-Path $env:ProgramFiles 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\Edge\Application\msedge.exe'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\Edge\Application\msedge.exe')
  )
  foreach ($candidate in $candidates) {
    if ($candidate -and (Test-Path -LiteralPath $candidate)) { return (Resolve-Path -LiteralPath $candidate).Path }
  }
  throw 'No Chrome or Edge executable was found for the temporary browser.'
}

function Start-TemporaryBrowser([string]$url) {
  $script:browserCdpPort = Get-FreePort
  $script:browserProfile = Join-Path $tempData 'browser-profile'
  $executable = Get-BrowserExecutable
  $script:browser = Start-Process -FilePath $executable -WindowStyle Hidden -PassThru -ArgumentList @(
    "--remote-debugging-port=$($script:browserCdpPort)",
    "--user-data-dir=$($script:browserProfile)",
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    '--disable-background-networking',
    '--disable-component-update',
    '--disable-features=Translate,OptimizationHints',
    '--window-size=1440,1200',
    $url
  )
  $endpoint = "http://127.0.0.1:$($script:browserCdpPort)"
  for ($i = 0; $i -lt 60; $i++) {
    if ($browser.HasExited) { throw 'Temporary browser exited before CDP readiness.' }
    try {
      $version = Invoke-RestMethod -Uri "$endpoint/json/version" -TimeoutSec 2
      if ($version.webSocketDebuggerUrl) { return $endpoint }
    } catch { Start-Sleep -Milliseconds 500 }
  }
  throw 'Temporary browser CDP endpoint did not become ready.'
}

function Stop-Tree([object]$process, [string]$label = 'process') {
  if ($null -eq $process) { return }
  $ids = [System.Collections.Generic.HashSet[int]]::new()
  $pending = [System.Collections.Generic.Queue[int]]::new()
  [void]$ids.Add([int]$process.Id)
  $pending.Enqueue([int]$process.Id)
  while ($pending.Count -gt 0) {
    $parentId = $pending.Dequeue()
    foreach ($child in @(Get-CimInstance Win32_Process -Filter "ParentProcessId=$parentId" -ErrorAction SilentlyContinue)) {
      $childId = [int]$child.ProcessId
      if ($ids.Add($childId)) { $pending.Enqueue($childId) }
    }
  }
  foreach ($id in @($ids)) {
    Stop-Process -Id $id -Force -ErrorAction SilentlyContinue
  }
  $deadline = [DateTime]::UtcNow.AddSeconds(10)
  while ([DateTime]::UtcNow -lt $deadline) {
    $remaining = @($ids | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
    if ($remaining.Count -eq 0) { return }
    Start-Sleep -Milliseconds 100
  }
  $remaining = @($ids | Where-Object { Get-Process -Id $_ -ErrorAction SilentlyContinue })
  if ($remaining.Count -gt 0) { throw "$label did not exit cleanly." }
}

function Get-BrowserDiagnostic {
  if (-not (Test-Path -LiteralPath $browserDiagnostic)) { throw 'Browser diagnostic output is missing.' }
  return (Get-Content -LiteralPath $browserDiagnostic -Raw | ConvertFrom-Json)
}

function Assert-BrowserAuditorHealthy {
  if ($null -ne $browser -and $browser.HasExited) {
    throw 'Temporary browser exited before the requested browser stages completed.'
  }
  if ($null -ne $auditor -and $auditor.HasExited) {
    $diagnostic = if (Test-Path -LiteralPath $browserDiagnostic) { Get-BrowserDiagnostic } else { $null }
    $category = if ($diagnostic -and $diagnostic.failure_category) { [string]$diagnostic.failure_category } else { 'unknown' }
    throw "Browser auditor exited before the requested browser stages completed ($category)."
  }
}

function Wait-AuditorExit([int]$TimeoutSeconds = 20, [switch]$AllowFailure) {
  if ($null -eq $auditor) { return }
  $deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
  while (-not $auditor.HasExited -and [DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 200
    $auditor.Refresh()
  }
  if (-not $auditor.HasExited) {
    if ($AllowFailure) { return }
    throw 'Browser auditor did not exit cleanly.'
  }
  $auditorExitCode = $auditor.ExitCode
  if (-not $AllowFailure -and $null -ne $auditorExitCode -and [int]$auditorExitCode -ne 0) {
    $diagnostic = if (Test-Path -LiteralPath $browserDiagnostic) { Get-BrowserDiagnostic } else { $null }
    $category = if ($diagnostic -and $diagnostic.failure_category) { [string]$diagnostic.failure_category } else { 'unknown' }
    throw "Browser auditor exited with code $($auditor.ExitCode) ($category)."
  }
}

function Flush-BrowserAudit {
  if ($null -eq $auditor) { throw 'Browser auditor was not started.' }
  Remove-Item -LiteralPath $browserFlushed -Force -ErrorAction SilentlyContinue
  New-Item -ItemType File -Force -Path $browserFlush | Out-Null
  $deadline = [DateTime]::UtcNow.AddSeconds(20)
  while (-not (Test-Path -LiteralPath $browserFlushed) -and [DateTime]::UtcNow -lt $deadline) {
    Assert-BrowserAuditorHealthy
    Start-Sleep -Milliseconds 200
  }
  if (-not (Test-Path -LiteralPath $browserFlushed)) { throw 'Browser auditor did not flush pending responses.' }
}

function Complete-BrowserAudit {
  if ($null -eq $auditor) { throw 'Browser auditor was not started.' }
  Flush-BrowserAudit
  New-Item -ItemType File -Force -Path $browserStop | Out-Null
  Wait-AuditorExit
  $diagnostic = Get-BrowserDiagnostic
  if ([string]$diagnostic.status -ne 'passed' -or $diagnostic.failure_category) {
    $category = if ($diagnostic.failure_category) { [string]$diagnostic.failure_category } else { 'unknown' }
    throw "Browser audit did not complete successfully ($category)."
  }
}

function Assert-ExitCode([string]$label) {
  if ($LASTEXITCODE -ne 0) { throw "$label failed." }
}

function Get-ProviderEndpoints([string]$configPath) {
  $config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
  $byId = @{}
  foreach ($provider in @($config.providers)) {
    if ($provider.id) { $byId[[string]$provider.id] = $provider }
  }
  if ($byId.Count -eq 0 -and $config.base_url) {
    $id = if ($config.active_provider_id) { [string]$config.active_provider_id } else { 'default' }
    $byId[$id] = [pscustomobject]@{ id = $id; enabled = $true; base_url = [string]$config.base_url }
  }
  $ids = @()
  if ($config.active_provider_id) { $ids += [string]$config.active_provider_id }
  if ($config.fallback_provider_ids) { $ids += @($config.fallback_provider_ids | ForEach-Object { [string]$_ }) }
  if ($config.fallback_provider_id) { $ids += [string]$config.fallback_provider_id }
  if ($ids.Count -eq 0) { $ids = @($byId.Keys) }
  $seen = @{}
  foreach ($id in $ids) {
    if (-not $byId.ContainsKey($id)) { continue }
    $provider = $byId[$id]
    if (-not $provider.enabled -or -not $provider.base_url) { continue }
    $uri = [Uri]$provider.base_url
    $port = if ($uri.IsDefaultPort) { if ($uri.Scheme -eq 'https') { 443 } else { 80 } } else { $uri.Port }
    $tuple = "$($uri.Scheme)://$($uri.Host):$port"
    if (-not $seen.ContainsKey($tuple)) {
      $seen[$tuple] = $true
      [pscustomobject]@{ scheme = $uri.Scheme; host = $uri.Host; port = $port; tuple = $tuple }
    }
  }
}

function Get-DbSnapshot {
  $env:APPLICATION_JD_HARNESS_DB = Join-Path $tempData 'data.db'
  $code = @'
import hashlib, json, os, sqlite3
db = sqlite3.connect(os.environ["APPLICATION_JD_HARNESS_DB"])
tables = [
  "applications", "application_events", "resumes", "application_jd_versions",
  "conversations", "chat_messages", "jd_analyses", "resume_matches",
  "application_material_kits", "material_revision_proposals",
  "opportunity_fit_reviews", "opportunity_fit_review_sessions", "opportunity_fit_review_stages",
  "interview_preparation_proposals", "mock_interview_attempts",
  "mock_interview_turns", "mock_interview_feedback_proposals", "mock_interview_review_drafts",
  "interview_notes", "interview_review_proposals", "offers", "offer_comparison_dimensions",
  "offer_comparison_values", "offer_negotiation_proposals", "offer_negotiation_briefs",
  "questions", "question_reviews", "wakeups", "knowledge_sources", "knowledge_source_origins",
  "knowledge_extraction_snapshots", "knowledge_notes", "knowledge_note_versions",
  "knowledge_note_evidence", "knowledge_captured_source_metadata", "knowledge_evidence",
  "knowledge_source_assets", "knowledge_jobs", "knowledge_logs", "knowledge_source_briefs",
  "knowledge_brief_attempts", "knowledge_brief_attempt_steps", "knowledge_retrieval_traces",
  "knowledge_evidence_fts",
  "interview_knowledge_capture_attempts", "application_evidence_bundles",
]
available = {row[0] for row in db.execute("select name from sqlite_master where type='table'")}
out = {}
for name in tables:
    rows = db.execute(f"select * from {name} order by rowid").fetchall() if name in available else []
    data = json.dumps(rows, ensure_ascii=False, separators=(",", ":"), default=str)
    out[name] = {"count": len(rows), "sha256": hashlib.sha256(data.encode("utf-8")).hexdigest()}
print(json.dumps(out, separators=(",", ":")))
'@
  $json = $code | & uv run python -
  Assert-ExitCode 'database snapshot'
  return (($json -join '').Trim() | ConvertFrom-Json)
}

function Assert-StageUnchanged($before, $after, [string[]]$allowedTables) {
  $names = @($before.PSObject.Properties.Name) + @($after.PSObject.Properties.Name) |
    Sort-Object -Unique
  foreach ($name in $names) {
    if ($name -in $allowedTables) { continue }
    $oldProperty = $before.PSObject.Properties[$name]
    $newProperty = $after.PSObject.Properties[$name]
    $old = if ($null -ne $oldProperty) { $oldProperty.Value } else { $null }
    $new = if ($null -ne $newProperty) { $newProperty.Value } else { $null }
    $oldCount = if ($null -ne $old) { [int]$old.count } else { 0 }
    $newCount = if ($null -ne $new) { [int]$new.count } else { 0 }
    $oldHash = if ($null -ne $old) { [string]$old.sha256 } else { '' }
    $newHash = if ($null -ne $new) { [string]$new.sha256 } else { '' }
    if ($oldCount -ne $newCount -or $oldHash -ne $newHash) {
      throw "Unexpected write in $name."
    }
  }
}

function Clear-Consumer([string]$consumer) {
  $env:APPLICATION_JD_HARNESS_DB = Join-Path $tempData 'data.db'
  $env:APPLICATION_JD_HARNESS_APP = [string]$applicationId
  $code = @'
import os, sqlite3
db = sqlite3.connect(os.environ["APPLICATION_JD_HARNESS_DB"])
app = int(os.environ["APPLICATION_JD_HARNESS_APP"])
consumer = os.environ["APPLICATION_JD_HARNESS_CONSUMER"]
if consumer == "triage":
    db.execute("delete from opportunity_fit_review_stages where review_id in (select id from opportunity_fit_review_sessions where application_id = ?)", (app,))
    db.execute("delete from opportunity_fit_review_sessions where application_id = ?", (app,))
elif consumer == "material-kit":
    db.execute("delete from application_material_kits where application_id = ?", (app,))
elif consumer == "interview-preparation":
    db.execute("delete from interview_preparation_proposals where application_id = ?", (app,))
else:
    raise SystemExit("unknown consumer")
db.commit()
'@
  $env:APPLICATION_JD_HARNESS_CONSUMER = $consumer
  $code | & uv run python -
  Assert-ExitCode "cleanup $consumer"
}

function Clear-SyntheticData {
  $env:APPLICATION_JD_HARNESS_DB = Join-Path $tempData 'data.db'
  $env:APPLICATION_JD_HARNESS_APP = [string]$applicationId
  $env:APPLICATION_JD_HARNESS_RESUME = [string]$resumeId
  $env:APPLICATION_JD_HARNESS_EVENT = [string]$eventId
  $code = @'
import os, sqlite3
db = sqlite3.connect(os.environ["APPLICATION_JD_HARNESS_DB"])
app = int(os.environ["APPLICATION_JD_HARNESS_APP"])
resume = int(os.environ["APPLICATION_JD_HARNESS_RESUME"])
event = int(os.environ["APPLICATION_JD_HARNESS_EVENT"])
for sql in (
    "delete from opportunity_fit_review_stages where review_id in (select id from opportunity_fit_review_sessions where application_id = ?)",
    "delete from opportunity_fit_review_sessions where application_id = ?",
    "delete from application_material_kits where application_id = ?",
    "delete from material_revision_proposals where application_id = ?",
    "delete from interview_preparation_proposals where application_id = ?",
    "delete from mock_interview_review_drafts where proposal_id in (select id from mock_interview_feedback_proposals where attempt_id in (select id from mock_interview_attempts where application_id = ?))",
    "delete from mock_interview_feedback_proposals where attempt_id in (select id from mock_interview_attempts where application_id = ?)",
    "delete from mock_interview_turns where attempt_id in (select id from mock_interview_attempts where application_id = ?)",
    "delete from mock_interview_attempts where application_id = ?",
    "delete from application_jd_versions where application_id = ?",
    "delete from chat_messages where conversation_id in (select id from conversations where context_type = 'application' and context_ref = ?)",
    "delete from conversations where context_type = 'application' and context_ref = ?",
    "delete from application_events where id = ?",
    "delete from resumes where id = ?",
    "delete from applications where id = ?",
):
    value = event if "application_events" in sql else resume if "resumes" in sql else app
    db.execute(sql, (value,))
db.commit()
remaining = db.execute("select count(*) from applications where id = ?", (app,)).fetchone()[0]
if remaining:
    raise SystemExit("synthetic Application cleanup left a row")
'@
  $code | & uv run python -
  Assert-ExitCode 'synthetic cleanup'
}

function Get-BrowserRecords {
  if (-not (Test-Path -LiteralPath $browserAudit)) { throw 'Browser audit output is missing.' }
  return @(Get-Content -LiteralPath $browserAudit | ForEach-Object { $_ | ConvertFrom-Json })
}

function Wait-StageCompletion([string]$stageName) {
  if ([string]::IsNullOrWhiteSpace($CompletionDirectory)) {
    [void](Read-Host "Press Enter after $stageName is complete")
    return
  }
  New-Item -ItemType Directory -Force -Path $CompletionDirectory | Out-Null
  $marker = Join-Path $CompletionDirectory ($stageName + '.complete')
  Write-Host "WAITING_FOR_COMPLETION=$marker"
  for ($i = 0; $i -lt 3600; $i++) {
    Assert-BrowserAuditorHealthy
    if (Test-Path -LiteralPath $marker) {
      Remove-Item -LiteralPath $marker -Force -ErrorAction Stop
      return
    }
    Start-Sleep -Milliseconds 500
  }
  throw "Timed out waiting for $stageName completion marker."
}

function Save-StageDiagnostic([string]$stageName) {
  if (-not $applicationId) { throw 'Cannot write a stage diagnostic before Application setup.' }
  New-Item -ItemType Directory -Force -Path $stageDiagnosticRoot | Out-Null
  $args = @(
    '--stage', $stageName,
    '--db', (Join-Path $tempData 'data.db'),
    '--application-id', ([string]$applicationId),
    '--provider-audit', $providerRequestAudit,
    '--operation-audit', $operationAudit,
    '--provider-start-index', ([string]$script:providerAuditOffset),
    '--operation-start-index', ([string]$script:operationAuditOffset),
    '--output', $stageDiagnosticReport
  )
  if ($stageName -eq 'jd_pilot') {
    $args += @('--browser-audit', $browserAudit)
  }
  if ($null -ne $jdVersionId) {
    $args += @('--jd-version-id', ([string]$jdVersionId))
  }
  $json = & uv run python (Join-Path $repo 'scripts\application_jd_stage_diagnostic.py') @args
  Assert-ExitCode "stage diagnostic $stageName"
  $record = ($json -join '').Trim() | ConvertFrom-Json
  if ($null -eq $record.audit_offsets) {
    throw "stage diagnostic $stageName did not return audit offsets."
  }
  $script:providerAuditOffset = [int]$record.audit_offsets.provider_end_index
  $script:operationAuditOffset = [int]$record.audit_offsets.operation_end_index
  Write-Host "STAGE_DIAGNOSTIC=$stageDiagnosticReport"
}

function Save-FailedBrowserAudit {
  if (-not (Test-Path -LiteralPath $browserAudit)) { return }
  try {
    New-Item -ItemType Directory -Force -Path $stageDiagnosticRoot | Out-Null
    $name = 'failed-browser-audit-' + (Get-Date -Format 'yyyyMMddHHmmss') + '.jsonl'
    Copy-Item -LiteralPath $browserAudit -Destination (Join-Path $stageDiagnosticRoot $name) -Force
    Write-Host "FAILED_BROWSER_AUDIT=$([IO.Path]::Combine($stageDiagnosticRoot, $name))"
  } catch {
    Write-Host "FAILED_BROWSER_AUDIT_COPY_ERROR=$($_.Exception.Message)"
  }
}

function Save-FailedProviderAudit {
  if (-not (Test-Path -LiteralPath $providerAudit)) { return }
  try {
    New-Item -ItemType Directory -Force -Path $stageDiagnosticRoot | Out-Null
    $name = 'failed-provider-egress-' + (Get-Date -Format 'yyyyMMddHHmmss') + '.jsonl'
    Copy-Item -LiteralPath $providerAudit -Destination (Join-Path $stageDiagnosticRoot $name) -Force
    Write-Host "FAILED_PROVIDER_AUDIT=$([IO.Path]::Combine($stageDiagnosticRoot, $name))"
  } catch {
    Write-Host "FAILED_PROVIDER_AUDIT_COPY_ERROR=$($_.Exception.Message)"
  }
}

function Test-StageProviderHttp500([int]$operationStartIndex) {
  if (-not (Test-Path -LiteralPath $operationAudit)) { return $false }
  $records = @(Get-Content -LiteralPath $operationAudit | ForEach-Object {
    try { $_ | ConvertFrom-Json } catch { $null }
  })
  $window = @($records | Select-Object -Skip ([Math]::Max(0, $operationStartIndex)))
  return @($window | Where-Object {
    $_.kind -eq 'provider_request_result' -and
    $_.status -eq 'error' -and
    [int]$_.http_status -eq 500 -and
    $_.failure_category -eq 'provider_http_5xx'
  }).Count -gt 0
}

function Assert-NoProviderCalls([int]$providerStartIndex) {
  if (-not (Test-Path -LiteralPath $providerRequestAudit)) { return }
  $records = @(Get-Content -LiteralPath $providerRequestAudit | ForEach-Object {
    try { $_ | ConvertFrom-Json } catch { $null }
  })
  $window = @($records | Select-Object -Skip ([Math]::Max(0, $providerStartIndex)))
  $calls = @($window | Where-Object kind -eq 'provider_request_metadata')
  if ($calls.Count -gt 0) { throw "Stage A unexpectedly made $($calls.Count) Provider call(s)." }
}

function Get-TriageReplayContext {
  if (-not (Test-Path -LiteralPath $triageReplayContextPath)) {
    throw 'Triage replay context was not captured before the Provider call.'
  }
  $env:APPLICATION_JD_HARNESS_DB = Join-Path $tempData 'data.db'
  $env:APPLICATION_JD_HARNESS_APP = [string]$applicationId
  $env:APPLICATION_JD_HARNESS_TRIAGE_CONTEXT = $triageReplayContextPath
  $code = @'
import hashlib, json, os, sqlite3
from datetime import datetime, timezone
from uuid import UUID

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

context_path = os.environ["APPLICATION_JD_HARNESS_TRIAGE_CONTEXT"]
with open(context_path, encoding="utf-8") as handle:
    context = json.load(handle)
payload = context.get("payload")
if not isinstance(payload, dict) or payload.get("schema_version") != 2:
    raise SystemExit("private triage context has an invalid payload")
application_id = int(os.environ["APPLICATION_JD_HARNESS_APP"])
try:
    normalized_key = str(UUID(str(payload.get("idempotency_key", "")).strip()))
except (ValueError, AttributeError):
    raise SystemExit("captured idempotency key is invalid")
with sqlite3.connect(os.environ["APPLICATION_JD_HARNESS_DB"]) as db:
    row = db.execute(
        "SELECT id, stage_generation, status, idempotency_key, source_snapshot_json, "
        "source_fingerprint_sha256, jd_version_id, lease_expires_at, provider_call_token "
        "FROM opportunity_fit_review_stages "
        "WHERE application_id = ? AND stage = 'triage' AND idempotency_key = ? "
        "ORDER BY id DESC LIMIT 1",
        (application_id, normalized_key),
    ).fetchone()
if row is None:
    raise SystemExit("triage stage for the captured idempotency key is missing")
stage_id, generation, status, key, snapshot_json, source_fingerprint, jd_version_id, lease_expires_at, provider_call_token = row
if status != "provider_unknown":
    raise SystemExit(f"triage stage is not a replayable Provider failure: {status}")
if normalized_key != str(key):
    raise SystemExit("captured idempotency key does not match the frozen stage")
try:
    snapshot = json.loads(snapshot_json)
except (TypeError, ValueError) as exc:
    raise SystemExit(f"frozen source snapshot is invalid: {exc}")
if not isinstance(snapshot, dict):
    raise SystemExit("frozen source snapshot is not an object")
if snapshot.get("application", {}).get("id") != application_id:
    raise SystemExit("frozen snapshot application does not match the request")
resume = snapshot.get("resume")
if not isinstance(resume, dict) or resume.get("id") != payload.get("resume_id"):
    raise SystemExit("captured resume does not match the frozen snapshot")
if jd_version_id != payload.get("jd_version_id"):
    raise SystemExit("captured JD version does not match the frozen stage")
jd = snapshot.get("jd")
if not isinstance(jd, dict) or jd.get("source_label") != payload.get("jd_source_label"):
    raise SystemExit("captured JD source label does not match the frozen snapshot")
snapshot_assertions = snapshot.get("candidate_assertions")
payload_assertions = payload.get("candidate_assertions")
if not isinstance(snapshot_assertions, list) or not isinstance(payload_assertions, list):
    raise SystemExit("captured candidate assertions are not arrays")
expected_assertions = [
    item.get("text") for item in snapshot_assertions
    if isinstance(item, dict) and isinstance(item.get("text"), str)
]
actual_assertions = [
    item.strip() for item in payload_assertions
    if isinstance(item, str) and item.strip()
]
if expected_assertions != actual_assertions:
    raise SystemExit("captured candidate assertions do not match the frozen snapshot")
try:
    parsed_lease_expires_at = datetime.fromisoformat(str(lease_expires_at))
    if parsed_lease_expires_at.tzinfo is None or parsed_lease_expires_at.utcoffset() is None:
        parsed_lease_expires_at = parsed_lease_expires_at.replace(tzinfo=timezone.utc)
    lease_valid_at_capture = parsed_lease_expires_at.astimezone(timezone.utc) > datetime.now(timezone.utc)
except (TypeError, ValueError):
    lease_valid_at_capture = False
print(json.dumps({
    "stage_id": int(stage_id),
    "stage_generation": int(generation),
    "status": str(status),
    "provider_call_token_present": bool(provider_call_token),
    "lease_valid_at_capture": bool(lease_valid_at_capture),
    "idempotency_key_sha256": digest(normalized_key),
    "payload_fingerprint_sha256": digest(canonical(payload)),
    "source_fingerprint_sha256": str(source_fingerprint),
    "lease_expires_at": str(lease_expires_at or ""),
    "same_input_verified": True,
}, separators=(",", ":")))
'@
  $json = $code | & uv run python -
  Assert-ExitCode 'triage replay context verification'
  return (($json -join '').Trim() | ConvertFrom-Json)
}

function Write-TriageReplayMetadata($metadata) {
  New-Item -ItemType Directory -Force -Path $stageDiagnosticRoot | Out-Null
  Add-Content -LiteralPath $stageDiagnosticReport -Value (($metadata | ConvertTo-Json -Compress -Depth 10)) -Encoding utf8
}

function Get-TriageReplayPayload {
  $env:APPLICATION_JD_HARNESS_TRIAGE_CONTEXT = $triageReplayContextPath
  $code = @'
import json, os

context_path = os.environ["APPLICATION_JD_HARNESS_TRIAGE_CONTEXT"]
with open(context_path, encoding="utf-8") as handle:
    private = json.load(handle)
payload = private.get("payload") if isinstance(private, dict) else None
if not isinstance(payload, dict) or payload.get("schema_version") != 2:
    raise SystemExit("private triage context has an invalid payload")
print(json.dumps(private.get("payload"), ensure_ascii=True, sort_keys=True, separators=(",", ":")))
'@
  $json = $code | & uv run python -
  Assert-ExitCode 'triage replay payload serialization'
  return (($json -join '').Trim())
}

function Get-TriageReplayErrorCode($response) {
  if ($null -eq $response) { return '' }
  $stream = $null
  $reader = $null
  try {
    $stream = $response.GetResponseStream()
    if ($null -eq $stream) { return '' }
    $reader = [System.IO.StreamReader]::new(
      $stream,
      [System.Text.UTF8Encoding]::new($false),
      $true
    )
    $text = $reader.ReadToEnd()
    $match = [regex]::Match($text, '"error_code"\s*:\s*"(?<code>[A-Za-z0-9_.-]+)"')
    if ($match.Success) { return $match.Groups['code'].Value }
    return ''
  } catch {
    return ''
  } finally {
    if ($null -ne $reader) { $reader.Dispose() }
    elseif ($null -ne $stream) { $stream.Dispose() }
  }
}

# Replay uses the same idempotency key and is allowed at most one replay.
function Invoke-TriageReplayOnce([int]$providerStartIndex, [int]$operationStartIndex) {
  if (-not (Test-StageProviderHttp500 $operationStartIndex)) { return }
  if ($script:triageReplayCount -ge 1) { throw 'Triage replay exceeded the at-most-one harness limit.' }
  $context = Get-TriageReplayContext
  if (-not $context.same_input_verified) { throw 'Triage replay input could not be verified.' }
  $expiry = [DateTimeOffset]::Parse([string]$context.lease_expires_at)
  $deadline = [DateTimeOffset]::UtcNow.AddSeconds(180)
  while ($expiry -gt [DateTimeOffset]::UtcNow) {
    if ([DateTimeOffset]::UtcNow -gt $deadline) { throw 'Triage replay lease did not expire within the harness bound.' }
    Start-Sleep -Milliseconds 500
  }
  $leaseValidBeforeReplay = $expiry -gt [DateTimeOffset]::UtcNow
  $body = Get-TriageReplayPayload
  $status = 0
  $responseErrorCode = ''
  try {
    $response = Invoke-WebRequest -UseBasicParsing -Method Post `
      -Uri "$baseUrl/api/applications/$applicationId/opportunity-fit-reviews" `
      -ContentType 'application/json' -Body $body -TimeoutSec 300
    $status = [int]$response.StatusCode
    if ($status -ge 400) { $responseErrorCode = Get-TriageReplayErrorCode $response.BaseResponse }
  } catch {
    if ($null -ne $_.Exception.Response) {
      $status = [int]$_.Exception.Response.StatusCode.value__
      $responseErrorCode = Get-TriageReplayErrorCode $_.Exception.Response
    }
  }
  $script:triageReplayCount = 1
  $providerRecords = @(Get-Content -LiteralPath $providerRequestAudit | ForEach-Object { $_ | ConvertFrom-Json })
  $providerWindow = @($providerRecords | Select-Object -Skip ([Math]::Max(0, $providerStartIndex)))
  $firstProvider = @($providerWindow | Where-Object kind -eq 'provider_request_metadata' | Select-Object -First 1)
  $replayProviders = @($providerWindow | Where-Object kind -eq 'provider_request_metadata' | Select-Object -Skip 1)
  $sameProviderInput = @($replayProviders | Where-Object {
    $_.input_fingerprint_sha256 -eq $firstProvider.input_fingerprint_sha256
  }).Count -gt 0
  $replayProvider = if ($replayProviders.Count -eq 1) { $replayProviders[0] } else { $null }
  $metadata = [ordered]@{
    kind = 'triage_replay'
    stage = 'triage'
    replay_attempted = $true
    replay_count = 1
    same_input_verified = [bool]$context.same_input_verified
    provider_input_fingerprint_match = [bool]$sameProviderInput
    source_fingerprint_sha256 = [string]$context.source_fingerprint_sha256
    payload_fingerprint_sha256 = [string]$context.payload_fingerprint_sha256
    stage_id = [int]$context.stage_id
    stage_generation = [int]$context.stage_generation
    stage_status = [string]$context.status
    lease_valid_at_capture = [bool]$context.lease_valid_at_capture
    lease_valid_before_replay = [bool]$leaseValidBeforeReplay
    provider_call_token_present = [bool]$context.provider_call_token_present
    idempotency_key_sha256 = [string]$context.idempotency_key_sha256
    provider_input_fingerprint_sha256 = [string]$firstProvider.input_fingerprint_sha256
    replay_provider_input_fingerprint_sha256 = [string]$replayProvider.input_fingerprint_sha256
    response_status = $status
    response_error_code = [string]$responseErrorCode
    provider_request_count = $replayProviders.Count
    provider_model = [string]$firstProvider.model
    replay_provider_model = [string]$replayProvider.model
  }
  Write-TriageReplayMetadata $metadata
  Save-StageDiagnostic 'triage'
  if ($replayProviders.Count -ne 1 -or -not $sameProviderInput) {
    throw 'Triage replay did not produce exactly one Provider call with the same input fingerprint.'
  }
  if ($status -notin @(200, 201)) {
    throw "Triage same-input Provider replay returned HTTP $status."
  }
}

function Assert-LocalBrowser($records) {
  $origin = [Uri]$baseUrl
  foreach ($record in @($records | Where-Object kind -eq 'browser_request')) {
    $uri = [Uri]$record.url
    if ($uri.Scheme -ne $origin.Scheme -or $uri.Host -ne $origin.Host -or $uri.Port -ne $origin.Port) {
      throw 'Browser accessed a non-local URL.'
    }
    $routeMatch = [regex]::Match($record.url, '/api/applications/(\d+)(?:/|$)')
    $context = $record.request_context
    if ($routeMatch.Success -and $null -ne $context -and $null -ne $context.payload_application_id) {
      if ([int]$context.payload_application_id -ne [int]$routeMatch.Groups[1].Value) {
        throw 'Browser request payload application_id does not match its bound URL.'
      }
    }
  }
}

function Test-LoopbackProviderEndpoints($allowedEndpoints) {
  $items = @($allowedEndpoints)
  if ($items.Count -lt 1) { return $false }
  foreach ($endpoint in $items) {
    $hostName = [string]$endpoint.Host
    $scheme = [string]$endpoint.Scheme
    if ($scheme -ne 'http' -or $hostName -notin @('127.0.0.1', 'localhost')) { return $false }
  }
  return $true
}

function Assert-LoopbackProviderRequests {
  if (-not (Test-Path -LiteralPath $providerRequestAudit)) {
    throw 'Controlled Provider request audit output is missing.'
  }
  $requests = @(Get-Content -LiteralPath $providerRequestAudit | ForEach-Object {
    $_ | ConvertFrom-Json
  } | Where-Object kind -eq 'provider_request_metadata')
  if ($requests.Count -lt 1) { throw 'No controlled Provider request was observed.' }
  foreach ($request in $requests) {
    $endpoint = $request.endpoint
    if ($null -eq $endpoint -or [string]$endpoint.scheme -ne 'http' -or
        [string]$endpoint.host -notin @('127.0.0.1', 'localhost')) {
      throw 'Controlled Provider request endpoint was not loopback.'
    }
  }
}

function Assert-ProviderEgress($allowedEndpoints) {
  if (Test-LoopbackProviderEndpoints $allowedEndpoints) {
    Assert-LoopbackProviderRequests
    return
  }
  if (-not (Test-Path -LiteralPath $providerAudit)) { throw 'Provider audit output is missing.' }
  $entries = @(Get-Content -LiteralPath $providerAudit | ForEach-Object { $_ | ConvertFrom-Json })
  $rejected = @($entries | Where-Object status -eq 'rejected')
  if ($rejected.Count -gt 0) {
    $tuples = @($rejected | ForEach-Object { "$($_.host):$($_.port)" } | Sort-Object -Unique) -join ', '
    throw "Provider egress proxy rejected an outbound connection ($tuples)."
  }
  $connected = @($entries | Where-Object status -eq 'connected')
  if ($connected.Count -lt 1) { throw 'No real Provider connection was observed.' }
  foreach ($entry in $connected) {
    $tuple = "$($entry.scheme)://$($entry.host):$($entry.port)"
    if (-not (@($allowedEndpoints | Where-Object tuple -eq $tuple).Count)) {
      throw "Provider egress endpoint is outside the configured allowlist: $tuple"
    }
  }
}

function Assert-StageA($records) {
  $jdPosts = @($records | Where-Object { $_.kind -eq 'browser_request' -and $_.method -eq 'POST' -and $_.url -match '/api/applications/[0-9]+/job-description/versions$' })
  if ($jdPosts.Count -lt 1) { throw 'Stage A did not create a UI JD version.' }
  if (@($jdPosts | Where-Object { $_.request_context.application_id -ne [int]$applicationId }).Count -ne 0) { throw 'Stage A mixed applications.' }
  $jdResponses = @($records | Where-Object {
    $_.kind -eq 'browser_response' -and $_.method -eq 'POST' -and
    $_.url -match '/api/applications/[0-9]+/job-description/versions$' -and
    $_.response_status -in @(200, 201) -and $null -ne $_.response_jd_version_id
  })
  if ($jdResponses.Count -lt 1) { throw 'Stage A did not record a successful UI JD version response.' }
  $historyResponses = @($records | Where-Object {
    $_.kind -eq 'browser_response' -and $_.method -eq 'GET' -and
    $_.url -match '/job-description/versions(?:\?.*)?$' -and
    $_.response_status -eq 200 -and $null -ne $_.response_source_kinds
  })
  if ($historyResponses.Count -lt 1) { throw 'Stage A did not read JD history after Pilot confirmation.' }
  $sourceKinds = @(
    $jdResponses | ForEach-Object { [string]$_.response_source_kind }
    $historyResponses | ForEach-Object { @($_.response_source_kinds) | ForEach-Object { [string]$_ } }
  ) | Where-Object { $_ } | Sort-Object -Unique
  if ('ui' -notin $sourceKinds -or 'pilot' -notin $sourceKinds) { throw 'Stage A did not prove both UI and Pilot JD sources from saved/read-back version data.' }
  $historyVersionIds = @(
    $historyResponses | ForEach-Object { @($_.response_jd_version_ids) | ForEach-Object { [int]$_ } }
  ) | Sort-Object -Unique
  if ($historyVersionIds.Count -lt 2) { throw 'Stage A did not read both saved JD versions.' }
  $detailResponses = @($records | Where-Object {
    $_.kind -eq 'browser_response' -and $_.method -eq 'GET' -and
    $_.url -match '/job-description/versions/[0-9]+$' -and
    $_.response_status -eq 200
  })
  if ($detailResponses.Count -lt 1) { throw 'Stage A did not read JD detail.' }
  if (-not ($records | Where-Object { $_.kind -eq 'browser_request' -and $_.method -eq 'POST' -and $_.url -match '/api/chat(?:/stream)?$' })) { throw 'Stage A did not record Pilot chat.' }
  if (-not ($records | Where-Object { $_.kind -eq 'browser_request' -and $_.method -eq 'POST' -and $_.url -match '/api/chat/confirm$|/api/chat/confirm/stream$' })) { throw 'Stage A did not record Pilot confirmation.' }
  $confirmationResponses = @($records | Where-Object {
    $_.kind -eq 'browser_response' -and $_.method -eq 'POST' -and
    $_.url -match '/api/chat/confirm$|/api/chat/confirm/stream$' -and $_.response_status -in @(200, 201)
  })
  if ($confirmationResponses.Count -lt 1) { throw 'Stage A did not record a successful Pilot confirmation response.' }
}

function Assert-ConsumerRequest($records, [string]$consumer, [switch]$AllowProvider500) {
  $pattern = switch ($consumer) {
    'triage' { '/opportunity-fit-reviews$' }
    'material-kit' { '/material-kit/generate$' }
    'interview-preparation' { '/interview-preparation-proposals$' }
  }
  $matches = @($records | Where-Object {
    $_.kind -eq 'browser_request' -and $_.method -eq 'POST' -and $_.url -match $pattern -and
    $_.request_context.application_id -eq [int]$applicationId -and
    $_.request_context.jd_version_id -eq [int]$jdVersionId
  })
  if ($matches.Count -lt 1) { throw "Stage B did not submit $consumer with the frozen JD version." }
  $successful = @($records | Where-Object {
    $_.kind -eq 'browser_response' -and $_.method -eq 'POST' -and $_.url -match $pattern -and
    $_.request_context.application_id -eq [int]$applicationId -and
    $_.request_context.jd_version_id -eq [int]$jdVersionId -and
    $_.response_status -in @(200, 201) -and $_.response_jd_version_id -eq [int]$jdVersionId
  })
  if ($AllowProvider500 -and @($records | Where-Object {
    $_.kind -eq 'browser_response' -and $_.method -eq 'POST' -and $_.url -match $pattern -and
    $_.response_status -eq 502 -and $_.response_error_code -match 'provider'
  }).Count -gt 0) {
    return
  }
  if ($successful.Count -lt 1) { throw "Stage B did not record a successful $consumer response for the frozen JD version." }
}

try {
  $configPath = Join-Path $sourceData 'config.json'
  if (-not (Test-Path -LiteralPath $configPath)) { throw 'Provider config is missing.' }
  New-Item -ItemType Directory -Force -Path $tempData | Out-Null
  if ([string]::IsNullOrWhiteSpace($CdpUrl)) { $CdpUrl = Start-TemporaryBrowser 'about:blank' }
  Write-Host "CDP_URL=$CdpUrl"
  $temporaryConfigPath = Join-Path $tempData 'config.json'
  Copy-Item -LiteralPath $configPath -Destination $temporaryConfigPath
  $temporaryConfig = Get-Content -LiteralPath $temporaryConfigPath -Raw | ConvertFrom-Json
  $providerOverrideBaseUrl = [string]$env:APPLICATION_JD_PROVIDER_BASE_URL
  $providerOverrideModel = [string]$env:APPLICATION_JD_PROVIDER_MODEL
  $providerOverrideApiKey = [string]$env:APPLICATION_JD_PROVIDER_API_KEY
  if (-not $providerOverrideApiKey) { $providerOverrideApiKey = [string]$env:OFFERPILOT_JD_PROVIDER_KEY }
  if ($providerOverrideBaseUrl) { $temporaryConfig.base_url = $providerOverrideBaseUrl }
  if ($providerOverrideApiKey) { $temporaryConfig.api_key = $providerOverrideApiKey }
  if ($providerOverrideModel) {
    $temporaryConfig.model = $providerOverrideModel
  } else {
    $temporaryConfig.model = 'deepseek-v4-flash'
  }
  foreach ($provider in @($temporaryConfig.providers)) {
    if ([string]$provider.id -eq [string]$temporaryConfig.active_provider_id) {
      if ($providerOverrideBaseUrl) { $provider.base_url = $providerOverrideBaseUrl }
      if ($providerOverrideApiKey) { $provider.api_key = $providerOverrideApiKey }
      if ($providerOverrideModel) {
        $provider.model = $providerOverrideModel
      } else {
        $provider.model = 'deepseek-v4-flash'
      }
    }
  }
  [IO.File]::WriteAllText(
    $temporaryConfigPath,
    ($temporaryConfig | ConvertTo-Json -Depth 30),
    [Text.UTF8Encoding]::new($false)
  )
  $providers = @(Get-ProviderEndpoints $temporaryConfigPath)
  if ($providers.Count -eq 0) { throw 'No enabled Provider endpoint is configured.' }
  $allowlist = Join-Path $tempData 'provider-allowlist.json'
  $providers | ConvertTo-Json -Compress | Set-Content -LiteralPath $allowlist -Encoding utf8
  $port = Get-FreePort
  $proxyPort = Get-FreePort
  $baseUrl = "http://127.0.0.1:$port"
  $env:OFFERPILOT_DATA = $tempData
  $env:OFFERPILOT_HTTP_AUDIT_FILE = $httpAudit
  $env:OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE = $providerRequestAudit
  $env:OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE = $operationAudit
  $env:OFFERPILOT_FULL_VERIFY_OPERATION = 'application_jd_browser'
  $env:OFFERPILOT_FULL_VERIFY_ACTIVE_STAGE = 'application_jd_browser'
  $env:HTTPS_PROXY = "http://127.0.0.1:$proxyPort"
  $env:HTTP_PROXY = "http://127.0.0.1:$proxyPort"
  $env:NO_PROXY = '127.0.0.1,localhost'
  $env:LITELLM_LOCAL_MODEL_COST_MAP = 'True'
  $proxy = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', "Set-Location '$repo'; uv run python scripts/provider-egress-proxy.py --port $proxyPort --audit '$providerAudit' --expected-endpoints-file '$allowlist'")
  $server = Start-Process powershell -WindowStyle Hidden -PassThru -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', "Set-Location '$repo'; uv run oc start --port $port")
  $healthy = $false
  for ($i = 0; $i -lt 120; $i++) {
    if ($server.HasExited) { throw 'Isolated service exited before readiness.' }
    try { if (Invoke-RestMethod -Uri "$baseUrl/api/health" -TimeoutSec 2) { $healthy = $true; break } } catch { Start-Sleep -Milliseconds 500 }
  }
  if (-not $healthy) { throw 'Isolated service did not become healthy.' }

  $beforeCleanup = Get-DbSnapshot
  $application = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/applications" -ContentType 'application/json' -Body '{"company_name":"\u7b71\u54f2\u6848\u4f8b\u516c\u53f8","position_name":"\u540e\u7aef\u5de5\u7a0b\u5e08","status":"applied"}'
  $applicationId = [int]$application.id
  $resume = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/resumes" -ContentType 'application/json' -Body '{"title":"\u7b71\u54f2\u540e\u7aef\u7b80\u5386","text":"Python FastAPI SQLAlchemy"}'
  $resumeId = [int]$resume.id
  $event = Invoke-RestMethod -Method Post -Uri "$baseUrl/api/application-events" -ContentType 'application/json' -Body (ConvertTo-Json @{ application_id = $applicationId; event_type = 'interview'; subtype = 'technical'; scheduled_at = '2026-12-01T10:00:00Z'; duration_minutes = 60; status = 'todo' })
  $eventId = [int]$event.id
  Write-Host "SERVICE_URL=$baseUrl"
  Write-Host "APPLICATION_ID=$applicationId"
  Write-Host "RESUME_ID=$resumeId"
  Write-Host "EVENT_ID=$eventId"
  $beforeA = Get-DbSnapshot

  $auditor = Start-Process powershell -WindowStyle Hidden -PassThru `
    -RedirectStandardOutput $browserStdout -RedirectStandardError $browserStderr `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', "Set-Location '$repo'; uv run python scripts/browser-network-audit.py --debugging-url '$CdpUrl' --expected-url '$baseUrl' --audit '$browserAudit' --stop-file '$browserStop' --ready-file '$browserReady' --diagnostic-file '$browserDiagnostic' --flush-file '$browserFlush' --flushed-file '$browserFlushed' --private-context-file '$triageReplayContextPath'")
  for ($i = 0; $i -lt 120; $i++) {
    if ($auditor.HasExited) { throw 'Browser auditor exited before readiness.' }
    if (Test-Path -LiteralPath $browserReady) { break }
    Start-Sleep -Milliseconds 500
  }
  if (-not (Test-Path -LiteralPath $browserReady)) { throw 'Browser auditor did not become ready.' }
  Assert-BrowserAuditorHealthy
  Write-Host 'Dedicated browser target is ready. Complete JD UI and Pilot confirmation in that target.'
  Write-Host 'Then complete triage, material kit, and interview preparation in that same target.'
  Wait-StageCompletion 'jd_pilot'
  Assert-BrowserAuditorHealthy
  Flush-BrowserAudit
  $records = Get-BrowserRecords
  Assert-LocalBrowser $records
  Assert-StageA $records
  Assert-NoProviderCalls $script:providerAuditOffset
  Save-StageDiagnostic 'jd_pilot'
  $afterA = Get-DbSnapshot
  Assert-StageUnchanged $beforeA $afterA @('application_jd_versions', 'conversations', 'chat_messages')
  $env:APPLICATION_JD_HARNESS_DB = Join-Path $tempData 'data.db'
  $env:APPLICATION_JD_HARNESS_APP = [string]$applicationId
  $jdVersionCode = @'
import os, sqlite3
db = sqlite3.connect(os.environ["APPLICATION_JD_HARNESS_DB"])
print(db.execute(
    "select id from application_jd_versions where application_id = ? "
    "order by version_number desc limit 1",
    (int(os.environ["APPLICATION_JD_HARNESS_APP"]),),
).fetchone()[0])
'@
  $jdVersionId = [int](($jdVersionCode | & uv run python -).Trim())
  Assert-ExitCode 'JD version readback'

  if ($Stage -eq 'jd-only') {
    Write-Host 'Application JD browser acceptance passed.'
  } else {
    foreach ($consumer in @('triage', 'material-kit', 'interview-preparation')) {
      $consumerBefore = Get-DbSnapshot
      $consumerProviderStart = $script:providerAuditOffset
      $consumerOperationStart = $script:operationAuditOffset
      Write-Host "Complete $consumer in the dedicated browser target."
      Wait-StageCompletion $consumer
      Assert-BrowserAuditorHealthy
      Flush-BrowserAudit
      $records = Get-BrowserRecords
      Assert-LocalBrowser $records
      $triageProvider500 = $consumer -eq 'triage' -and (Test-StageProviderHttp500 $consumerOperationStart)
      if ($triageProvider500) {
        Assert-ConsumerRequest $records $consumer -AllowProvider500
      } else {
        Assert-ConsumerRequest $records $consumer
      }
      switch ($consumer) {
        'triage' {
          Save-StageDiagnostic 'triage'
          if ($triageProvider500) {
            Invoke-TriageReplayOnce $consumerProviderStart $consumerOperationStart
          }
        }
        'material-kit' { Save-StageDiagnostic 'material_kit' }
        'interview-preparation' { Save-StageDiagnostic 'interview_preparation' }
      }
      $consumerAfter = Get-DbSnapshot
      $allowed = switch ($consumer) {
        'triage' { @('opportunity_fit_review_sessions', 'opportunity_fit_review_stages') }
        'material-kit' { @('application_material_kits') }
        'interview-preparation' { @('interview_preparation_proposals') }
      }
      Assert-StageUnchanged $consumerBefore $consumerAfter $allowed
      Clear-Consumer $consumer
      Assert-StageUnchanged $consumerBefore (Get-DbSnapshot) @()
      if ($Stage -eq 'triage-only' -and $consumer -eq 'triage') {
        Assert-ProviderEgress $providers
        Complete-BrowserAudit
        Write-Host 'Application JD targeted Triage replay acceptance passed.'
        return
      }
    }
    Assert-ProviderEgress $providers
  }
  Complete-BrowserAudit
  Write-Host 'Application JD browser acceptance passed.'
} catch {
  Write-Host 'Application JD browser acceptance failed.'
  Write-Host "FAILURE_REASON=$($_.Exception.Message)"
  Save-FailedBrowserAudit
  Save-FailedProviderAudit
  throw
} finally {
  $cleanupErrors = [System.Collections.Generic.List[string]]::new()
  $serviceStopped = $true
  $allProcessesStopped = $true
  try {
    if ($browserStop -and $null -ne $auditor -and -not $auditor.HasExited) {
      New-Item -ItemType File -Force -Path $browserStop | Out-Null
      Wait-AuditorExit -AllowFailure
    }
  } catch {
    [void]$cleanupErrors.Add("auditor stop request: $($_.Exception.Message)")
  }
  foreach ($entry in @(
    [pscustomobject]@{ Process = $auditor; Label = 'browser auditor' },
    [pscustomobject]@{ Process = $server; Label = 'isolated service' },
    [pscustomobject]@{ Process = $proxy; Label = 'Provider proxy' },
    [pscustomobject]@{ Process = $browser; Label = 'temporary browser' }
  )) {
    try {
      Stop-Tree $entry.Process $entry.Label
    } catch {
      [void]$cleanupErrors.Add("$($entry.Label): $($_.Exception.Message)")
      $allProcessesStopped = $false
      if ($entry.Label -eq 'isolated service') { $serviceStopped = $false }
    }
  }
  if ($serviceStopped -and $applicationId -and $resumeId -and $eventId -and (Test-Path -LiteralPath (Join-Path $tempData 'data.db'))) {
    try {
      Clear-SyntheticData
      if ($beforeCleanup) { Assert-StageUnchanged $beforeCleanup (Get-DbSnapshot) @() }
    } catch {
      [void]$cleanupErrors.Add("synthetic data cleanup: $($_.Exception.Message)")
    }
  } elseif (-not $serviceStopped) {
    [void]$cleanupErrors.Add('synthetic data cleanup skipped because the isolated service did not exit')
  }
  try {
    if ($previousData) { $env:OFFERPILOT_DATA = $previousData } else { Remove-Item Env:OFFERPILOT_DATA -ErrorAction SilentlyContinue }
    if ($previousHttpAudit) { $env:OFFERPILOT_HTTP_AUDIT_FILE = $previousHttpAudit } else { Remove-Item Env:OFFERPILOT_HTTP_AUDIT_FILE -ErrorAction SilentlyContinue }
    if ($previousProviderRequestAudit) { $env:OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE = $previousProviderRequestAudit } else { Remove-Item Env:OFFERPILOT_PROVIDER_REQUEST_AUDIT_FILE -ErrorAction SilentlyContinue }
    if ($previousOperationAudit) { $env:OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE = $previousOperationAudit } else { Remove-Item Env:OFFERPILOT_FULL_VERIFY_OPERATION_AUDIT_FILE -ErrorAction SilentlyContinue }
    if ($previousFullVerifyOperation) { $env:OFFERPILOT_FULL_VERIFY_OPERATION = $previousFullVerifyOperation } else { Remove-Item Env:OFFERPILOT_FULL_VERIFY_OPERATION -ErrorAction SilentlyContinue }
    if ($previousFullVerifyStage) { $env:OFFERPILOT_FULL_VERIFY_ACTIVE_STAGE = $previousFullVerifyStage } else { Remove-Item Env:OFFERPILOT_FULL_VERIFY_ACTIVE_STAGE -ErrorAction SilentlyContinue }
    if ($previousHttpsProxy) { $env:HTTPS_PROXY = $previousHttpsProxy } else { Remove-Item Env:HTTPS_PROXY -ErrorAction SilentlyContinue }
    if ($previousHttpProxy) { $env:HTTP_PROXY = $previousHttpProxy } else { Remove-Item Env:HTTP_PROXY -ErrorAction SilentlyContinue }
    if ($previousNoProxy) { $env:NO_PROXY = $previousNoProxy } else { Remove-Item Env:NO_PROXY -ErrorAction SilentlyContinue }
    if ($previousLiteLlmLocalCostMap) { $env:LITELLM_LOCAL_MODEL_COST_MAP = $previousLiteLlmLocalCostMap } else { Remove-Item Env:LITELLM_LOCAL_MODEL_COST_MAP -ErrorAction SilentlyContinue }
    Remove-Item Env:APPLICATION_JD_HARNESS_DB, Env:APPLICATION_JD_HARNESS_APP, Env:APPLICATION_JD_HARNESS_RESUME, Env:APPLICATION_JD_HARNESS_EVENT, Env:APPLICATION_JD_HARNESS_CONSUMER -ErrorAction SilentlyContinue
  } catch {
    [void]$cleanupErrors.Add("environment restore: $($_.Exception.Message)")
  }
  try {
    if (Test-Path -LiteralPath $triageReplayContextPath) {
      Remove-Item -LiteralPath $triageReplayContextPath -Force -ErrorAction Stop
    }
  } catch {
    [void]$cleanupErrors.Add("private triage replay context cleanup: $($_.Exception.Message)")
  }
  if ($allProcessesStopped) {
    try {
      if (Test-Path -LiteralPath $tempData) {
        Remove-Item -LiteralPath $tempData -Recurse -Force -ErrorAction Stop
      }
    } catch {
      [void]$cleanupErrors.Add("temporary directory cleanup: $($_.Exception.Message)")
    }
  } else {
    [void]$cleanupErrors.Add("temporary directory retained because a child process did not exit: $tempData")
  }
  if ($cleanupErrors.Count -gt 0) { throw ($cleanupErrors -join '; ') }
}
