# Mod Organizer 2 to Mod Update Arsenal source bridge.
# Local-only: reads only each installed mod's meta.ini; no network calls or changes to MO2.
param([string]$ModsPath = "", [string]$OutputPath = "")
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($ModsPath)) {
    $ModsPath = Read-Host 'Paste your MO2 Mods folder path (MO2 > Folder icon > Mods folder)'
}
$ModsPath = $ModsPath.Trim().Trim('"')
if (-not (Test-Path -LiteralPath $ModsPath -PathType Container)) {
    Write-Error "MO2 Mods folder not found: $ModsPath"
    exit 1
}
function Parse-MO2INI([string]$Path) {
    $data = @{}
    $section = ''
    foreach ($line in [System.IO.File]::ReadAllLines($Path)) {
        $s = $line.Trim()
        if ($s -match '^\s*\[(.+)\]\s*$') { $section = $Matches[1].ToLowerInvariant(); continue }
        if (-not $s -or $s.StartsWith(';') -or $s.StartsWith('#')) { continue }
        $eq = $s.IndexOf('=')
        if ($eq -lt 1) { continue }
        $key = $s.Substring(0,$eq).Trim().ToLowerInvariant()
        $val = $s.Substring($eq+1).Trim().Trim('"')
        $data["$section/$key"] = $val
    }
    return ,$data
}
$results = [System.Collections.Generic.List[object]]::new()
$missing = 0
$errors = 0
$dirs = @(Get-ChildItem -LiteralPath $ModsPath -Directory -ErrorAction Stop)
foreach ($folder in $dirs) {
    $metaPath = Join-Path $folder.FullName 'meta.ini'
    if (-not (Test-Path -LiteralPath $metaPath -PathType Leaf)) { $missing++; continue }
    try {
        $ini = Parse-MO2INI $metaPath
        $id = $ini['general/modid']
        if (-not $id -or $id -notmatch '^\d+$' -or $id -eq '0') {
            $id = $ini['general/nexusid']
        }
        if (-not $id -or $id -notmatch '^\d+$' -or $id -eq '0') {
            foreach ($k in @($ini.Keys | Where-Object { $_ -match '^installedfiles/.+modid$' })) {
                if ($ini[$k] -match '^[1-9]\d*$') { $id = $ini[$k]; break }
            }
        }
        $files = @()
        foreach ($k in @($ini.Keys | Where-Object { $_ -match '^installedfiles/.+fileid$' })) {
            if ($ini[$k] -match '^[1-9]\d*$') { $files += [string]$ini[$k] }
        }
        $obj = [ordered]@{
            name = $folder.Name
            modId = $(if ($id -match '^[1-9]\d*$') { [string]$id } else { '' })
            fileIds = @($files | Select-Object -Unique)
            version = [string]$ini['general/version']
            installedArchive = [string]$ini['general/installationfile']
        }
        $results.Add($obj)
    } catch { $errors++ }
}
if ([string]::IsNullOrWhiteSpace($OutputPath)) {
    $desktop = [Environment]::GetFolderPath('DesktopDirectory')
    if (-not $desktop -or -not (Test-Path $desktop)) { $desktop = (Get-Location).Path }
    $OutputPath = Join-Path $desktop 'Skyrim_MO2_Source_Links.json'
}
$out = [ordered]@{
    format = 'MO2 Mod Update Arsenal Sources'
    version = 1
    mods = @($results)
    summary = [ordered]@{ checked = $dirs.Count; metadataMissing = $missing; unreadable = $errors; withModId = @($results | Where-Object {$_.modId}).Count }
}
$json = ConvertTo-Json -InputObject $out -Depth 8
[System.IO.File]::WriteAllText($OutputPath, $json, [System.Text.UTF8Encoding]::new($false))
Write-Host "Exported $($results.Count) MO2 mod records to $OutputPath"
Write-Host "Valid Nexus IDs: $($out.summary.withModId) | Missing metadata: $missing | Unreadable: $errors"
Write-Host 'Import this JSON using the Link MO2 button in your Skyrim Mod Update Arsenal HTML.'
