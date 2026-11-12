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
# Narrow Win32 adapter for this verified modal only. UIA on this hosted image
# exposes standard filename/buttons as Pane, so validate actual HWND classes.
Add-Type @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class ExportNative {
  public delegate bool EnumProc(IntPtr hwnd, IntPtr data);
  [DllImport("user32.dll")] static extern bool EnumChildWindows(IntPtr p, EnumProc f, IntPtr data);
  [DllImport("user32.dll")] public static extern bool IsWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool IsChild(IntPtr p, IntPtr c);
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern int GetWindowText(IntPtr h, StringBuilder s, int n);
  [DllImport("user32.dll")] static extern int GetDlgCtrlID(IntPtr h);
  [DllImport("user32.dll")] static extern bool IsWindowEnabled(IntPtr h);
  [DllImport("user32.dll", CharSet=CharSet.Unicode, EntryPoint="SendMessageTimeoutW")] static extern IntPtr Write(IntPtr h, uint m, UIntPtr w, string s, uint flags, uint ms, out UIntPtr result);
  [DllImport("user32.dll", CharSet=CharSet.Unicode, EntryPoint="SendMessageTimeoutW")] static extern IntPtr Read(IntPtr h, uint m, UIntPtr w, StringBuilder s, uint flags, uint ms, out UIntPtr result);
  [DllImport("user32.dll", CharSet=CharSet.Unicode)] static extern bool PostMessage(IntPtr h, uint m, IntPtr w, IntPtr l);
  public static string Class(IntPtr h) { var s=new StringBuilder(256); GetClassName(h,s,s.Capacity); return s.ToString(); }
  public static string Title(IntPtr h) { var s=new StringBuilder(1024); GetWindowText(h,s,s.Capacity); return s.ToString(); }
  public static uint Pid(IntPtr h) { uint p; GetWindowThreadProcessId(h,out p); return p; }
  public static IntPtr[] Children(IntPtr h) { var a=new List<IntPtr>(); EnumChildWindows(h,(c,d)=>{a.Add(c);return true;},IntPtr.Zero);return a.ToArray(); }
  public static int Id(IntPtr h) { return GetDlgCtrlID(h); }
  public static void Verify(IntPtr d, IntPtr h, uint pid, string cls) {
    if (h==IntPtr.Zero || !IsChild(d,h) || Pid(h)!=pid || Class(h)!=cls || !IsWindowEnabled(h)) throw new Exception("Native control identity mismatch");
  }
  public static string SetFilename(IntPtr d, IntPtr h, uint pid, string value) {
    Verify(d,h,pid,"Edit"); UIntPtr r;
    if(Write(h,12,UIntPtr.Zero,value,2,5000,out r)==IntPtr.Zero || r==UIntPtr.Zero) throw new Exception("WM_SETTEXT failed");
    var s=new StringBuilder(32768);
    if(Read(h,13,(UIntPtr)(uint)s.Capacity,s,2,5000,out r)==IntPtr.Zero || s.ToString()!=value) throw new Exception("Native filename readback mismatch");
    return s.ToString();
  }
  public static void Click(IntPtr d, IntPtr h, uint pid) {
    Verify(d,h,pid,"Button");
    if(!PostMessage(h,245,IntPtr.Zero,IntPtr.Zero)) throw new Exception("Native BM_CLICK failed");
  }
}
'@
$root = [System.Windows.Automation.AutomationElement]::RootElement
$deadline = [DateTime]::UtcNow.AddSeconds(30)
$dialog = $null
while ([DateTime]::UtcNow -lt $deadline) {
  $windows = $root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition)
  foreach ($window in $windows) {
    if ($window.Current.ProcessId -ne $OwnerPid) { continue }
    # Windows exposes the owned modal underneath its application window in
    # UIAutomation, not necessarily as a desktop RootElement child.
    $candidates = @($window) + @($window.FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition))
    $matches = @($candidates | Where-Object { $_.Current.ProcessId -eq $OwnerPid -and $_.Current.ClassName -eq '#32770' -and $_.Current.Name -eq $title })
    if ($matches.Count -gt 1) { throw 'Verified native dialog identity is ambiguous' }
    if ($matches.Count -eq 1) { $dialog = $matches[0]; break }
  }
  if ($null -ne $dialog) { break }
  Start-Sleep -Milliseconds 100
}
if ($null -eq $dialog) {
  # Diagnostic evidence only: do not relax owner/class/title checks or operate
  # on any unexpected window. This is a disposable synthetic hosted desktop.
  $diagnostic = @{ expectedPid=$OwnerPid; expectedTitle=$title; sessionId=(Get-Process -Id $OwnerPid).SessionId }
  try {
    $diagnostic.windows = @($root.FindAll([System.Windows.Automation.TreeScope]::Children, [System.Windows.Automation.Condition]::TrueCondition) | ForEach-Object {
      @{ pid=$_.Current.ProcessId; title=$_.Current.Name; class=$_.Current.ClassName; offscreen=$_.Current.IsOffscreen }
    })
    $bounds = [System.Windows.Forms.SystemInformation]::VirtualScreen
    $bitmap = [System.Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
    $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
    try { $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bitmap.Size); $bitmap.Save($Screenshot, [System.Drawing.Imaging.ImageFormat]::Png) }
    finally { $graphics.Dispose(); $bitmap.Dispose() }
  } catch { $diagnostic.captureError = $_.Exception.Message }
  $diagnostic | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 -LiteralPath ($Screenshot + '.json')
  throw 'Real Windows Save dialog did not appear for the verified process'
}
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
@($controls | ForEach-Object { @{ name=$_.Current.Name; id=$_.Current.AutomationId; type=$_.Current.ControlType.ProgrammaticName } }) | ConvertTo-Json -Depth 4 | Set-Content -Encoding UTF8 -LiteralPath ($Screenshot + '.controls.json')
$hwnd = [IntPtr]$dialog.Current.NativeWindowHandle
if ($hwnd -eq [IntPtr]::Zero -or [ExportNative]::Pid($hwnd) -ne $OwnerPid -or [ExportNative]::Class($hwnd) -ne '#32770' -or [ExportNative]::Title($hwnd) -ne $title) { throw 'Native dialog HWND identity mismatch' }
$children = [ExportNative]::Children($hwnd)
@($children | ForEach-Object { @{ hwnd=$_.ToInt64(); pid=[ExportNative]::Pid($_); class=[ExportNative]::Class($_); id=[ExportNative]::Id($_) } }) | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath ($Screenshot + '.hwnds.json')
$cancelHandles = @($children | Where-Object { [ExportNative]::Id($_) -eq 2 -and [ExportNative]::Class($_) -eq 'Button' -and [ExportNative]::Pid($_) -eq $OwnerPid })
$saveHandles = @($children | Where-Object { [ExportNative]::Id($_) -eq 1 -and [ExportNative]::Class($_) -eq 'Button' -and [ExportNative]::Pid($_) -eq $OwnerPid })
if ($cancelHandles.Count -ne 1 -or $saveHandles.Count -ne 1) { throw 'Native HWND buttons are not unique' }
if ($Choice -eq 'save') {
  if (Test-Path -LiteralPath $Destination) { throw 'Native save destination must be fresh' }
  # Stay within the exact UIA filename host, never an address/search field.
  $hosts = @($controls | Where-Object { $_.Current.AutomationId -eq 'FileNameControlHost' })
  if ($hosts.Count -ne 1) { throw 'Filename host is not unique' }
  $nested = $hosts[0].FindAll([System.Windows.Automation.TreeScope]::Descendants, [System.Windows.Automation.Condition]::TrueCondition)
  $handles = @($nested | ForEach-Object { [IntPtr]$_.Current.NativeWindowHandle } | Where-Object { $_ -ne [IntPtr]::Zero -and [ExportNative]::IsChild($hwnd,$_) })
  $edits = @(@($handles) + @($handles | ForEach-Object { [ExportNative]::Children($_) }) | Where-Object { [ExportNative]::Class($_) -eq 'Edit' -and [ExportNative]::Pid($_) -eq $OwnerPid } | Sort-Object -Unique)
  if ($edits.Count -ne 1) { throw 'Native filename Edit HWND is not unique' }
  $readback = [ExportNative]::SetFilename($hwnd,$edits[0],$OwnerPid,$Destination)
  @{ expected=$Destination; readback=$readback; readbackVerified=$true; dialogHwnd=$hwnd.ToInt64(); editHwnd=$edits[0].ToInt64(); saveHwnd=$saveHandles[0].ToInt64(); editClass=[ExportNative]::Class($edits[0]); ownerPid=$OwnerPid } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath ($Screenshot + '.input.json')
  $bitmap = [System.Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  try { $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bitmap.Size); $bitmap.Save(($Screenshot + '.input.png'), [System.Drawing.Imaging.ImageFormat]::Png) }
  finally { $graphics.Dispose(); $bitmap.Dispose() }
  [ExportNative]::Click($hwnd,$saveHandles[0],$OwnerPid)
} else { [ExportNative]::Click($hwnd,$cancelHandles[0],$OwnerPid) }
  Start-Sleep -Milliseconds 500
  $foreground = [ExportNative]::GetForegroundWindow()
  $postClick = @{ dialogHwnd=$hwnd.ToInt64(); exists=[ExportNative]::IsWindow($hwnd); visible=[ExportNative]::IsWindowVisible($hwnd); foregroundHwnd=$foreground.ToInt64(); foregroundPid=[ExportNative]::Pid($foreground); foregroundClass=[ExportNative]::Class($foreground); foregroundTitle=[ExportNative]::Title($foreground) }
  $postClick | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath ($Screenshot + '.post-click.json')
  $bitmap = [System.Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  try { $graphics.CopyFromScreen($bounds.Left, $bounds.Top, 0, 0, $bitmap.Size); $bitmap.Save(($Screenshot + '.post-click.png'), [System.Drawing.Imaging.ImageFormat]::Png) }
  finally { $graphics.Dispose(); $bitmap.Dispose() }
  @{ postClick=$postClick; choice=$Choice; ownerPid=$OwnerPid; title=$title; nativeClass='#32770'; automation='UIAutomation identity + verified Win32 HWND WM_SETTEXT/readback/BM_CLICK'; dialogReplaced=$false } | ConvertTo-Json -Compress
} catch {
  if ($cancel.Count -eq 1) { try { $cancel[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() } catch {} }
  else { try { $dialog.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close() } catch {} }
  throw
}
