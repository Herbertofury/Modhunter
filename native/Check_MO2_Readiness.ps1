#requires -Version 5.1
<#
.SYNOPSIS
Inspect the selected MO2 installation and this package without changing MO2,
mods, profiles, saves, load order, settings, credentials or browser data.
.DESCRIPTION
A static preflight, not an actual MO2/Nexus execution test. Only -SaveReport
writes anything: a new credential-free JSON report beside this script.
#>
[CmdletBinding()]
param([string]$MO2Directory, [switch]$Json, [switch]$SaveReport, [switch]$PluginOnly)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Checks = New-Object 'Collections.Generic.List[object]'
$PluginHashes = @{}
function Add-Check([string]$Name, [bool]$Passed, [string]$Detail) {
    $Checks.Add([ordered]@{ name = $Name; passed = $Passed; detail = $Detail })
}
function Safe-Version([string]$Value) {
    if (!$Value) { return '' }
    $Clean = $Value -replace '[^a-zA-Z0-9. +_-]', ''
    return $Clean.Substring(0, [Math]::Min(80, $Clean.Length))
}
function File-Version([string]$Path) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    $File = Get-Item -LiteralPath $Path -Force
    if ($File.Length -gt 268435456 -or ($File.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return '' }
    return Safe-Version ([Diagnostics.FileVersionInfo]::GetVersionInfo($Path).ProductVersion)
}
function Is-X64([string]$Path) {
    $Stream = $null; $Reader = $null
    try {
        $Stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        $Reader = [IO.BinaryReader]::new($Stream)
        if ($Stream.Length -lt 128 -or $Reader.ReadUInt16() -ne 0x5A4D) { return $false }
        $Stream.Position = 0x3c; $Offset = $Reader.ReadUInt32()
        if ($Offset -lt 64 -or $Offset -gt 16777216 -or ($Offset + 24) -gt $Stream.Length) { return $false }
        $Stream.Position = $Offset
        return ($Reader.ReadUInt32() -eq 0x4550 -and $Reader.ReadUInt16() -eq 0x8664)
    } catch { return $false }
    finally { if ($Reader) { $Reader.Dispose() } elseif ($Stream) { $Stream.Dispose() } }
}
function Read-PluginVersion([string]$Path) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
    $File = Get-Item -LiteralPath $Path -Force
    if ($File.Length -gt 65536 -or ($File.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return '' }
    $Versions = [regex]::Matches([IO.File]::ReadAllText($Path), '(?m)^\s*VERSION\s*=\s*["'']([0-9]+\.[0-9]+\.[0-9]+)["'']\s*$')
    if ($Versions.Count -ne 1) { return '' }
    return $Versions[0].Groups[1].Value
}
function File-SHA256([string]$Path) {
    # Match the installer: Windows PowerShell may inherit a module path that
    # omits Get-FileHash when launched from another host.
    $Stream = [IO.File]::OpenRead($Path)
    try {
        $SHA256 = [Security.Cryptography.SHA256]::Create()
        try { return [BitConverter]::ToString($SHA256.ComputeHash($Stream)).Replace('-', '') }
        finally { $SHA256.Dispose() }
    } finally { $Stream.Dispose() }
}
function Get-VerifiedPackageVersion {
    $Manifest = Join-Path $PSScriptRoot 'SHA256SUMS.txt'
    if (!(Test-Path -LiteralPath $Manifest -PathType Leaf)) { return '' }
    $ManifestFile = Get-Item -LiteralPath $Manifest -Force
    if ($ManifestFile.Length -gt 4194304 -or ($ManifestFile.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return '' }
    $Expected = @{}
    foreach ($Line in [IO.File]::ReadAllLines($Manifest)) {
        if ($Line -notmatch '^([0-9a-fA-F]{64})  (.+)$') { return '' }
        $Digest = $Matches[1]
        $Relative = $Matches[2].Replace('\', '/')
        if ([IO.Path]::IsPathRooted($Relative) -or $Relative.Contains(':') -or $Relative -match '(^|/)(\.|\.\.|)(/|$)' -or $Expected.ContainsKey($Relative)) { return '' }
        $Expected[$Relative] = $Digest
    }
    $Required = @('BUILD_INFO.json',
        'MO2/mo2_update_arsenal/version.py', 'MO2/mo2_update_arsenal/qt_bridge.py', 'MO2/mo2_update_arsenal/version_evidence.py',
        'MO2/mo2_update_arsenal/bin/modhunter-cli.exe', 'MO2/mo2_update_arsenal/bin/modhunter-qt-bridge.dll')
    if ($PluginOnly) {
        $Required += @('Install_MO2_Plugin.ps1', 'Install_MO2_Plugin.bat', 'MO2/mo2_update_arsenal/__init__.py',
            'MO2/mo2_update_arsenal/plugin.py', 'MO2/mo2_update_arsenal/web/index.html')
        foreach ($Name in @('7z.exe', '7z.dll', 'License.txt', 'provenance.json')) {
            $Required += 'MO2/mo2_update_arsenal/bin/archive-tools/' + $Name
        }
        foreach ($Relative in $Required) { if (!$Expected.ContainsKey($Relative)) { return '' } }
        $Required = $Expected.Keys
    } else { $Required += @('Modhunter.exe', 'modhunter-cli.exe') }
    foreach ($Relative in $Required) {
        $Path = Join-Path $PSScriptRoot $Relative
        if (!$Expected.ContainsKey($Relative) -or !(Test-Path -LiteralPath $Path -PathType Leaf)) { return '' }
        $File = Get-Item -LiteralPath $Path -Force
        if ($File.Length -gt 268435456 -or ($File.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return '' }
        if ((File-SHA256 $Path) -ne $Expected[$Relative]) { return '' }
        if ($PluginOnly -and $Relative.StartsWith('MO2/mo2_update_arsenal/', [StringComparison]::Ordinal)) {
            $PluginHashes[$Relative.Substring('MO2/mo2_update_arsenal/'.Length)] = $Expected[$Relative]
        }
    }
    # A matching checksum alone does not make a mixed package valid. Use the
    # same build/plugin version contract as Install_MO2_Plugin.ps1, after both
    # sources have been checked against this archive's manifest.
    $BuildPath = Join-Path $PSScriptRoot 'BUILD_INFO.json'
    if ((Get-Item -LiteralPath $BuildPath).Length -gt 65536) { return '' }
    $PluginVersion = Read-PluginVersion (Join-Path $PSScriptRoot 'MO2/mo2_update_arsenal/version.py')
    try {
        $Build = [IO.File]::ReadAllText($BuildPath) | ConvertFrom-Json
        if (!$PluginVersion -or $Build.version -isnot [string] -or $Build.version -cne $PluginVersion -or
            $Build.commit -isnot [string] -or $Build.commit -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$') { return '' }
        if ($PluginOnly -and $Build.component -cne 'mo2-plugin') { return '' }
    } catch { return '' }
    return $PluginVersion
}
try {
    if (!$MO2Directory) {
        if ($Json) { throw 'JSON mode requires -MO2Directory.' }
        Add-Type -AssemblyName System.Windows.Forms
        $Picker = New-Object System.Windows.Forms.FolderBrowserDialog
        $Picker.Description = 'Select the MO2 folder containing ModOrganizer.exe (not Skyrim or the mods folder).'
        try {
            if ($Picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { exit 0 }
            $MO2Directory = $Picker.SelectedPath
        } finally { $Picker.Dispose() }
    }
    $Exe = Join-Path $MO2Directory 'ModOrganizer.exe'
    $MO2Version = File-Version $Exe
    $MO2Family = $MO2Version -match '^v?2\.5\.(2|3)(?:[^0-9]|$)'
    $ExactTarget = $MO2Version -match '^v?2\.5\.3(?:[ ._-]*(?:b|beta)[ ._-]*12|\.12)(?:[^0-9]|$)'
    Add-Check 'mo2_family' $MO2Family ('Observed product version: ' + $MO2Version + '. Beta suffix may require Help > About confirmation.')
    $X64 = (Test-Path -LiteralPath $Exe -PathType Leaf) -and (Is-X64 $Exe)
    Add-Check 'mo2_x64' $X64 'Requires the 64-bit Windows MO2 executable.'
    $QtFiles = @(@('Qt6Core.dll', 'dlls/Qt6Core.dll') | Where-Object { Test-Path -LiteralPath (Join-Path $MO2Directory $_) -PathType Leaf })
    $QtVersions = @($QtFiles | ForEach-Object { File-Version (Join-Path $MO2Directory $_) })
    $QtGood = $false
    if (@($QtFiles).Count -eq 1 -and $QtVersions[0] -match '^(6\.(7|11)\.[0-9]+)(?:\.0)?$') {
        $QtGood = ([version]$Matches[1] -ge [version]'6.7.1') -and (Is-X64 (Join-Path $MO2Directory $QtFiles[0]))
    }
    Add-Check 'qt_runtime_files' $QtGood ('Observed Qt: ' + ($QtVersions -join ', ') + '. Exactly one root or dlls/ Qt6Core.dll is required for an unambiguous static check.')
    $PackageVersion = Get-VerifiedPackageVersion
    $PackageGood = ![string]::IsNullOrEmpty($PackageVersion)
    $PackageDetail = if ($PluginOnly) { 'Verifies every plugin package manifest member, including the supported installer, native helpers and archive tools.' } else { 'Checks packaged desktop, diagnostics, version logic and native Qt binding against SHA256SUMS.txt and verifies the release version.' }
    Add-Check 'package_checksums' $PackageGood $PackageDetail
    $Installed = Join-Path $MO2Directory 'plugins/mo2_update_arsenal/version.py'
    $InstalledVersion = Read-PluginVersion $Installed
    $InstalledGood = $PackageGood -and ($InstalledVersion -eq $PackageVersion)
    if ($PluginOnly -and $InstalledGood) {
        $InstalledRoot = Join-Path $MO2Directory 'plugins/mo2_update_arsenal'
        foreach ($Name in $PluginHashes.Keys) {
            $Member = Join-Path $InstalledRoot $Name
            if (!(Test-Path -LiteralPath $Member -PathType Leaf) -or
                ((Get-Item -LiteralPath $Member -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -or
                (File-SHA256 $Member) -ne $PluginHashes[$Name]) { $InstalledGood = $false; break }
        }
    }
    $ExpectedVersion = if ($PackageGood) { $PackageVersion } else { 'unverified package' }
    Add-Check 'plugin_installed' $InstalledGood ('Installed plugin version: ' + $InstalledVersion + '; expected: ' + $ExpectedVersion + '. Use Install_MO2_Plugin.bat with MO2 closed when missing or from another release.')
    $Ready = $MO2Family -and $X64 -and $QtGood -and $PackageGood
    $Report = [ordered]@{
        schemaVersion = 1; requestedTarget = 'MO2 2.5.3b12 / Skyrim Special Edition';
        observedMO2Version = $MO2Version; exactBetaSuffixObserved = [bool]$ExactTarget;
        observedQtVersions = $QtVersions; modhunterVersion = $PackageVersion;
        readyToInstall = [bool]$Ready; readyForConnectionTest = [bool]($Ready -and $InstalledGood);
        scope = 'Static file/version/package checks only. The plugin separately verifies the actually loaded Qt runtime and a live owned signal. No game, mod, save, load order or sign-in data was read or modified.';
        checks = @($Checks.ToArray());
    }
    $ReportJson = $Report | ConvertTo-Json -Depth 8
    if ($SaveReport) {
        $Name = 'MO2-readiness-' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8) + '.json'
        $ReportPath = Join-Path $PSScriptRoot $Name
        $Bytes = [Text.UTF8Encoding]::new($false).GetBytes($ReportJson + "`n")
        $Handle = [IO.File]::Open($ReportPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
        try { $Handle.Write($Bytes, 0, $Bytes.Length) } finally { $Handle.Dispose() }
        if (!$Json) { Write-Host ('Saved diagnostic report: ' + $Name) }
    }
    if ($Json) { Write-Output $ReportJson }
    else {
        Write-Host ('Modhunter ' + $ExpectedVersion + ' - MO2 readiness check')
        foreach ($Check in $Checks) { $Prefix = if ($Check.passed) { 'PASS' } else { 'REVIEW' }; Write-Host ($Prefix + ' ' + $Check.name + ': ' + $Check.detail) }
        Write-Host 'After installation: start MO2 and let the profile load, then launch Modhunter. The bridge starts automatically; Find MO2 reconnects on demand. Check the active Skyrim profile before reviewing updates.'
        Write-Host 'Exact beta-12 execution and your real account/mod setup still require your connection test. No mod updates were executed.'
    }
    if (!$Ready) { exit 2 }
} catch {
    # No environment, paths, tokens or file content are exported on errors.
    if ($Json) { Write-Output '{"schemaVersion":1,"readyToInstall":false,"error":"Preflight could not read the selected installation. No MO2 or game files were changed."}' }
    else { Write-Error 'Preflight could not read the selected installation. Check the selected MO2 folder and package permissions. No MO2 or game files were changed.' }
    exit 1
}
