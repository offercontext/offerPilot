param([Parameter(Mandatory=$true)][ValidateSet('conditions','observe-and-click')][string]$Mode)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName UIAutomationClient,UIAutomationTypes,System.Windows.Forms,System.Drawing
Add-Type @'
using System;using System.Runtime.InteropServices;
public static class ReminderNative {
 [DllImport("shell32.dll")] public static extern int SHQueryUserNotificationState(out int state);
 [DllImport("user32.dll")] public static extern bool SetCursorPos(int x,int y);
 [DllImport("user32.dll")] public static extern void mouse_event(uint flags,uint x,uint y,uint data,UIntPtr extra);
}
'@
if($Mode -eq 'conditions'){
 $state=0;$hr=[ReminderNative]::SHQueryUserNotificationState([ref]$state)
 $result=@{interactive=[Environment]::UserInteractive;sessionId=(Get-Process -Id $PID).SessionId;notificationStateHRESULT=$hr;notificationState=$state;focusAssist='not inferred from registry';permissionChanged=$false;securityChanged=$false}
 try{$result.toastEnabled=(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications' -Name ToastEnabled -ErrorAction Stop).ToastEnabled}catch{$result.toastEnabled='not explicitly set or unreadable'}
 try{$result.appEnabled=(Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\org.offerpilot.desktop' -Name Enabled -ErrorAction Stop).Enabled}catch{$result.appEnabled='not explicitly set or unreadable'}
 $result.shellExperienceHostCount=@(Get-Process -Name ShellExperienceHost -ErrorAction SilentlyContinue).Count
 $result | ConvertTo-Json -Compress;exit 0
}
$result=@{visible=$false;pointerClick=$false;boundedSeconds=85;matchedShellHost=$false}
[IO.File]::WriteAllText($env:REMINDER_NATIVE_READY,'ready')
$clock=[Diagnostics.Stopwatch]::StartNew()
$body='你有一条提醒。打开 OfferPilot 查看。'
while($clock.Elapsed.TotalSeconds -lt 85){
 $windows=[System.Windows.Automation.AutomationElement]::RootElement.FindAll([System.Windows.Automation.TreeScope]::Children,[System.Windows.Automation.Condition]::TrueCondition)
 foreach($window in $windows){
  try{
   $process=Get-Process -Id $window.Current.ProcessId -ErrorAction Stop
   if($process.ProcessName -notin @('ShellExperienceHost','ShellHost')){continue}
   $titleCondition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,'OfferPilot')
   if($null -eq $window.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$titleCondition)){continue}
   $condition=[System.Windows.Automation.PropertyCondition]::new([System.Windows.Automation.AutomationElement]::NameProperty,$body)
   $node=$window.FindFirst([System.Windows.Automation.TreeScope]::Descendants,$condition)
   if($null -eq $node -or $node.Current.IsOffscreen){continue}
   $rect=$node.Current.BoundingRectangle
   if($rect.Width -le 0 -or $rect.Height -le 0){continue}
   $x=[int]($rect.X+$rect.Width/2);$y=[int]($rect.Y+$rect.Height/2)
   $screen=[Windows.Forms.SystemInformation]::VirtualScreen
   if($x -lt $screen.Left -or $x -ge $screen.Right -or $y -lt $screen.Top -or $y -ge $screen.Bottom){continue}
   $result.visible=$true;$result.matchedShellHost=$true;$result.rectangle=@{x=$rect.X;y=$rect.Y;width=$rect.Width;height=$rect.Height}
   $bitmap=[Drawing.Bitmap]::new($screen.Width,$screen.Height);$graphics=[Drawing.Graphics]::FromImage($bitmap)
   try{$graphics.CopyFromScreen($screen.Left,$screen.Top,0,0,$bitmap.Size);$bitmap.Save($env:REMINDER_NATIVE_SCREENSHOT,[Drawing.Imaging.ImageFormat]::Png)}finally{$graphics.Dispose();$bitmap.Dispose()}
   if([ReminderNative]::SetCursorPos($x,$y)){[ReminderNative]::mouse_event(2,0,0,0,[UIntPtr]::Zero);[ReminderNative]::mouse_event(4,0,0,0,[UIntPtr]::Zero);$result.pointerClick=$true}
   $result | ConvertTo-Json -Depth 4 -Compress;exit 0
  }catch{$result.lastObservationError=$_.Exception.GetType().Name}
 }
 Start-Sleep -Milliseconds 250
}
$result | ConvertTo-Json -Depth 4 -Compress
