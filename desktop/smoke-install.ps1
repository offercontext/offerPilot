param(
    [Parameter(Mandatory=$true)][string]$InstallerDirectory
)

# CI-only smoke of this run's unsigned NSIS payload. Never launch Electron or
# borrow an existing user profile. This does not prove ordinary-user/UAC/UI UX.
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or
    $env:RUNNER_ENVIRONMENT -ne 'github-hosted') {
    throw 'This installation smoke requires a disposable Windows Actions runner'
}
if (-not $env:RUNNER_TEMP -or -not (Test-Path -LiteralPath $env:RUNNER_TEMP -PathType Container)) {
    throw 'Runner temporary directory is unavailable'
}
$package = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'package.json') -Raw | ConvertFrom-Json
if ($package.build.nsis.runAfterFinish -ne $false -or $package.build.nsis.deleteAppDataOnUninstall -ne $false) {
    throw 'Smoke requires no post-install launch and preserved application data'
}

$identityJson = & node (Join-Path $PSScriptRoot 'nsis-smoke-identity.cjs')
if ($LASTEXITCODE -ne 0) { throw 'Cannot verify the locked installer registry identity' }
$identity = $identityJson | ConvertFrom-Json

function Get-ExistingRegistration {
    foreach ($hive in @([Microsoft.Win32.RegistryHive]::CurrentUser, [Microsoft.Win32.RegistryHive]::LocalMachine)) {
        foreach ($view in @([Microsoft.Win32.RegistryView]::Registry32, [Microsoft.Win32.RegistryView]::Registry64)) {
            $base = [Microsoft.Win32.RegistryKey]::OpenBaseKey($hive, $view)
            try {
                foreach ($name in @($identity.guid, $identity.appId, $identity.productName)) {
                    $key = $base.OpenSubKey(('Software\' + $name), $false)
                    if ($null -ne $key) { $key.Dispose(); 'existing-install-key' }
                }
                $uninstall = $base.OpenSubKey('Software\Microsoft\Windows\CurrentVersion\Uninstall', $false)
                if ($null -ne $uninstall) {
                    try {
                        foreach ($name in $uninstall.GetSubKeyNames()) {
                            $key = $uninstall.OpenSubKey($name, $false)
                            if ($null -eq $key) { throw 'Uninstall registration changed during preflight' }
                            try {
                                if ($name.Trim('{}') -eq $identity.guid -or $name -eq $identity.appId -or
                                    $key.GetValue('DisplayName') -eq $identity.productName) { 'existing-uninstall-key' }
                            } finally { $key.Dispose() }
                        }
                    } finally { $uninstall.Dispose() }
                }
            } finally { $base.Dispose() }
        }
    }
}

# NSIS can use registration from an earlier install even when /D is new. Refuse
# before executing anything rather than upgrade/uninstall a pre-existing copy.
if (@(Get-ExistingRegistration).Count -ne 0) { throw 'Existing OfferPilot installation must not be touched' }
$ExistingPaths = @(
    (Join-Path $env:APPDATA 'OfferPilot Desktop'),
    (Join-Path $env:LOCALAPPDATA 'Programs\OfferPilot Desktop'),
    (Join-Path $env:ProgramFiles 'OfferPilot Desktop'),
    (Join-Path ${env:ProgramFiles(x86)} 'OfferPilot Desktop'),
    (Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'OfferPilot Desktop.lnk'),
    (Join-Path ([Environment]::GetFolderPath('Programs')) 'OfferPilot Desktop.lnk'),
    (Join-Path ([Environment]::GetFolderPath('CommonDesktopDirectory')) 'OfferPilot Desktop.lnk'),
    (Join-Path ([Environment]::GetFolderPath('CommonPrograms')) 'OfferPilot Desktop.lnk')
)
foreach ($existing in $ExistingPaths) {
    if (Test-Path -LiteralPath $existing) { throw 'Existing OfferPilot files or profile must not be touched' }
}
$installers = @(Get-ChildItem -LiteralPath $InstallerDirectory -Filter '*-setup.exe' -File)
if ($installers.Count -ne 1) { throw 'Expected exactly one installer built by the current job' }
if ($installers[0].Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Installer must not be a filesystem link' }
$installerSha = (Get-FileHash -Algorithm SHA256 -LiteralPath $installers[0].FullName).Hash.ToLowerInvariant()
Write-Host ('Installer SHA256: ' + $installerSha)
Write-Host ('Source SHA: ' + $env:GITHUB_SHA)

$owner = [Guid]::NewGuid().ToString('N')
$TempRoot = Join-Path $env:RUNNER_TEMP ('offerpilot-installed-smoke-' + $owner)
$InstallDir = Join-Path $TempRoot 'fresh installation with spaces'
$ProfileRoot = Join-Path $TempRoot 'synthetic profiles'
$Marker = Join-Path $TempRoot '.smoke-owner'
if (Test-Path -LiteralPath $TempRoot) { throw 'Smoke destination is not fresh' }
New-Item -ItemType Directory -Path $TempRoot, $ProfileRoot | Out-Null
Set-Content -LiteralPath $Marker -Value $owner -Encoding ascii
$previousTemp = $env:TEMP
$previousTmp = $env:TMP
$primaryFailure = $null
$cleanupFailure = $null
$OwnedNsisUnsettled = $false

function Invoke-OwnedInstaller([string]$Executable, [string]$Arguments) {
    $info = [Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $Executable
    $info.Arguments = $Arguments
    $info.UseShellExecute = $false
    $process = [Diagnostics.Process]::Start($info)
    $script:OwnedNsisUnsettled = $true
    try {
        if (-not $process.WaitForExit(180000)) {
            $process.Kill()
            if (-not $process.WaitForExit(15000)) {
                throw 'Owned NSIS process did not exit after bounded cleanup; cleanup incomplete'
            }
            $script:OwnedNsisUnsettled = $false
            throw 'Owned NSIS process timed out'
        }
        $script:OwnedNsisUnsettled = $false
        if ($process.ExitCode -ne 0) { throw 'Owned NSIS process failed' }
    } finally { $process.Dispose() }
}

try {
    # /D must be last and unquoted, including when the directory contains spaces.
    Invoke-OwnedInstaller $installers[0].FullName ('/S /currentuser /D=' + $InstallDir)
    $backend = Join-Path $InstallDir 'resources\backend\offerpilot-backend.exe'
    $static = Join-Path $InstallDir 'resources\web'
    $electron = Join-Path $InstallDir 'OfferPilot Desktop.exe'
    foreach ($file in @($backend, $electron, (Join-Path $static 'index.html'))) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw 'Installed payload is incomplete' }
    }
    if (@(Get-ChildItem -LiteralPath $TempRoot -Recurse -Force -Attributes ReparsePoint).Count -ne 0) {
        throw 'Installed smoke tree must not contain links'
    }
    # The existing smoke creates independent fresh data roots, starts only the
    # installed backend, and verifies authentication, persistence and shutdown.
    $env:TEMP = $ProfileRoot
    $env:TMP = $ProfileRoot
    & python (Join-Path $PSScriptRoot 'smoke-backend.py') --backend $backend --static-dir $static
    if ($LASTEXITCODE -ne 0) { throw 'Installed backend smoke failed' }
    if (Test-Path -LiteralPath (Join-Path $env:APPDATA 'OfferPilot Desktop')) {
        throw 'Installer unexpectedly created a real Electron profile'
    }
} catch { $primaryFailure = $_ }
finally {
    $env:TEMP = $previousTemp
    $env:TMP = $previousTmp
    try {
        if ($OwnedNsisUnsettled) { throw 'Owned NSIS process remains unsettled; refusing concurrent uninstall' }
        if ((Get-Content -LiteralPath $Marker -Raw).Trim() -cne $owner) { throw 'Smoke ownership marker changed' }
        if (@(Get-ChildItem -LiteralPath $TempRoot -Recurse -Force -Attributes ReparsePoint).Count -ne 0) {
            throw 'Refusing cleanup through a filesystem link'
        }
        $uninstaller = Join-Path $InstallDir 'Uninstall OfferPilot Desktop.exe'
        if (Test-Path -LiteralPath $uninstaller -PathType Leaf) {
            # NSIS _?= prevents the uninstaller from copying/spawning itself in
            # another temp directory, so the owned process handle is meaningful.
            Invoke-OwnedInstaller $uninstaller ('/S /currentuser _?=' + $InstallDir)
            foreach ($relative in @('OfferPilot Desktop.exe', 'resources\backend\offerpilot-backend.exe', 'resources\web\index.html')) {
                if (Test-Path -LiteralPath (Join-Path $InstallDir $relative)) { throw 'Uninstall left installed payload behind' }
            }
        } elseif (Test-Path -LiteralPath $InstallDir) {
            throw 'Incomplete installation has no owned uninstaller'
        }
        if (@(Get-ExistingRegistration).Count -ne 0) { throw 'Uninstall left product registration behind' }
        foreach ($existing in $ExistingPaths) {
            if (Test-Path -LiteralPath $existing) {
                throw 'Uninstall left product files, profile or shortcut behind; no non-owned path was removed'
            }
        }
        # NSIS _?= can leave its own executable; remove only this freshly owned,
        # link-free root after checking that uninstall removed the payload.
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    } catch { $cleanupFailure = $_ }
}
if ($primaryFailure) {
    if ($cleanupFailure) { Write-Warning ('Cleanup also failed: ' + $cleanupFailure.Exception.Message) }
    throw $primaryFailure
}
if ($cleanupFailure) { throw $cleanupFailure }
Write-Host 'Current NSIS silent install, installed-backend smoke and uninstall passed. Electron UI/UAC/SmartScreen are not covered.'
