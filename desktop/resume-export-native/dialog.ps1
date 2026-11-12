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
# Use real pointer/keyboard input so the shell dialog commits its filename state.
Add-Type @'
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;
public static class ExportNative {
  public delegate bool EnumProc(IntPtr hwnd, IntPtr data);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X,Y; public POINT(int x,int y){X=x;Y=y;} }
  [StructLayout(LayoutKind.Sequential)] public struct GUIINFO { public uint size, flags; public IntPtr active, focus, capture, menuOwner, moveSize, caret; public RECT caretRect; }
  [StructLayout(LayoutKind.Sequential)] public struct KEY { public ushort vk, scan; public uint flags, time; public UIntPtr extra; }
  [StructLayout(LayoutKind.Sequential)] public struct MOUSE { public int x,y; public uint data, flags, time; public UIntPtr extra; }
  [StructLayout(LayoutKind.Explicit)] public struct UNION { [FieldOffset(0)] public KEY key; [FieldOffset(0)] public MOUSE mouse; }
  [StructLayout(LayoutKind.Sequential)] public struct INPUT { public uint type; public UNION value; }
  [DllImport("user32.dll")] static extern bool GetWindowRect(IntPtr h,out RECT r);
  [DllImport("user32.dll")] static extern IntPtr WindowFromPoint(POINT p);
  [DllImport("user32.dll")] static extern bool SetCursorPos(int x,int y);
  [DllImport("user32.dll")] static extern bool GetGUIThreadInfo(uint id,ref GUIINFO info);
  [DllImport("user32.dll")] static extern uint SendInput(uint n,INPUT[] inputs,int size);
  static INPUT Key(ushort vk, ushort scan, uint flags) { return new INPUT { type=1, value=new UNION { key=new KEY { vk=vk, scan=scan, flags=flags } } }; }
  static void Send(INPUT[] inputs) { if(SendInput((uint)inputs.Length,inputs,Marshal.SizeOf(typeof(INPUT)))!=inputs.Length) throw new Exception("Native SendInput incomplete"); }
  public static IntPtr Focus(IntPtr d) { uint pid; uint thread=GetWindowThreadProcessId(d,out pid); var info=new GUIINFO { size=(uint)Marshal.SizeOf(typeof(GUIINFO)) }; if(!GetGUIThreadInfo(thread,ref info)) throw new Exception("Native focus unavailable"); return info.focus; }
  static void OwnedPointer(IntPtr d, IntPtr h, uint pid, string cls) {
    Verify(d,h,pid,cls);
    if(GetForegroundWindow()!=d) throw new Exception("Verified dialog is not foreground");
    RECT r; if(!GetWindowRect(h,out r) || r.Right<=r.Left || r.Bottom<=r.Top) throw new Exception("Native control bounds unavailable");
    var p=new POINT(r.Left+(r.Right-r.Left)/2,r.Top+(r.Bottom-r.Top)/2);
    if(WindowFromPoint(p)!=h || !SetCursorPos(p.X,p.Y) || WindowFromPoint(p)!=h) throw new Exception("Native control click is occluded");
    try { Send(new INPUT[] { new INPUT { type=0,value=new UNION { mouse=new MOUSE { flags=2 } } }, new INPUT { type=0,value=new UNION { mouse=new MOUSE { flags=4 } } } }); }
    finally { SendInput(1,new INPUT[] { new INPUT { type=0,value=new UNION { mouse=new MOUSE { flags=4 } } } },Marshal.SizeOf(typeof(INPUT))); }
  }
  static void CheckFocus(IntPtr d,IntPtr h,uint pid) { Verify(d,h,pid,"Edit"); if(GetForegroundWindow()!=d || Focus(d)!=h) throw new Exception("Native filename focus changed"); }

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
  [DllImport("user32.dll", CharSet=CharSet.Unicode, EntryPoint="SendMessageTimeoutW")] static extern IntPtr Read(IntPtr h, uint m, UIntPtr w, StringBuilder s, uint flags, uint ms, out UIntPtr result);
  public static string Class(IntPtr h) { var s=new StringBuilder(256); GetClassName(h,s,s.Capacity); return s.ToString(); }
  public static string Title(IntPtr h) { var s=new StringBuilder(1024); GetWindowText(h,s,s.Capacity); return s.ToString(); }
  public static uint Pid(IntPtr h) { uint p; GetWindowThreadProcessId(h,out p); return p; }
  public static IntPtr[] Children(IntPtr h) { var a=new List<IntPtr>(); EnumChildWindows(h,(c,d)=>{a.Add(c);return true;},IntPtr.Zero);return a.ToArray(); }
  public static int Id(IntPtr h) { return GetDlgCtrlID(h); }
  public static void Verify(IntPtr d, IntPtr h, uint pid, string cls) {
    if (h==IntPtr.Zero || !IsChild(d,h) || Pid(h)!=pid || Class(h)!=cls || !IsWindowEnabled(h)) throw new Exception("Native control identity mismatch");
  }
  public static string SetFilename(IntPtr d, IntPtr h, uint pid, string value) {
    OwnedPointer(d,h,pid,"Edit");
    var deadline=DateTime.UtcNow.AddSeconds(2);
    while(Focus(d)!=h && DateTime.UtcNow<deadline) System.Threading.Thread.Sleep(25);
    CheckFocus(d,h,pid);
    // Standard Ctrl+A then Unicode key events, not WM_SETTEXT or clipboard.
    try { Send(new INPUT[] { Key(17,0,0),Key(65,0,0),Key(65,0,2),Key(17,0,2) }); }
    finally { SendInput(2,new INPUT[] { Key(65,0,2),Key(17,0,2) },Marshal.SizeOf(typeof(INPUT))); }
    foreach(char c in value) { CheckFocus(d,h,pid); try { Send(new INPUT[] { Key(0,c,4),Key(0,c,6) }); } finally { SendInput(1,new INPUT[] { Key(0,c,6) },Marshal.SizeOf(typeof(INPUT))); } }
    deadline=DateTime.UtcNow.AddSeconds(2);
    do {
      CheckFocus(d,h,pid); UIntPtr r; var s=new StringBuilder(32768);
      if(Read(h,13,(UIntPtr)(uint)s.Capacity,s,2,500,out r)==IntPtr.Zero) throw new Exception("Native filename readback unavailable");
      if(s.ToString()==value) return s.ToString();
      System.Threading.Thread.Sleep(25);
    } while(DateTime.UtcNow<deadline);
    throw new Exception("Native filename readback mismatch");
  }
  public static void Click(IntPtr d, IntPtr h, uint pid) {
    OwnedPointer(d,h,pid,"Button");
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
  @{ expected=$Destination; readback=$readback; readbackVerified=$true; inputMethod='verified pointer focus + SendInput Ctrl+A/Unicode'; focusedHwnd=[ExportNative]::Focus($hwnd).ToInt64(); dialogHwnd=$hwnd.ToInt64(); editHwnd=$edits[0].ToInt64(); saveHwnd=$saveHandles[0].ToInt64(); editClass=[ExportNative]::Class($edits[0]); ownerPid=$OwnerPid } | ConvertTo-Json | Set-Content -Encoding UTF8 -LiteralPath ($Screenshot + '.input.json')
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
  @{ postClick=$postClick; choice=$Choice; ownerPid=$OwnerPid; title=$title; nativeClass='#32770'; automation='UIAutomation/Win32 identity + actual pointer and SendInput keyboard'; dialogReplaced=$false } | ConvertTo-Json -Compress
} catch {
  if ($cancel.Count -eq 1) { try { $cancel[0].GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern).Invoke() } catch {} }
  else { try { $dialog.GetCurrentPattern([System.Windows.Automation.WindowPattern]::Pattern).Close() } catch {} }
  throw
}
