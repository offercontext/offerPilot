$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$phase = 'validate-paths'
$extractedCount = $null
trap {
  # Only bounded, non-sensitive phase/API values leave the child process.
  @{ extraction='Windows-native-read-only'; success=$false; phase=$phase; extractedCount=$extractedCount } | ConvertTo-Json -Compress
  exit 0
}
$exe = [IO.Path]::GetFullPath($env:UI_EXE)
$root = [IO.Path]::GetFullPath($env:UI_INSTALL_DIR).TrimEnd('\') + '\'
if (-not $exe.StartsWith($root, [StringComparison]::OrdinalIgnoreCase) -or [IO.Path]::GetFileName($exe) -ne 'OfferPilot Desktop.exe') { throw 'Unexpected installed executable' }
$shortcut = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'OfferPilot Desktop.lnk'
foreach ($file in @($exe, $shortcut)) {
  $item = Get-Item -LiteralPath $file -ErrorAction Stop
  if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'Linked or missing icon source' }
}
$phase = 'load-drawing'
Add-Type -AssemblyName System.Drawing
$phase = 'compile-native-bindings'
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class HaruNativeIcons {
  [StructLayout(LayoutKind.Sequential, CharSet=CharSet.Unicode)] public struct SHFILEINFO {
    public IntPtr hIcon; public int iIcon; public uint dwAttributes;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=260)] public string displayName;
    [MarshalAs(UnmanagedType.ByValTStr, SizeConst=80)] public string typeName;
  }
  [DllImport("shell32.dll", CharSet=CharSet.Unicode)] public static extern uint ExtractIconEx(string file, int index, out IntPtr large, out IntPtr small, uint count);
  [DllImport("shell32.dll", CharSet=CharSet.Unicode)] public static extern IntPtr SHGetFileInfo(string file, uint attrs, ref SHFILEINFO info, uint size, uint flags);
  [DllImport("user32.dll")] public static extern bool DestroyIcon(IntPtr icon);
}
'@
$screens = Join-Path $env:UI_EVIDENCE_DIR 'screens'
$phase = 'prepare-evidence-directory'
[void][IO.Directory]::CreateDirectory($screens)
$results = [Collections.Generic.List[object]]::new()
function Save-OwnedIcon([IntPtr]$Handle, [string]$Name) {
  if ($Handle -eq [IntPtr]::Zero) { throw 'Native icon unavailable' }
  $icon = [Drawing.Icon]::FromHandle($Handle)
  $bitmap = $null
  try {
    $bitmap = $icon.ToBitmap()
    if ($bitmap.Width -lt 16 -or $bitmap.Width -gt 256 -or $bitmap.Width -ne $bitmap.Height) { throw 'Unexpected native icon dimensions' }
    $file = 'haru-native-icon-' + $Name + '.png'
    $bitmap.Save((Join-Path $screens $file), [Drawing.Imaging.ImageFormat]::Png)
    $results.Add(@{ name=$Name; width=$bitmap.Width; height=$bitmap.Height; file=('screens/' + $file) })
  } finally { if ($bitmap) { $bitmap.Dispose() }; $icon.Dispose() }
}
$large = [IntPtr]::Zero; $small = [IntPtr]::Zero
try {
  $phase = 'extract-pe-group'
  $extractedCount = [HaruNativeIcons]::ExtractIconEx($exe, 0, [ref]$large, [ref]$small, 1)
  # Both one-large and one-small handles were requested: success extracts two icons.
  if ($extractedCount -ne 2) { throw 'PE icon pair extraction failed' }
  $phase = 'save-pe-small'
  Save-OwnedIcon $small 'pe-small'
  $phase = 'save-pe-normal'
  Save-OwnedIcon $large 'pe-normal'
} finally {
  if ($large -ne [IntPtr]::Zero) { [void][HaruNativeIcons]::DestroyIcon($large) }
  if ($small -ne [IntPtr]::Zero) { [void][HaruNativeIcons]::DestroyIcon($small) }
}
foreach ($target in @(@{ name='exe'; path=$exe }, @{ name='shortcut'; path=$shortcut })) {
  foreach ($size in @(@{ name='small'; flag=1 }, @{ name='normal'; flag=0 })) {
    $info = New-Object HaruNativeIcons+SHFILEINFO
    try {
      $phase = 'shell-read-' + $target.name + '-' + $size.name
      # Read actual file icon; never request a generic extension icon with USEFILEATTRIBUTES.
      $ok = [HaruNativeIcons]::SHGetFileInfo($target.path, 0, [ref]$info, [Runtime.InteropServices.Marshal]::SizeOf($info), (0x100 -bor $size.flag))
      if ($ok -eq [IntPtr]::Zero) { throw 'Shell icon extraction failed' }
      $phase = 'shell-save-' + $target.name + '-' + $size.name
      Save-OwnedIcon $info.hIcon ('shell-' + $target.name + '-' + $size.name)
    } finally { if ($info.hIcon -ne [IntPtr]::Zero) { [void][HaruNativeIcons]::DestroyIcon($info.hIcon) } }
  }
}
@{ extraction='Windows-native-read-only'; success=$true; visualReviewRequired=$true; icons=@($results.ToArray()) } | ConvertTo-Json -Depth 5 -Compress
