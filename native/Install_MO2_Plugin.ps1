param(
    [string]$MO2Directory,
    [switch]$Json,
    [switch]$Preview,
    [string]$ExpectedVersion,
    [string]$ExpectedCommit,
    [string]$ExpectedManifestSha256,
    [string]$ExpectedInstalledFingerprint
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if ($Json) { [Console]::OutputEncoding = New-Object Text.UTF8Encoding($false) }
$report = [ordered]@{
    schemaVersion = 1; ok = $false; status = 'failed'; code = 'invalid_request'
    version = $null; commit = $null; targetDirectory = $MO2Directory
    pluginDirectory = $null; previousVersion = $null; backupDirectory = $null
    backupRoot = $null; preservedData = $false; rollback = 'not_needed'
    message = ''; installedFingerprint = $null
}
$exitCode = 0
$failureCode = 'invalid_request'
$installMutex = $null
$lockHeld = $false
$staging = $null
$movedOld = $false

function Refuse-Install([string]$Code, [string]$Message) {
    $problem = New-Object InvalidOperationException($Message)
    $problem.Data['InstallCode'] = $Code
    throw $problem
}

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

function Get-TextSHA256([string]$Text) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($Text))).Replace('-', '').ToLowerInvariant() }
    finally { $sha.Dispose() }
}

function Assert-NoLinkedPath([string]$Path, [switch]$AllowMissing) {
    $full = [IO.Path]::GetFullPath($Path)
    if ($full -match '[\x00-\x1f<>"|?*]' -or $full.StartsWith('\\?\') -or $full.StartsWith('\\.\')) {
        Refuse-Install 'unsafe_path' 'The selected filesystem path is not supported.'
    }
    $cursor = $full
    while ($cursor) {
        $entry = Get-Item -LiteralPath $cursor -Force -ErrorAction SilentlyContinue
        if ($entry) {
            if ($entry.PSProvider.Name -ne 'FileSystem' -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                Refuse-Install 'unsafe_path' "Refusing a linked filesystem path: $cursor"
            }
        } elseif ($cursor -eq $full -and !$AllowMissing) {
            Refuse-Install 'invalid_target' "The required filesystem path is unavailable: $full"
        }
        $parent = [IO.Path]::GetDirectoryName($cursor.TrimEnd([char[]]'\/'))
        if (!$parent -or $parent -eq $cursor) { break }
        $cursor = $parent
    }
    return $full
}

function Read-InstalledVersion([string]$Path) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $file = Get-Item -LiteralPath $Path -Force
    if ($file.Length -gt 65536 -or ($file.Attributes -band [IO.FileAttributes]::ReparsePoint)) { return $null }
    $versions = [regex]::Matches([IO.File]::ReadAllText($Path), '(?m)^\s*VERSION\s*=\s*["'']([0-9]+\.[0-9]+\.[0-9]+)["'']\s*$')
    if ($versions.Count -eq 1) { return $versions[0].Groups[1].Value }
    return $null
}

function Test-X64Executable([string]$Path) {
    $stream = $null; $reader = $null
    try {
        $stream = [IO.File]::OpenRead($Path)
        $reader = New-Object IO.BinaryReader($stream)
        if ($stream.Length -lt 128 -or $reader.ReadUInt16() -ne 0x5A4D) { return $false }
        $stream.Position = 0x3c
        $offset = $reader.ReadUInt32()
        if ($offset -lt 64 -or $offset -gt 16777216 -or ($offset + 24) -gt $stream.Length) { return $false }
        $stream.Position = $offset
        return ($reader.ReadUInt32() -eq 0x4550 -and $reader.ReadUInt16() -eq 0x8664)
    } catch { return $false }
    finally { if ($reader) { $reader.Dispose() } elseif ($stream) { $stream.Dispose() } }
}

function Get-FinalExecutablePath([string]$Path) {
    # The running process can have been launched through an 8.3 name or a
    # directory alias. Compare the actual opened files' final Windows paths.
    if (!('ModhunterInstallerNativePath' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using System.Text;
using Microsoft.Win32.SafeHandles;
public static class ModhunterInstallerNativePath {
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    public static extern uint GetFinalPathNameByHandle(SafeFileHandle handle, StringBuilder path, uint length, uint flags);
}
'@
    }
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Open, [IO.FileAccess]::Read,
        [IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete)
    try {
        $buffer = New-Object Text.StringBuilder(32768)
        $length = [ModhunterInstallerNativePath]::GetFinalPathNameByHandle($stream.SafeFileHandle, $buffer, 32768, 0)
        if ($length -eq 0 -or $length -ge 32768) { throw 'Could not identify the running executable path.' }
        return $buffer.ToString().TrimEnd([char[]]'\/')
    } finally { $stream.Dispose() }
}

function Assert-MO2Stopped([string]$Executable) {
    try { $processes = @(Get-Process -Name ModOrganizer -ErrorAction Stop) }
    catch {
        if ($_.FullyQualifiedErrorId -like 'NoProcessFoundForGivenName*') { return }
        Refuse-Install 'process_ambiguous' 'Running MO2 processes could not be inspected safely. Try again after closing MO2.'
    }
    if ($processes.Count -eq 0) { return }
    try { $selected = Get-FinalExecutablePath $Executable }
    catch { Refuse-Install 'process_ambiguous' 'Could not safely identify the selected MO2 executable while MO2 is running.' }
    foreach ($process in $processes) {
        try {
            $path = $process.Path
            if (!$path) { throw 'The process path is unavailable.' }
            $running = Get-FinalExecutablePath $path
        } catch {
            Refuse-Install 'process_ambiguous' 'A running MO2 process cannot be identified safely. Close that MO2 process and try again.'
        }
        if ([string]::Equals($selected, $running, [StringComparison]::OrdinalIgnoreCase)) {
            Refuse-Install 'mo2_running' 'Close the selected MO2 application before replacing its plugin, then try again.'
        }
    }
}

function Add-InstalledFingerprintRows([string]$Directory, [string]$Prefix, $Rows) {
    foreach ($entry in (Get-ChildItem -LiteralPath $Directory -Force | Sort-Object Name)) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            Refuse-Install 'unsafe_path' "Refusing to preserve linked plugin data or files: $($entry.FullName)"
        }
        $relative = $Prefix + $entry.Name
        if ($entry.PSIsContainer) {
            $Rows.Add('d:' + $relative)
            Add-InstalledFingerprintRows $entry.FullName ($relative + '/') $Rows
        } else { $Rows.Add('f:' + $relative + ':' + (Get-PluginFileSHA256 $entry.FullName)) }
    }
}

function Get-InstalledFingerprint([string]$Root, [string]$Destination) {
    $rows = New-Object 'Collections.Generic.List[string]'
    $rows.Add('target:' + $Root.ToUpperInvariant())
    $rows.Add('exe:' + (Get-PluginFileSHA256 (Join-Path $Root 'ModOrganizer.exe')))
    if (Test-Path -LiteralPath $Destination) {
        $entry = Get-Item -LiteralPath $Destination -Force
        if (!$entry.PSIsContainer -or ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            Refuse-Install 'unsafe_path' 'Refusing to replace a linked or non-directory plugin path.'
        }
        $rows.Add('plugin:present')
        Add-InstalledFingerprintRows $Destination '' $rows
    } else { $rows.Add('plugin:missing') }
    return Get-TextSHA256 ($rows.ToArray() | ConvertTo-Json -Compress)
}

function Assert-CompletePackage([string]$Directory, [string]$Prefix, $Manifest, $Observed) {
    foreach ($entry in (Get-ChildItem -LiteralPath $Directory -Force)) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { Refuse-Install 'package_invalid' 'Refusing a linked installer resource.' }
        $relative = $Prefix + $entry.Name
        if ($entry.PSIsContainer) { Assert-CompletePackage $entry.FullName ($relative + '/') $Manifest $Observed }
        elseif ($relative -ne 'SHA256SUMS.txt') {
            if (!$Manifest.ContainsKey($relative) -or $entry.Length -gt 268435456 -or
                (Get-PluginFileSHA256 $entry.FullName) -ne $Manifest[$relative]) {
                Refuse-Install 'package_invalid' "The complete installer resource does not match its checksum manifest: $relative"
            }
            $Observed.Add($relative)
        }
    }
}

try {
# Rust's canonical Windows paths use the extended-length spelling. Normalize
# only ordinary disk/UNC targets before PowerShell provider operations; device
# namespaces such as GLOBALROOT remain rejected by the normal path checks.
if ($MO2Directory -and $MO2Directory.StartsWith('\\?\UNC\', [StringComparison]::OrdinalIgnoreCase)) {
    $MO2Directory = '\\' + $MO2Directory.Substring(8)
} elseif ($MO2Directory -and $MO2Directory.StartsWith('\\?\', [StringComparison]::Ordinal) -and $MO2Directory.Substring(4) -match '^[A-Za-z]:\\') {
    $MO2Directory = $MO2Directory.Substring(4)
}
if ($Json) {
    if (!$MO2Directory -or $MO2Directory -notmatch '^(?:[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$))' -or
        $ExpectedVersion -notmatch '^[0-9]+\.[0-9]+\.[0-9]+$' -or
        $ExpectedCommit -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$' -or
        $ExpectedManifestSha256 -notmatch '^[0-9a-fA-F]{64}$' -or
        (!$Preview -and $ExpectedInstalledFingerprint -notmatch '^[0-9a-fA-F]{64}$')) {
        Refuse-Install 'invalid_request' 'JSON mode requires an absolute MO2 directory and expected package identity; installation also requires the reviewed installed fingerprint.'
    }
}
if (!$MO2Directory) {
    if ($Preview) { Refuse-Install 'invalid_request' 'Preview requires an explicit MO2 directory.' }
    Add-Type -AssemblyName System.Windows.Forms
    $picker = New-Object System.Windows.Forms.FolderBrowserDialog
    $picker.Description = 'Select the MO2 installation folder containing ModOrganizer.exe'
    if ($picker.ShowDialog() -ne [System.Windows.Forms.DialogResult]::OK) { Write-Host 'Installation canceled.'; exit 0 }
    $MO2Directory = $picker.SelectedPath
}
$failureCode = 'invalid_target'
$MO2Directory = Assert-NoLinkedPath $MO2Directory
$MO2Directory = (Get-Item -LiteralPath $MO2Directory -Force).FullName
if ($MO2Directory.Length -gt [IO.Path]::GetPathRoot($MO2Directory).Length) { $MO2Directory = $MO2Directory.TrimEnd([char[]]'\/') }
$report.targetDirectory = $MO2Directory
if (!(Test-Path -LiteralPath (Join-Path $MO2Directory 'ModOrganizer.exe') -PathType Leaf)) { throw 'Select the MO2 installation folder containing ModOrganizer.exe.' }
Assert-NoLinkedPath (Join-Path $MO2Directory 'ModOrganizer.exe') | Out-Null
if ($Json -and !(Test-X64Executable (Join-Path $MO2Directory 'ModOrganizer.exe'))) {
    Refuse-Install 'unsupported_target' 'This plugin bundle requires a valid 64-bit x64 ModOrganizer.exe. Select a supported x64 MO2 application.'
}
$installMutex = New-Object Threading.Mutex($false, ('Local\ModhunterPlugin-' + (Get-TextSHA256 $MO2Directory.ToUpperInvariant())))
try { $lockHeld = $installMutex.WaitOne(0) }
catch [Threading.AbandonedMutexException] { $lockHeld = $true }
if (!$lockHeld) { Refuse-Install 'target_busy' 'Another installer is working on this MO2 application. Wait for it to finish, then preview again.' }
Assert-MO2Stopped (Join-Path $MO2Directory 'ModOrganizer.exe')
$failureCode = 'package_invalid'
Assert-NoLinkedPath $PSScriptRoot | Out-Null
$source = Join-Path $PSScriptRoot 'MO2\mo2_update_arsenal'
$packagedSource = (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'MO2')) -or (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'BUILD_INFO.json'))
if (!$packagedSource) {
    if ($Json) { Refuse-Install 'package_invalid' 'JSON mode requires the complete verified installer resource bundle.' }
    $source = Join-Path (Split-Path $PSScriptRoot -Parent) 'mo2_update_arsenal'
}
Assert-NoLinkedPath $source | Out-Null
if (!(Test-Path -LiteralPath (Join-Path $source '__init__.py')) -or !(Test-Path -LiteralPath (Join-Path $source 'web\index.html'))) { throw 'The complete Modhunter MO2 plugin is missing from this package.' }
$helper = Join-Path $source 'bin\modhunter-cli.exe'
if (!(Test-Path -LiteralPath $helper -PathType Leaf)) { throw 'The Rust plugin audit helper is missing. Use the complete Windows package containing MO2\mo2_update_arsenal\bin\modhunter-cli.exe.' }
$qtHelper = Join-Path $source 'bin\modhunter-qt-bridge.dll'
if (!(Test-Path -LiteralPath $qtHelper -PathType Leaf)) { throw 'The native Qt bridge helper is missing. Use the complete Windows package containing MO2\mo2_update_arsenal\bin\modhunter-qt-bridge.dll.' }
foreach ($path in @($source, (Join-Path $source 'web'), (Join-Path $source 'bin'), $helper, $qtHelper)) {
    if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing to install a linked native helper: $path" }
}
# The Rust helper resolves archive-tools beside its own executable. These
# four pinned-package components must travel with the installed plugin too.
$archiveRoot = Join-Path $source 'bin\archive-tools'
$archiveNames = @('7z.exe', '7z.dll', 'License.txt', 'provenance.json')
$archiveRelativeFiles = @($archiveNames | ForEach-Object { 'MO2/mo2_update_arsenal/bin/archive-tools/' + $_ })
$archiveSourceHashes = @{}
if (!(Test-Path -LiteralPath $archiveRoot -PathType Container)) { throw 'The complete archive helper is missing. Extract the complete Windows package including MO2\mo2_update_arsenal\bin\archive-tools.' }
if ((Get-Item -LiteralPath $archiveRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to install a linked archive helper directory.' }
foreach ($name in $archiveNames) {
    $path = Join-Path $archiveRoot $name
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "The complete archive helper is missing: $name. Extract the complete Windows package again." }
    if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing to install a linked archive helper file: $name" }
    $archiveSourceHashes[$name] = Get-PluginFileSHA256 $path
}
$versionFile = Join-Path $source 'version.py'
if (!(Test-Path -LiteralPath $versionFile -PathType Leaf)) { throw 'The MO2 plugin version is missing. Extract the complete Windows package again.' }
Assert-NoLinkedPath $versionFile | Out-Null
if ((Get-Item -LiteralPath $versionFile).Length -gt 65536) { throw 'The MO2 plugin version is invalid. Extract the complete Windows package again.' }
$versionMatches = [regex]::Matches([IO.File]::ReadAllText($versionFile), '(?m)^\s*VERSION\s*=\s*["'']([0-9]+\.[0-9]+\.[0-9]+)["'']\s*$')
if ($versionMatches.Count -ne 1) { throw 'The MO2 plugin version is invalid. Extract the complete Windows package again.' }
$pluginVersion = $versionMatches[0].Groups[1].Value
$report.version = $pluginVersion
if ($packagedSource) {
    # Validate every installed package file before creating staging or moving
    # the existing plugin. This catches incomplete extraction, damaged helpers
    # and mixed-version files without touching the user's current installation.
    $checksumFile = Join-Path $PSScriptRoot 'SHA256SUMS.txt'
    if (!(Test-Path -LiteralPath $checksumFile -PathType Leaf)) { throw 'The package checksum manifest is missing. Extract the complete Windows ZIP, including SHA256SUMS.txt.' }
    Assert-NoLinkedPath $checksumFile | Out-Null
    if ((Get-Item -LiteralPath $checksumFile).Length -gt 4194304) { throw 'The package checksum manifest is invalid.' }
    if ($ExpectedManifestSha256 -and (Get-PluginFileSHA256 $checksumFile) -ne $ExpectedManifestSha256) {
        Refuse-Install 'package_changed' 'The installer resource checksum manifest differs from the desktop build.'
    }
    $manifest = @{}
    foreach ($line in [IO.File]::ReadAllLines($checksumFile)) {
        if ($line -notmatch '^([0-9a-fA-F]{64})  (.+)$') { throw 'The package checksum manifest is invalid.' }
        $digest = $Matches[1]
        $relative = $Matches[2].Replace('\', '/')
        if ([IO.Path]::IsPathRooted($relative) -or $relative -match '[<>:"|?*\x00-\x1f]' -or
            $relative -match '(^|/)(\.|\.\.|)(/|$)' -or $relative -match '[ .](/|$)' -or
            $relative -match '(?i)(^|/)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(\.[^/]+)?(/|$)' -or
            $manifest.ContainsKey($relative)) { throw 'The package checksum manifest contains an invalid or duplicate path.' }
        $manifest[$relative] = $digest
    }
    $installedFiles = @($manifest.Keys | Where-Object {
        $_ -match '^MO2/mo2_update_arsenal/[^/]+\.py$' -or $_ -in @(
            'MO2/mo2_update_arsenal/web/index.html',
            'MO2/mo2_update_arsenal/bin/modhunter-cli.exe',
            'MO2/mo2_update_arsenal/bin/modhunter-qt-bridge.dll') -or ($archiveRelativeFiles -contains $_)
    })
    foreach ($relative in (@('BUILD_INFO.json', 'MO2/mo2_update_arsenal/__init__.py', 'MO2/mo2_update_arsenal/plugin.py', 'MO2/mo2_update_arsenal/version.py', 'MO2/mo2_update_arsenal/web/index.html', 'MO2/mo2_update_arsenal/bin/modhunter-cli.exe', 'MO2/mo2_update_arsenal/bin/modhunter-qt-bridge.dll') + $archiveRelativeFiles)) {
        if (!$manifest.ContainsKey($relative)) { throw "The package checksum manifest omitted a required file: $relative" }
    }
    foreach ($relative in @('BUILD_INFO.json') + $installedFiles) {
        $path = Join-Path $PSScriptRoot $relative
        if (!(Test-Path -LiteralPath $path -PathType Leaf)) { throw "A packaged plugin file is missing: $relative" }
        Assert-NoLinkedPath $path | Out-Null
        if ((Get-Item -LiteralPath $path -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw "Refusing to install a linked package file: $relative" }
        if ((Get-Item -LiteralPath $path -Force).Length -gt 268435456) { throw "A packaged plugin file is oversized: $relative" }
        if ((Get-PluginFileSHA256 $path) -ne $manifest[$relative]) { throw "The packaged plugin checksum does not match: $relative. Extract the complete Windows ZIP again." }
    }
    foreach ($file in (Get-ChildItem -LiteralPath $source -File -Filter '*.py')) {
        if (!$manifest.ContainsKey('MO2/mo2_update_arsenal/' + $file.Name)) { throw "The plugin contains a file outside its package checksum manifest: $($file.Name)" }
    }
    $buildFile = Join-Path $PSScriptRoot 'BUILD_INFO.json'
    if ((Get-Item -LiteralPath $buildFile).Length -gt 65536) { throw 'The package build identity is invalid.' }
    $build = [IO.File]::ReadAllText($buildFile) | ConvertFrom-Json
    if ($build.version -isnot [string] -or $build.version -cne $pluginVersion -or $build.commit -isnot [string] -or
        $build.commit -notmatch '^(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})$') { throw 'The package build and MO2 plugin versions do not match. Extract one complete Windows release again.' }
    $report.commit = $build.commit.ToLowerInvariant()
    if (($ExpectedVersion -and $ExpectedVersion -cne $pluginVersion) -or
        ($ExpectedCommit -and $ExpectedCommit -ne $build.commit)) {
        Refuse-Install 'package_changed' 'The installer resource does not match the selected desktop version and source commit.'
    }
    if ($Json) {
        if (!$manifest.ContainsKey('Install_MO2_Plugin.ps1')) { throw 'The package checksum manifest omitted the installer script.' }
        if (@($manifest.Keys | Where-Object { $_.StartsWith('MO2/mo2_update_arsenal/', [StringComparison]::OrdinalIgnoreCase) -and $_ -notin $installedFiles }).Count -ne 0) {
            throw 'The installer resource declares plugin files that this installer does not own.'
        }
        $observed = New-Object 'Collections.Generic.List[string]'
        Assert-CompletePackage $PSScriptRoot '' $manifest $observed
        if ($observed.Count -ne $manifest.Count) { throw 'The complete installer resource is missing a manifest file.' }
    }
}
$failureCode = 'target_unsafe'
$plugins = Join-Path $MO2Directory 'plugins'
$destination = Join-Path $plugins 'mo2_update_arsenal'
$backupRoot = Join-Path $MO2Directory 'ModhunterBackups'
$report.pluginDirectory = $destination
$report.backupRoot = $backupRoot
foreach ($path in @($plugins, $destination)) {
    if ((Test-Path -LiteralPath $path) -and ((Get-Item -LiteralPath $path).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Refusing to replace a linked plugin directory: $path" }
}
if ((Test-Path -LiteralPath $backupRoot) -and ((Get-Item -LiteralPath $backupRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Refusing to use a linked backup directory: $backupRoot" }
foreach ($path in @($plugins, $destination, $backupRoot)) {
    Assert-NoLinkedPath $path -AllowMissing | Out-Null
    if ((Test-Path -LiteralPath $path) -and !(Test-Path -LiteralPath $path -PathType Container)) { throw 'The plugin or backup path is not a directory.' }
}
$fingerprint = Get-InstalledFingerprint $MO2Directory $destination
$report.installedFingerprint = $fingerprint
$report.previousVersion = Read-InstalledVersion (Join-Path $destination 'version.py')
if ($ExpectedInstalledFingerprint -and $ExpectedInstalledFingerprint -ne $fingerprint) {
    Refuse-Install 'target_changed' 'The selected MO2 application or its plugin changed after preview. Review a new preview before installing.'
}
if ($Preview) {
    $report.ok = $true
    $report.status = 'preview'
    $report.code = 'ready'
    $report.message = 'The selected application and matching plugin package are ready for installation.'
} else {
$failureCode = 'install_failed'
New-Item -ItemType Directory -Force -Path $plugins | Out-Null
$nonce = [Guid]::NewGuid().ToString('N')
$staging = Join-Path $MO2Directory ('.modhunter-staging-' + $nonce)
$backup = Join-Path $backupRoot ((Get-Date).ToString('yyyyMMdd-HHmmss') + '-' + $nonce)

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
    foreach ($file in (Get-ChildItem -LiteralPath $source -File -Filter '*.py')) {
        Copy-Item -LiteralPath $file.FullName -Destination $staging
    }
    New-Item -ItemType Directory -Path (Join-Path $staging 'web') | Out-Null
    Copy-Item -LiteralPath (Join-Path $source 'web\index.html') -Destination (Join-Path $staging 'web\index.html')
    New-Item -ItemType Directory -Path (Join-Path $staging 'bin') | Out-Null
    Copy-Item -LiteralPath $helper -Destination (Join-Path $staging 'bin\modhunter-cli.exe')
    Copy-Item -LiteralPath $qtHelper -Destination (Join-Path $staging 'bin\modhunter-qt-bridge.dll')
    $archiveStaging = Join-Path $staging 'bin\archive-tools'
    New-Item -ItemType Directory -Path $archiveStaging | Out-Null
    foreach ($name in $archiveNames) {
        $from = Join-Path $archiveRoot $name
        $to = Join-Path $archiveStaging $name
        if (((Get-Item -LiteralPath $archiveRoot -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) -or
            ((Get-Item -LiteralPath $from -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw "Refusing to copy a linked archive helper: $name" }
        Copy-Item -LiteralPath $from -Destination $to
        if ((Get-PluginFileSHA256 $to) -ne $archiveSourceHashes[$name]) { throw "The staged archive helper changed during installation: $name" }
    }
    # All component bytes are staged; validate before changing the installed plugin.
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
    $hadPrevious = Test-Path -LiteralPath $destination
    if ($hadPrevious) {
        # Older MO2 builds can store Chronicle progress, cache and operation
        # history in plugin/data. Carry that state into the new plugin while
        # leaving an unchanged copy in the full previous-plugin backup.
        $oldData = @(Get-ChildItem -LiteralPath $destination -Force | Where-Object { $_.Name -eq 'data' })
        if ($oldData.Count -gt 1) { throw 'Several plugin data paths matched; refusing an ambiguous upgrade.' }
        if ($oldData.Count -eq 1) {
            Copy-RegularPluginData $oldData[0].FullName (Join-Path $staging 'data')
            $report.preservedData = $true
        }
    }
    # Revalidate after staging and data copy. Preview pins all prior plugin
    # bytes, including progress; the lock serializes our own installers.
    foreach ($path in @($MO2Directory, $plugins, $destination, $backupRoot)) {
        Assert-NoLinkedPath $path -AllowMissing | Out-Null
    }
    if ((Get-InstalledFingerprint $MO2Directory $destination) -ne $fingerprint) {
        Refuse-Install 'target_changed' 'The selected MO2 application or its plugin changed during installation. Nothing was replaced; review a new preview.'
    }
    Assert-MO2Stopped (Join-Path $MO2Directory 'ModOrganizer.exe')
    if ($hadPrevious) {
        New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null
        Move-Item -LiteralPath $destination -Destination $backup
        $movedOld = $true
        $report.backupDirectory = $backup
    }
    try { Move-Item -LiteralPath $staging -Destination $destination }
    catch {
        $replacementError = $_
        if ($movedOld) {
            try {
                if (Test-Path -LiteralPath $destination) { throw 'The destination changed while restoring the previous plugin.' }
                Move-Item -LiteralPath $backup -Destination $destination
                $report.rollback = 'restored'
                $report.backupDirectory = $null
            } catch {
                $report.rollback = 'failed'
                Refuse-Install 'rollback_failed' "Installation failed and the previous plugin could not be restored automatically. Its unchanged backup is at $backup"
            }
        }
        throw $replacementError
    }
    $report.ok = $true
    $report.status = if ($hadPrevious) { 'updated' } else { 'installed' }
    $report.code = 'installed'
    $report.message = "Installed Modhunter $pluginVersion. Start MO2 and let its profile load; the bridge starts automatically."
} finally {
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
}
}
} catch {
    $code = $_.Exception.Data['InstallCode']
    if (!$code) { $code = $failureCode }
    $report.ok = $false
    $report.status = 'failed'
    $report.code = $code
    $report.message = ([string]$_.Exception.Message).Substring(0, [Math]::Min(2048, ([string]$_.Exception.Message).Length))
    $exitCode = if ($code -in @('install_failed', 'rollback_failed')) { 1 } else { 2 }
} finally {
    if ($lockHeld) { $installMutex.ReleaseMutex() }
    if ($installMutex) { $installMutex.Dispose() }
}
if ($Json) { [Console]::Out.WriteLine(($report | ConvertTo-Json -Depth 5 -Compress)) }
elseif ($report.ok) {
    Write-Host $report.message
    if ($report.pluginDirectory -and !$Preview) { Write-Host "Plugin directory: $($report.pluginDirectory)" }
    if ($report.backupDirectory) { Write-Host "Previous plugin preserved at $($report.backupDirectory)" }
} else { [Console]::Error.WriteLine($report.message) }
exit $exitCode
