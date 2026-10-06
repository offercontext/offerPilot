param(
  [Parameter(Mandatory=$true)][ValidateSet('install', 'snapshot', 'cleanup')][string]$Mode
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)

if ($Mode -eq 'install') {
  if (Test-Path -LiteralPath $env:UI_INSTALL_DIR) { throw 'Install destination is not fresh' }
  if (Test-Path -LiteralPath $env:UI_USER_DATA) { throw 'Existing real app data must not be touched' }
  $installer = [System.Diagnostics.ProcessStartInfo]::new()
  $installer.FileName = $env:UI_INSTALLER
  # NSIS requires /D last, without quotes even when the value contains spaces.
  $installer.Arguments = '/S /currentuser /D=' + $env:UI_INSTALL_DIR
  $installer.UseShellExecute = $false
  $process = [System.Diagnostics.Process]::Start($installer)
  if (-not $process.WaitForExit(180000)) {
    # This is our own installer handle, never a PID read from application output.
    $process.Kill()
    throw 'Installer timed out'
  }
  if ($process.ExitCode -ne 0) { throw 'Installer failed' }
  @{ exitCode = $process.ExitCode } | ConvertTo-Json -Compress
  exit 0
}

$processes = @(Get-CimInstance Win32_Process | Where-Object {
  $_.ExecutablePath -and ($_.ExecutablePath -ieq $env:UI_EXE -or $_.ExecutablePath -ieq $env:UI_BACKEND)
} | ForEach-Object {
  @{ pid = [int]$_.ProcessId; parentPid = [int]$_.ParentProcessId;
    path = $_.ExecutablePath; created = $_.CreationDate.ToUniversalTime().ToString('o') }
})
if ($Mode -eq 'snapshot') {
  $ids = @($processes | ForEach-Object { $_.pid })
  $listeners = @(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {
    $ids -contains [int]$_.OwningProcess
  } | ForEach-Object {
    @{ pid = [int]$_.OwningProcess; address = $_.LocalAddress; port = [int]$_.LocalPort }
  })
  @{ processes = $processes; listeners = $listeners } | ConvertTo-Json -Depth 5 -Compress
  exit 0
}

# Failure-only containment. Input identities were independently verified by CIM.
# Recheck executable path, parent and creation time to refuse PID-reuse mistakes.
$owned = @($env:UI_OWNED_IDENTITIES | ConvertFrom-Json)
$stopped = 0
foreach ($identity in $owned) {
  $match = @($processes | Where-Object {
    $_.pid -eq $identity.pid -and $_.parentPid -eq $identity.parentPid -and
    $_.path -ieq $identity.path -and $_.created -eq $identity.created
  })
  if ($match.Count -eq 1) {
    Stop-Process -Id $identity.pid -Force -ErrorAction Stop
    $stopped++
  }
}
@{ cleanupStopped = $stopped } | ConvertTo-Json -Compress
