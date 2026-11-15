param([Parameter(Mandatory=$true)][ValidateSet('install', 'snapshot', 'cleanup')][string]$Mode)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)

if ($Mode -eq 'install') {
  if ($env:GITHUB_ACTIONS -ne 'true' -or -not $env:RUNNER_TEMP) { throw 'Dedicated Windows CI runner required' }
  if (Test-Path -LiteralPath $env:LOCALIZATION_INSTALL_DIR) { throw 'Install destination must be absent' }
  if (Test-Path -LiteralPath $env:LOCALIZATION_USER_DATA) { throw 'Existing real app data is a blocker; never delete it' }
  $start = [Diagnostics.ProcessStartInfo]::new()
  $start.FileName = $env:LOCALIZATION_INSTALLER
  # NSIS /D is last, with no quotes around the value (including Unicode/spaces).
  $start.Arguments = '/S /currentuser /D=' + $env:LOCALIZATION_INSTALL_DIR
  $start.UseShellExecute = $false
  $installer = [Diagnostics.Process]::Start($start)
  if (-not $installer.WaitForExit(180000)) {
    $installer.Kill()
    throw 'Owned installer timed out'
  }
  if ($installer.ExitCode -ne 0) { throw 'NSIS installation failed' }
  @{ exitCode=$installer.ExitCode } | ConvertTo-Json -Compress
  exit 0
}

$processes = @(Get-CimInstance Win32_Process | Where-Object {
  $_.ExecutablePath -and ($_.ExecutablePath -ieq $env:LOCALIZATION_EXE -or $_.ExecutablePath -ieq $env:LOCALIZATION_BACKEND)
} | ForEach-Object {
  @{ pid=[int]$_.ProcessId; parentPid=[int]$_.ParentProcessId; path=$_.ExecutablePath; created=$_.CreationDate.ToUniversalTime().ToString('o') }
})
if ($Mode -eq 'snapshot') {
  $ids = @($processes | ForEach-Object { $_.pid })
  $connections = @(Get-NetTCPConnection -ErrorAction SilentlyContinue | Where-Object { $ids -contains [int]$_.OwningProcess })
  $listeners = @($connections | Where-Object { $_.State -eq 'Listen' } | ForEach-Object {
    @{ pid=[int]$_.OwningProcess; address=$_.LocalAddress; port=[int]$_.LocalPort }
  })
  $outbound = @($connections | Where-Object {
    $_.State -in @('Established', 'SynSent') -and $_.RemoteAddress -notin @('127.0.0.1', '::1', '::ffff:127.0.0.1')
  } | ForEach-Object { @{ pid=[int]$_.OwningProcess; address=$_.RemoteAddress; port=[int]$_.RemotePort; state=[string]$_.State } })
  @{ processes=$processes; listeners=$listeners; outbound=$outbound } | ConvertTo-Json -Depth 5 -Compress
  exit 0
}

# Failure containment only. No process-name glob, taskkill /T, profile deletion,
# uninstall, or PID copied from product logs. Revalidate all identity fields.
$stopped = 0
foreach ($identity in @($env:LOCALIZATION_OWNED_IDENTITIES | ConvertFrom-Json)) {
  # ConvertFrom-Json may turn ISO strings into DateTime. Compare the same UTC
  # round-trip representation without dropping birth-time precision or fields.
  $created = $identity.created
  if ($created -is [DateTime]) { $created = $created.ToUniversalTime().ToString('o') }
  if ($created -isnot [string] -or $created -cnotmatch '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{7}Z$') { continue }
  $match = @($processes | Where-Object {
    $_.pid -eq $identity.pid -and $_.parentPid -eq $identity.parentPid -and
    $_.path -ieq $identity.path -and $_.created -ceq $created
  })
  if ($match.Count -eq 1) { Stop-Process -Id $identity.pid -Force -ErrorAction Stop; $stopped++ }
}
@{ stopped=$stopped } | ConvertTo-Json -Compress
