param([string]$MO2Directory)
$ErrorActionPreference = 'Stop'

function Get-PluginFileSHA256([string]$Path) {
    # Windows PowerShell launched from another host can inherit a module path
    # without Get-FileHash. Stream through .NET instead of relying on its module.
    $stream = [IO.File]::OpenRead($Path)
    try {
        $sha256 = [Security.Cryptography.SHA256]::Create()
        try { return [BitConverter]::ToString($sha256.ComputeHash($stream)).Replace('-', '') }
        finally { $sha256.Dispose() }
    }
    finally { $stream.Dispose() }
}

if (!$MO2Directory) {
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.FolderBrowserDialog
    $picker.Description = 'Select the MO2 installation folder containing ModOrganizer.exe'
    if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Write-Host 'Installation canceled.'; exit 0 }
    $MO2Directory = $picker.SelectedPath
}
$MO2Directory = (Resolve-Path -LiteralPath $MO2Directory).Path
if (!(Test-Path -LiteralPath (Join-Path $MO2Directory 'ModOrganizer.exe') -PathType Leaf)) { throw 'Select the MO2 installation folder containing ModOrganizer.exe.' }
if (Get-Process -Name ModOrganizer -ErrorAction SilentlyContinue) { throw 'Close MO2 before replacing its plugin, then run this installer again.' }
$source = Join-Path $PSScriptRoot 'MO2\mo2_update_arsenal'
$packagedSource = (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'MO2')) -or (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'BUILD_INFO.json'))
if (!$packagedSource) {
    $source = Join-Path (Split-Path $PSScriptRoot -Parent) 'mo2_update_arsenal'
}
if (!(Test-Path -LiteralPath (Join-Path $source '__init__.py')) -or !(Test-Path -LiteralPath (Join-Path $source 'web\index.html'))) { throw 'The complete Modhunter MO2 plugin is missing from this package.' }
$helper = Join-Path $source 'bin\modhunter-cli.exe'
if (!(Test-Path -LiteralPath $helper -PathType Leaf)) { throw 'The Rust plugin audit helper is missing. Use the complete Windows package containing MO2\mo2_update_arsenal\bin\modhunter-cli.exe.' }
$qtHelper = Join-Path $source 'bin\modhunter-qt-bridge.dll'
if (!(Test-Path -LiteralPath $qtHelper -PathType Leaf)) { throw 'The native Qt bridge helper is missing. Use the complete Windows package containing MO2\mo2_update_arsenal\bin\modhunter-qt-bridge.dll.' }
foreach ($path in @($source, (Join-Path $source 'web'), (Join-Path $source 'bin'), $helper, $qtHelper)) {
    if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing to install a linked native helper: $path" }
}
$versionFile = Join-Path $source 'version.py'
if (!(Test-Path -LiteralPath $versionFile -PathType Leaf)) { throw 'The MO2 plugin version is missing. Extract the complete Windows package again.' }
$versionMatches = [regex]::Matches([IO.File]::ReadAllText($versionFile), '(?m)^\s*VERSION\s*=\s*["'']([0-9]+\.[0-9]+\.[0-9]+)["'']\s*$')
if ($versionMatches.Count -ne 1) { throw 'The MO2 plugin version is invalid. Extract the complete Windows package again.' }
$pluginVersion = $versionMatches[0].Groups[1].Value
if ($packagedSource) {
    # Validate every installed package file before creating staging or moving
    # the existing plugin. This catches incomplete extraction, damaged helpers
    # and mixed-version files without touching the user's current installation.
    $checksumFile = Join-Path $PSScriptRoot 'SHA256SUMS.txt'
    if (!(Test-Path -LiteralPath $checksumFile -PathType Leaf)) { throw 'The package checksum manifest is missing. Extract the complete Windows ZIP, including SHA256SUMS.txt.' }
    $manifest = @{}
    foreach ($line in [IO.File]::ReadAllLines($checksumFile)) {
        if ($line -notmatch '^([0-9a-fA-F]{64})  (.+)$') { throw 'The package checksum manifest is invalid.' }
        $digest = $Matches[1]
        $relative = $Matches[2].Replace('\', '/')
        if ([IO.Path]::IsPathRooted($relative) -or $relative.Contains(':') -or $relative -match '(^|/)(\.|\.\.|)(/|$)' -or $manifest.ContainsKey($relative)) { throw 'The package checksum manifest contains an invalid or duplicate path.' }
        $manifest[$relative] = $digest
    }
    $installedFiles = @($manifest.Keys | Where-Object {
        $_ -match '^MO2/mo2_update_arsenal/[^/]+\.py$' -or $_ -in @(
            'MO2/mo2_update_arsenal/web/index.html',
            'MO2/mo2_update_arsenal/bin/modhunter-cli.exe',
            'MO2/mo2_update_arsenal/bin/modhunter-qt-bridge.dll')
    })
    foreach ($relative in @('BUILD_INFO.json', 'MO2/mo2_update_arsenal/__init__.py', 'MO2/mo2_update_arsenal/plugin.py', 'MO2/mo2_update_arsenal/version.py', 'MO2/mo2_update_arsenal/web/index.html', 'MO2/mo2_update_arsenal/bin/modhunter-cli.exe', 'MO2/mo2_update_arsenal/bin/modhunter-qt-bridge.dll')) {
        if (!$manifest.ContainsKey($relative)) { throw "The package checksum manifest omitted a required file: $relative" }
    }
    foreach ($relative in @('BUILD_INFO.json') + $installedFiles) {
        $path = Join-Path $PSScriptRoot $relative
        if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "A packaged plugin file is missing: $relative" }
        if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing to install a linked package file: $relative" }
        if ((Get-PluginFileSHA256 $path) -ne $manifest[$relative]) { throw "The packaged plugin checksum does not match: $relative. Extract the complete Windows ZIP again." }
    }
    foreach ($file in (Get-ChildItem -LiteralPath $source -File -Filter '*.py')) {
        if (!$manifest.ContainsKey('MO2/mo2_update_arsenal/' + $file.Name)) { throw "The plugin contains a file outside its package checksum manifest: $($file.Name)" }
    }
    $build = [IO.File]::ReadAllText((Join-Path $PSScriptRoot 'BUILD_INFO.json')) | ConvertFrom-Json
    if ($build.version -ne $pluginVersion -or $build.commit -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$') { throw 'The package build and MO2 plugin versions do not match. Extract one complete Windows release again.' }
}
$plugins = Join-Path $MO2Directory 'plugins'
$destination = Join-Path $plugins 'mo2_update_arsenal'
$backupRoot = Join-Path $MO2Directory 'ModhunterBackups'
foreach ($path in @($plugins, $destination)) {
    if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Refusing to replace a linked plugin directory: $path" }
}
if ((Test-Path -LiteralPath $backupRoot) -and ((Get-Item -LiteralPath $backupRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Refusing to use a linked backup directory: $backupRoot" }
New-Item -ItemType Directory -Force -Path $plugins | Out-Null
$nonce = [Guid]::NewGuid().ToString('N')
$staging = Join-Path $MO2Directory ('.modhunter-staging-' + $nonce)
$backup = Join-Path $backupRoot ((Get-Date).ToString('yyyyMMdd-HHmmss') + '-' + $nonce)
$movedOld = $false

function Copy-RegularPluginData([string]$From, [string]$To) {
    # Keep this compatible with Windows PowerShell 5.1, used by the .bat entry
    # point. Enumerate one level at a time so a junction is rejected before it
    # can be traversed; -Recurse could follow provider/version-specific links.
    $directory = Get-Item -LiteralPath $From -Force
    if (($directory.Attributes -band [IO.FileAttributes]::ReparsePoint) -or !$directory.PSIsContainer) {
        throw "Refusing to preserve linked or non-directory plugin data: $From"
    }
    [IO.Directory]::CreateDirectory($To) | Out-Null
    foreach ($entry in (Get-ChildItem -LiteralPath $From -Force)) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw "Refusing to preserve linked plugin data: $($entry.FullName)"
        }
        $target = Join-Path $To $entry.Name
        if ($entry.PSIsContainer) { Copy-RegularPluginData $entry.FullName $target }
        else { [IO.File]::Copy($entry.FullName, $target, $false) }
    }
}

try {
    New-Item -ItemType Directory -Path $staging | Out-Null
    Get-ChildItem -LiteralPath $source -File -Filter '*.py' | Copy-Item -Destination $staging
    New-Item -ItemType Directory -Path (Join-Path $staging 'web') | Out-Null
    Copy-Item -LiteralPath (Join-Path $source 'web\index.html') -Destination (Join-Path $staging 'web\index.html')
    New-Item -ItemType Directory -Path (Join-Path $staging 'bin') | Out-Null
    Copy-Item -LiteralPath $helper -Destination (Join-Path $staging 'bin\modhunter-cli.exe')
    Copy-Item -LiteralPath $qtHelper -Destination (Join-Path $staging 'bin\modhunter-qt-bridge.dll')
    if ($packagedSource) {
        # Confirm the bytes that will actually be installed. A source file can
        # change after preflight or while it is copied; reject that staging
        # before preserving data or moving the existing plugin into its backup.
        foreach ($relative in $installedFiles) {
            $path = Join-Path $staging ($relative.Substring('MO2/mo2_update_arsenal/'.Length))
            if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "A staged plugin file is missing: $relative" }
            if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing to install a linked staged plugin file: $relative" }
            if ((Get-PluginFileSHA256 $path) -ne $manifest[$relative]) { throw "The staged plugin checksum does not match: $relative. Extract the complete Windows ZIP again." }
        }
        foreach ($file in (Get-ChildItem -LiteralPath $staging -File -Filter '*.py' -Force)) {
            if (!$manifest.ContainsKey('MO2/mo2_update_arsenal/' + $file.Name)) { throw "The staged plugin contains a file outside its package checksum manifest: $($file.Name)" }
        }
    }
    if (Test-Path -LiteralPath $destination) {
        # Older MO2 builds can store Chronicle progress, cache and operation
        # history in plugin/data. Carry that state into the new plugin while
        # leaving an unchanged copy in the full previous-plugin backup.
        $oldData = @(Get-ChildItem -LiteralPath $destination -Force | Where-Object { $_.Name -eq 'data' })
        if ($oldData.Count -gt 1) { throw 'Several plugin data paths matched; refusing an ambiguous upgrade.' }
        if ($oldData.Count -eq 1) {
            Copy-RegularPluginData $oldData[0].FullName (Join-Path $staging 'data')
        }
        New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
        Move-Item -LiteralPath $destination -Destination $backup
        $movedOld = $true
    }
    try { Move-Item -LiteralPath $staging -Destination $destination }
    catch {
        if ($movedOld -and !(Test-Path -LiteralPath $destination)) { Move-Item -LiteralPath $backup -Destination $destination }
        throw
    }
    Write-Host "Installed Modhunter $pluginVersion plugin to $destination"
    if ($movedOld) { Write-Host "Previous plugin preserved at $backup" }
    Write-Host 'Start MO2 and let its profile load, then launch Modhunter. The bridge starts automatically; Find MO2 reconnects on demand.'
} finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}
