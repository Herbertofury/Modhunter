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
param([string]$MO2Directory, [switch]$Json, [switch]$SaveReport)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Checks = New-Object 'Collections.Generic.List[object]'
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
function Verify-Package {
    $Manifest = Join-Path $PSScriptRoot 'SHA256SUMS.txt'
    if (!(Test-Path -LiteralPath $Manifest -PathType Leaf)) { return $false }
    if ((Get-Item -LiteralPath $Manifest).Length -gt 4194304) { return $false }
    $Expected = @{}
    foreach ($Line in [IO.File]::ReadAllLines($Manifest)) {
        if ($Line -notmatch '^([0-9a-fA-F]{64})  (.+)$') { return $false }
        $Relative = $Matches[2].Replace('\', '/')
        if ($Expected.ContainsKey($Relative)) { return $false }
        $Expected[$Relative] = $Matches[1]
    }
    foreach ($Relative in @('Modhunter.exe', 'modhunter-cli.exe', 'BUILD_INFO.json',
        'MO2/mo2_update_arsenal/qt_bridge.py', 'MO2/mo2_update_arsenal/version_evidence.py',
        'MO2/mo2_update_arsenal/bin/modhunter-cli.exe', 'MO2/mo2_update_arsenal/bin/modhunter-qt-bridge.dll')) {
        $Path = Join-Path $PSScriptRoot $Relative
        if (!$Expected.ContainsKey($Relative) -or !(Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
        $File = Get-Item -LiteralPath $Path -Force
        if ($File.Length -gt 268435456 -or ($File.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $false }
        if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash -ne $Expected[$Relative]) { return $false }
    }
    return $true
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
    $PackageGood = Verify-Package
    Add-Check 'package_checksums' $PackageGood 'Checks the packaged desktop, diagnostic helper, version logic and native Qt binding against SHA256SUMS.txt.'
    $Installed = Join-Path $MO2Directory 'plugins/mo2_update_arsenal/version.py'
    $InstalledVersion = ''
    if ((Test-Path -LiteralPath $Installed -PathType Leaf) -and (Get-Item -LiteralPath $Installed).Length -lt 65536) {
        $Source = [IO.File]::ReadAllText($Installed)
        if ($Source -match 'VERSION\s*=\s*["'']([0-9]+\.[0-9]+\.[0-9]+)["'']') { $InstalledVersion = $Matches[1] }
    }
    $InstalledGood = $InstalledVersion -eq '0.10.1'
    Add-Check 'plugin_installed' $InstalledGood ('Installed plugin version: ' + $InstalledVersion + '. Use Install_MO2_Plugin.bat with MO2 closed when missing or older.')
    $Ready = $MO2Family -and $X64 -and $QtGood -and $PackageGood
    $Report = [ordered]@{
        schemaVersion = 1; requestedTarget = 'MO2 2.5.3b12 / Skyrim Special Edition';
        observedMO2Version = $MO2Version; exactBetaSuffixObserved = [bool]$ExactTarget;
        observedQtVersions = $QtVersions; modhunterVersion = '0.10.1';
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
        Write-Host 'Modhunter 0.10.1 - MO2 readiness check'
        foreach ($Check in $Checks) { $Prefix = if ($Check.passed) { 'PASS' } else { 'REVIEW' }; Write-Host ($Prefix + ' ' + $Check.name + ': ' + $Check.detail) }
        Write-Host 'After installation: start MO2, open Tools > Update Arsenal - Quest Board, then Find MO2 in Modhunter. Check the active Skyrim profile before scanning.'
        Write-Host 'Exact beta-12 execution and your real account/mod setup still require your connection test. No mod updates were executed.'
    }
    if (!$Ready) { exit 2 }
} catch {
    # No environment, paths, tokens or file content are exported on errors.
    if ($Json) { Write-Output '{"schemaVersion":1,"readyToInstall":false,"error":"Preflight could not read the selected installation. No MO2 or game files were changed."}' }
    else { Write-Error 'Preflight could not read the selected installation. Check the selected MO2 folder and package permissions. No MO2 or game files were changed.' }
    exit 1
}
