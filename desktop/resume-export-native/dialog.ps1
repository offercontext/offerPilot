param(
  [Parameter(Mandatory=$true)][int]$OwnerPid,
  [Parameter(Mandatory=$true)][string]$Executable,
  [Parameter(Mandatory=$true)][ValidateSet('save','cancel')][string]$Choice,
  [Parameter(Mandatory=$true)][string]$Destination,
  [Parameter(Mandatory=$true)][string]$Screenshot
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
# Keep this source ASCII for Windows PowerShell 5.1's script decoder. Its full
# .NET Framework supplies UIAutomation/WPF on the hosted Windows image.
$title = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('5L+d5a2YIE9mZmVyUGlsb3Qg5a+85Ye65paH5Lu2'))
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_ENVIRONMENT -ne 'github-hosted' -or $env:RUNNER_OS -ne 'Windows') { throw 'Disposable hosted Windows only' }
if ((Get-Process -Id $OwnerPid).Path -ine $Executable) { throw 'Unexpected dialog owner executable' }
Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes, System.Drawing, System.Windows.Forms
$root = [System.Windows.Automation.AutomationElement]::RootElement
$deadline = [DateTime]::UtcNow.AddSeconds(30)
$dialog = $null
while ([DateTime]::UtcNow -lt $deadline) {
  $windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
  foreach ($window in $windows) {
    if ($window.Current.ProcessId -eq $OwnerPid -and $window.Current.ClassName -eq '#32770' -and
        $window.Current.Name -eq $title) { $dialog = $window; break }
  }
  if ($null -ne $dialog) { break }
  Start-Sleep -Milliseconds 100
}
if ($null -eq $dialog) { throw 'Real Windows Save dialog did not appear for the verified process' }
$cancel = @()
try {
$bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
$bitmap = [System.Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
$graphics = [System.Drawing.Graphics]::FromImage($bitmap)
try {
  $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bitmap.Size)
  $bitmap.Save($Screenshot, [System.Drawing.Imaging.ImageFormat]::Png)
} finally { $graphics.Dispose(); $bitmap.Dispose() }
$controls = $dialog.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
$cancel = @($controls | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.AutomationId -eq '2' })
  if ($Choice -eq 'save') {
    if (Test-Path -LiteralPath $Destination) { throw 'Native save destination must be fresh' }
    $edits = @($controls | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Edit -and
      ($_.Current.AutomationId -eq '1001' -or $_.Current.Name -eq 'File name:') })
    if ($edits.Count -ne 1) { throw "Expected one native filename edit, found $($edits.Count)" }
    $edits[0].SetFocus()
    $value = $edits[0].GetCurrentPattern([System.Windows.Automation.ValuePattern]::Pattern)
    $value.SetValue($Destination)
    if ($value.Current.Value -ne $Destination) { throw 'Native filename field did not retain selected path' }
    $save = @($controls | Where-Object { $_.Current.ControlType -eq [System.Windows.Automation.ControlType]::Button -and $_.Current.AutomationId -eq '1' })
    if ($save.Count -ne 1) { throw 'Native Save button is ambiguous' }
    $save[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
  } else {
    if ($cancel.Count -ne 1) { throw 'Native Cancel button is ambiguous' }
    $cancel[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke()
  }
  @{ choice=$Choice; ownerPid=$OwnerPid; title=$title; nativeClass='#32770'; automation='Windows UIAutomation'; dialogReplaced=$false } | ConvertTo-Json -Compress
} catch {
  if ($cancel.Count -eq 1) { try { $cancel[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() } catch {} }
  else { try { $dialog.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close() } catch {} }
  throw
}
