param([string]$MO2Directory)
$ErrorActionPreference = 'Stop'
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
if (!(Test-Path -LiteralPath $source -PathType Container)) {
    $source = Join-Path (Split-Path $PSScriptRoot -Parent) 'mo2_update_arsenal'
}
if (!(Test-Path -LiteralPath (Join-Path $source '__init__.py')) -or !(Test-Path -LiteralPath (Join-Path $source 'web\index.html'))) { throw 'The complete Modhunter MO2 plugin is missing from this package.' }
$helper = Join-Path $source 'bin\modhunter-cli.exe'
if (!(Test-Path -LiteralPath $helper -PathType Leaf)) { throw 'The Rust plugin audit helper is missing. Use the complete Windows package containing MO2\mo2_update_arsenal\bin\modhunter-cli.exe.' }
$qtHelper = Join-Path $source 'bin\modhunter-qt-bridge.dll'
if (!(Test-Path -LiteralPath $qtHelper -PathType Leaf)) { throw 'The native Qt bridge helper is missing. Use the complete Windows package containing MO2\mo2_update_arsenal\bin\modhunter-qt-bridge.dll.' }
foreach ($path in @((Join-Path $source 'bin'), $helper, $qtHelper)) {
    if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing to install a linked native helper: $path" }
}
$plugins = Join-Path $MO2Directory 'plugins'
New-Item -ItemType Directory -Force -Path $plugins | Out-Null
$destination = Join-Path $plugins 'mo2_update_arsenal'
foreach ($path in @($plugins, $destination)) {
    if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Refusing to replace a linked plugin directory: $path" }
}
$nonce = [Guid]::NewGuid().ToString('N')
$staging = Join-Path $MO2Directory ('.modhunter-staging-' + $nonce)
$backupRoot = Join-Path $MO2Directory 'ModhunterBackups'
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
    Write-Host "Installed Modhunter 0.9 plugin to $destination"
    if ($movedOld) { Write-Host "Previous plugin preserved at $backup" }
    Write-Host 'Start MO2, open Tools > Update Arsenal - Quest Board, then click Find MO2 in Modhunter.'
} finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}
