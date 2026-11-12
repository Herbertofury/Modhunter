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
try {
    New-Item -ItemType Directory -Path $staging | Out-Null
    Get-ChildItem -LiteralPath $source -File -Filter '*.py' | Copy-Item -Destination $staging
    New-Item -ItemType Directory -Path (Join-Path $staging 'web') | Out-Null
    Copy-Item -LiteralPath (Join-Path $source 'web\index.html') -Destination (Join-Path $staging 'web\index.html')
    if (Test-Path -LiteralPath $destination) {
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
    Write-Host 'Start MO2, open Tools > Update Arsenal - Quest Board, and connect Modhunter to its loopback address.'
} finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}
