param([Parameter(Mandatory=$true)][long]$WindowHandle, [Parameter(Mandatory=$true)][uint32]$OwnerProcessId,
  [ValidateSet('Drag','Capture')][string]$Mode = 'Drag', [long]$OwnerWindowHandle = 0,
  [double]$XRatio = 0.5, [double]$YRatio = 0.5)
$ErrorActionPreference = 'Stop'
if ($XRatio -lt 0.1 -or $XRatio -gt 0.9 -or $YRatio -lt 0.1 -or $YRatio -gt 0.9) { throw 'Unsafe owned-window click point' }
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class HaruOwnedMouse {
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int Left, Top, Right, Bottom; }
  [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; public POINT(int x, int y) { X=x; Y=y; } }
  [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint p);
  [DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
  [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern IntPtr GetDC(IntPtr h);
  [DllImport("user32.dll")] public static extern int ReleaseDC(IntPtr h, IntPtr dc);
  [DllImport("gdi32.dll", SetLastError=true)] public static extern bool BitBlt(IntPtr target, int x, int y, int width, int height, IntPtr source, int sourceX, int sourceY, uint operation);
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern IntPtr WindowFromPoint(POINT p);
  [DllImport("user32.dll")] public static extern IntPtr GetAncestor(IntPtr h, uint flags);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, uint x, uint y, uint d, UIntPtr e);
}
'@
$handle = [IntPtr]$WindowHandle
$actualPid = [uint32]0
[void][HaruOwnedMouse]::GetWindowThreadProcessId($handle, [ref]$actualPid)
if ($actualPid -ne $OwnerProcessId) { throw 'Window does not belong to the launched Electron process' }
$box = New-Object HaruOwnedMouse+RECT
if (-not [HaruOwnedMouse]::GetWindowRect($handle, [ref]$box)) { throw 'Owned window rectangle unavailable' }
if ($Mode -eq 'Capture') {
  $ownerHandle = [IntPtr]$OwnerWindowHandle
  $backdropPid = [uint32]0
  [void][HaruOwnedMouse]::GetWindowThreadProcessId($ownerHandle, [ref]$backdropPid)
  if ($backdropPid -ne $OwnerProcessId -or $ownerHandle -eq $handle) { throw 'Backdrop must be the other owned Electron window' }
  foreach ($ownedHandle in @($handle, $ownerHandle)) {
    if (-not [HaruOwnedMouse]::IsWindowVisible($ownedHandle) -or [HaruOwnedMouse]::IsIconic($ownedHandle)) { throw 'Both capture windows must be visible and not minimized' }
  }
  $backdrop = New-Object HaruOwnedMouse+RECT
  if (-not [HaruOwnedMouse]::GetWindowRect($ownerHandle, [ref]$backdrop)) { throw 'Owned backdrop rectangle unavailable' }
  if ($box.Left -lt $backdrop.Left -or $box.Top -lt $backdrop.Top -or $box.Right -gt $backdrop.Right -or $box.Bottom -gt $backdrop.Bottom) { throw 'Haru capture must be entirely over the owned AppShell backdrop' }
  $captureWidth = $box.Right - $box.Left
  $captureHeight = $box.Bottom - $box.Top
  if ($captureWidth -lt 1 -or $captureWidth -gt 1040 -or $captureHeight -lt 1 -or $captureHeight -gt 1640) { throw 'Unexpected capture bounds' }
  # Probe corners, centre, and intervening points. Transparent pixels may hit
  # the real owner backdrop; a different process/window aborts the capture.
  $haruHitCount = 0
  foreach ($xRatioProbe in @(0.01, 0.25, 0.5, 0.75, 0.99)) {
    foreach ($yRatioProbe in @(0.01, 0.25, 0.5, 0.75, 0.99)) {
      $probeX = [int]($box.Left + ($captureWidth - 1) * $xRatioProbe)
      $probeY = [int]($box.Top + ($captureHeight - 1) * $yRatioProbe)
      $rootHandle = [HaruOwnedMouse]::GetAncestor([HaruOwnedMouse]::WindowFromPoint([HaruOwnedMouse+POINT]::new($probeX, $probeY)), 2)
      $probePid = [uint32]0
      [void][HaruOwnedMouse]::GetWindowThreadProcessId($rootHandle, [ref]$probePid)
      if ($probePid -ne $OwnerProcessId -or ($rootHandle -ne $handle -and $rootHandle -ne $ownerHandle)) { throw 'Foreign window covers the owned capture region' }
      if ($rootHandle -eq $handle) { $haruHitCount++ }
    }
  }
  if ($haruHitCount -lt 1) { throw 'Haru is fully occluded by its backdrop' }
  Add-Type -AssemblyName System.Drawing
  $bitmap = [System.Drawing.Bitmap]::new($captureWidth, $captureHeight, [System.Drawing.Imaging.PixelFormat]::Format24bppRgb)
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  $screenDc = [IntPtr]::Zero
  $imageDc = [IntPtr]::Zero
  try {
    # Raw OS compositor pixels inside Haru bounds. CAPTUREBLT includes layered
    # transparent windows. No rendering, scaling, overlay or post-processing.
    # https://learn.microsoft.com/en-us/windows/win32/api/wingdi/nf-wingdi-bitblt
    $screenDc = [HaruOwnedMouse]::GetDC([IntPtr]::Zero)
    if ($screenDc -eq [IntPtr]::Zero) { throw 'Desktop capture DC unavailable' }
    $imageDc = $graphics.GetHdc()
    $captureOperation = [uint32]0x40CC0020 # SRCCOPY | CAPTUREBLT
    if (-not [HaruOwnedMouse]::BitBlt($imageDc, 0, 0, $captureWidth, $captureHeight, $screenDc, $box.Left, $box.Top, $captureOperation)) { throw 'Owned native screen capture failed' }
    $graphics.ReleaseHdc($imageDc)
    $imageDc = [IntPtr]::Zero
    $capturePath = Join-Path $PSScriptRoot 'evidence/screens/haru-native-os-composited.png'
    $bitmap.Save($capturePath, [System.Drawing.Imaging.ImageFormat]::Png)
  } finally {
    if ($imageDc -ne [IntPtr]::Zero) { $graphics.ReleaseHdc($imageDc) }
    if ($screenDc -ne [IntPtr]::Zero) { [void][HaruOwnedMouse]::ReleaseDC([IntPtr]::Zero, $screenDc) }
    $graphics.Dispose(); $bitmap.Dispose()
  }
  @{ nativeScreenCapture = $true; method = 'Win32 GDI BitBlt SRCCOPY|CAPTUREBLT'; width = $captureWidth; height = $captureHeight;
    left = $box.Left; top = $box.Top; ownWindowSamples = 25; haruWindowSamples = $haruHitCount; file = 'screens/haru-native-os-composited.png'; imageEdited = $false } | ConvertTo-Json -Compress
  exit 0
}
$pointerX = [int]($box.Left + ($box.Right - $box.Left) * $XRatio)
$pointerY = [int]($box.Top + ($box.Bottom - $box.Top) * $YRatio)
$under = [HaruOwnedMouse]::GetAncestor([HaruOwnedMouse]::WindowFromPoint([HaruOwnedMouse+POINT]::new($pointerX, $pointerY)), 2)
if ($under -ne $handle) { throw 'Another window covers the intended owned-window point' }
if (-not [HaruOwnedMouse]::SetCursorPos($pointerX, $pointerY)) { throw 'Cursor move failed' }
try {
  [HaruOwnedMouse]::mouse_event(2, 0, 0, 0, [UIntPtr]::Zero)
  Start-Sleep -Milliseconds 100
  for ($step = 1; $step -le 8; $step++) {
    [void][HaruOwnedMouse]::SetCursorPos($pointerX - $step * 5, $pointerY - $step * 3)
    Start-Sleep -Milliseconds 60
  }
} finally { [HaruOwnedMouse]::mouse_event(4, 0, 0, 0, [UIntPtr]::Zero) }
Write-Output '{"ownedNativeDrag":true}'
