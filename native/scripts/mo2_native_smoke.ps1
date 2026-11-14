#requires -Version 7.5
<#
.SYNOPSIS
Run the real Modhunter plugin inside the official portable MO2 2.5.2 release.
.DESCRIPTION
Creates a new, isolated fixture instance with a synthetic BasicGame and two
mods, including an inert TES4 header in the disabled mod for Rust analysis.
No owned game or account is used. An isolated, owned fixture ZIP exercises the
real native installer and guarded actual-byte restoration.
The test-only plugins live under native/scripts and are never packaged for users.
Any missing native receipt, failed assertion or unexpected process exit fails.
#>
[CmdletBinding()]
param(
    [string]$RepositoryRoot = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path,
    [string]$EvidenceDirectory = '',
    [string]$WorkDirectory = '',
    [string]$RustCliPath = '',
    [string]$QtBridgePath = '',
    [string]$DesktopExecutable = '',
    [ValidateRange(30, 600)][int]$TimeoutSeconds = 150
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (!$IsWindows) { throw 'The native MO2 smoke requires Windows; it cannot pass through a mock on another OS.' }

function ConvertFrom-ReceiptJson([string]$Content) {
    # Dates are evidence strings, not values to convert to the local timezone
    # or reformat with fewer fractional digits. The release validator requires
    # exact structured equality with the original native JSON document.
    ConvertFrom-Json -InputObject $Content -DateKind String
}

# Deterministically exercise the same parser used for the actual native receipt.
# A random runtime timestamp only exposed the old bug when it ended in zero.
$ReceiptTimestampCases = @('2026-11-13T23:59:00.437220+00:00', '2026-11-13T23:59:00.000000Z', '2026-11-13T17:29:00.120000-06:30')
foreach ($Timestamp in $ReceiptTimestampCases) {
    $ProbeJson = @{ nested = @{ startedAt = $Timestamp }; ok = $true; count = 1 } | ConvertTo-Json -Depth 5
    $Probe = ConvertFrom-ReceiptJson $ProbeJson
    $RoundTrip = ConvertFrom-ReceiptJson ($Probe | ConvertTo-Json -Depth 5)
    if ($RoundTrip.nested.startedAt -isnot [string] -or
        $RoundTrip.nested.startedAt -cne $Timestamp -or
        $RoundTrip.ok -isnot [bool] -or !$RoundTrip.ok -or $RoundTrip.count -ne 1) {
        throw 'Native receipt JSON serialization changed a timestamp or value type.'
    }
}

$RepositoryRoot = [IO.Path]::GetFullPath($RepositoryRoot)
if (!$EvidenceDirectory) { $EvidenceDirectory = Join-Path $RepositoryRoot 'native/evidence/windows-mo2-smoke' }
$EvidenceDirectory = [IO.Path]::GetFullPath($EvidenceDirectory)
if ((Test-Path -LiteralPath $EvidenceDirectory) -and @(Get-ChildItem -LiteralPath $EvidenceDirectory -Force).Count) {
    throw "EvidenceDirectory must be empty to prevent mixing this run with an old native receipt: $EvidenceDirectory"
}
if (!$WorkDirectory) { $WorkDirectory = Join-Path ([IO.Path]::GetTempPath()) ('modhunter-mo2-smoke-' + [guid]::NewGuid().ToString('N')) }
$WorkDirectory = [IO.Path]::GetFullPath($WorkDirectory)
if (Test-Path -LiteralPath $WorkDirectory) {
    throw "WorkDirectory must be new so an existing MO2 instance cannot be changed: $WorkDirectory"
}
New-Item -ItemType Directory -Path $WorkDirectory, $EvidenceDirectory -Force | Out-Null
$RunEvidence = Join-Path $WorkDirectory 'evidence'
New-Item -ItemType Directory -Path $RunEvidence | Out-Null
$ReceiptPath = Join-Path $RunEvidence 'native-receipt.json'
$ReleaseUrl = 'https://github.com/ModOrganizer2/modorganizer/releases/download/v2.5.2/Mod.Organizer-2.5.2.7z'
$ReleaseBytes = 149660212
$Archive = Join-Path $WorkDirectory 'Mod.Organizer-2.5.2.7z'
$ExtractRoot = Join-Path $WorkDirectory 'portable'
$InstanceRoot = Join-Path $WorkDirectory 'instance'
$GameRoot = Join-Path $WorkDirectory 'fixture-game'
$GlobalMO2Root = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ModOrganizer'
$PortableRoot = $null
$Process = $null
$Failure = $null
$StartedAt = [DateTime]::UtcNow
$CriticalProductionFiles = @('plugin.py', 'version.py', 'download_ids.py', 'bridge.py', 'model.py', 'focus.py', 'scanner.py', 'version_evidence.py', 'gameplay.py', 'native_nexus.py', 'qt_bridge.py',
    'intelligence.py', 'plugin_intelligence.py', 'mod_health.py', 'content_recovery.py', 'duplicate_report.py',
    'source_adapters.py', 'source_registry.py', 'update_plan.py', 'quick_update.py',
    'install_brief.py', 'web/index.html')
$Summary = [ordered]@{
    schemaVersion = 1
    test = 'mo2-native-fixture-smoke-orchestrator'
    ok = $false
    phase = 'preflight'
    release = [ordered]@{ version = '2.5.2'; url = $ReleaseUrl; expectedBytes = $ReleaseBytes }
    fixture = [ordered]@{ game = 'Modhunter CI Fixture'; profile = 'Modhunter Smoke'; ownedGameFiles = $false; accountAuthentication = $false }
    startedAt = $StartedAt.ToString('o')
}

function Write-Utf8File([string]$Path, [string]$Content) {
    [IO.File]::WriteAllText($Path, $Content, [Text.UTF8Encoding]::new($false))
}

try {
    # MO2 starts its real nxmhandler helper during normal UI startup. Its
    # supported noregister preference is resolved from this global directory
    # when present, otherwise next to the portable executable. Use a disposable
    # Windows account and never overwrite existing handler/profile settings.
    if ((Test-Path -LiteralPath $GlobalMO2Root) -or
        (Test-Path -LiteralPath 'Registry::HKEY_CLASSES_ROOT\nxm')) {
        throw 'Run this native CI fixture in a clean disposable Windows account; existing MO2 data or an nxm handler was found.'
    }
    if ($RustCliPath) {
        $RustCliPath = [IO.Path]::GetFullPath($RustCliPath)
        if (!(Test-Path -LiteralPath $RustCliPath -PathType Leaf)) {
            throw "The supplied compiled Rust CLI does not exist: $RustCliPath"
        }
        $Summary['rustCli'] = @{
            required = $true
            sha256 = (Get-FileHash -LiteralPath $RustCliPath -Algorithm SHA256).Hash.ToLowerInvariant()
        }
        $ArchiveToolsSource = Join-Path (Split-Path -Parent $RustCliPath) 'archive-tools'
        $ArchiveToolPins = [ordered]@{
            '7z.exe' = 'dc0493429fe28136fcb177a0e77c9875cbb3b0bff5f2f8eeba4df302e98cef76'
            '7z.dll' = '500db9eb17f3c21b729ba41dd02ecfe1823da05028c7e4ba129084cc74cc364e'
        }
        foreach ($Name in @('7z.exe', '7z.dll', 'License.txt', 'provenance.json')) {
            $Tool = Join-Path $ArchiveToolsSource $Name
            if (!(Test-Path -LiteralPath $Tool -PathType Leaf) -or
                ((Get-Item -LiteralPath $Tool).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "Pinned archive helper asset missing or linked: $Name. Run prepare_archive_tools.py before this native smoke."
            }
        }
        if ((Get-Item -LiteralPath (Join-Path $ArchiveToolsSource 'provenance.json')).Length -gt 65536) {
            throw 'Pinned archive helper provenance exceeds its fixture bound.'
        }
        $ArchiveProvenance = ConvertFrom-ReceiptJson (Get-Content -LiteralPath (Join-Path $ArchiveToolsSource 'provenance.json') -Raw)
        if ($ArchiveProvenance.version -ne '26.06' -or
            $ArchiveProvenance.licenseSha256 -cne (Get-FileHash -LiteralPath (Join-Path $ArchiveToolsSource 'License.txt') -Algorithm SHA256).Hash.ToLowerInvariant()) {
            throw 'Pinned archive helper version or license provenance differs.'
        }
        foreach ($Name in $ArchiveToolPins.Keys) {
            $Expected = $ArchiveToolPins[$Name]
            if ((Get-FileHash -LiteralPath (Join-Path $ArchiveToolsSource $Name) -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Expected -or
                $ArchiveProvenance.files.$Name -cne $Expected) {
                throw "Archive helper does not match the pinned official build: $Name"
            }
        }
        $Summary['archiveTools'] = @{ version = '26.06'; files = $ArchiveToolPins; installedBesideCli = $false }
    }
    if (!$QtBridgePath) { throw 'Supply the matching compiled Qt bridge with -QtBridgePath; native Nexus binding proof is mandatory.' }
    $QtBridgePath = [IO.Path]::GetFullPath($QtBridgePath)
    if (!(Test-Path -LiteralPath $QtBridgePath -PathType Leaf)) { throw "The compiled Qt bridge does not exist: $QtBridgePath" }
    $Summary['qtBridge'] = @{
        required = $true
        sha256 = (Get-FileHash -LiteralPath $QtBridgePath -Algorithm SHA256).Hash.ToLowerInvariant()
        buildQt = '6.7.1'
    }
    $ProductionSource = Join-Path $RepositoryRoot 'mo2_update_arsenal'
    $HarnessSource = Join-Path $PSScriptRoot 'mo2_smoke_plugin'
    foreach ($Required in $CriticalProductionFiles) {
        if (!(Test-Path -LiteralPath (Join-Path $ProductionSource $Required) -PathType Leaf)) {
            throw "Production plugin asset missing: $Required. Build the dashboard before running the native test."
        }
    }
    $SevenZipCommand = Get-Command '7z.exe' -ErrorAction SilentlyContinue
    $SevenZip = if ($SevenZipCommand) { $SevenZipCommand.Source } else { Join-Path $env:ProgramFiles '7-Zip/7z.exe' }
    if (!(Test-Path -LiteralPath $SevenZip -PathType Leaf)) { throw '7-Zip is required to extract the official portable release.' }

    $Summary.phase = 'download-official-release'
    Invoke-WebRequest -Uri $ReleaseUrl -OutFile $Archive -TimeoutSec 300 -MaximumRetryCount 3 -RetryIntervalSec 3
    $ActualBytes = (Get-Item -LiteralPath $Archive).Length
    if ($ActualBytes -ne $ReleaseBytes) { throw "Pinned MO2 archive size differs: expected $ReleaseBytes, received $ActualBytes." }
    $Summary.release['bytes'] = $ActualBytes
    $Summary.release['sha256'] = (Get-FileHash -LiteralPath $Archive -Algorithm SHA256).Hash.ToLowerInvariant()
    # GitHub's official asset has no published digest. Record the downloaded
    # SHA256 as evidence; do not present it as an independently verified pin.
    $Summary.release['integrity'] = 'Pinned official HTTPS release and asset size; SHA256 recorded, no upstream digest published'

    $Summary.phase = 'extract-portable-release'
    & $SevenZip x $Archive "-o$ExtractRoot" -y *> (Join-Path $RunEvidence '7zip.log')
    if ($LASTEXITCODE -ne 0) { throw "7-Zip failed with exit code $LASTEXITCODE." }
    $Executables = @(Get-ChildItem -LiteralPath $ExtractRoot -Filter 'ModOrganizer.exe' -File -Recurse)
    if ($Executables.Count -ne 1) { throw 'Expected exactly one native ModOrganizer.exe in the official archive.' }
    $Executable = $Executables[0]
    $PortableRoot = $Executable.Directory.FullName
    $Summary.release['executableVersion'] = $Executable.VersionInfo.FileVersion
    if ($Executable.VersionInfo.FileVersion -notmatch '^2\.5\.2(?:[. +\-]|$)') {
        throw "Unexpected MO2 file version: $($Executable.VersionInfo.FileVersion)"
    }
    $BasicGames = Join-Path $PortableRoot 'plugins/basic_games'
    if (!(Test-Path -LiteralPath (Join-Path $BasicGames 'basic_game.py'))) {
        throw 'The pinned MO2 release did not provide its expected bundled basic_games plugin.'
    }
    $RuntimeDll = Join-Path $env:WINDIR 'System32/vcruntime140.dll'
    $Summary['vcRuntime'] = if (Test-Path -LiteralPath $RuntimeDll) { (Get-Item -LiteralPath $RuntimeDll).VersionInfo.FileVersion } else { 'Not found; native startup will determine availability' }

    $Summary.phase = 'prepare-isolated-fixture'
    $ProfileRoot = Join-Path $InstanceRoot 'profiles/Modhunter Smoke'
    $Directories = @($ProfileRoot, (Join-Path $InstanceRoot 'mods'), (Join-Path $InstanceRoot 'downloads'),
        (Join-Path $InstanceRoot 'overwrite'), (Join-Path $InstanceRoot 'cache'),
        (Join-Path $GameRoot 'Data'), (Join-Path $GameRoot 'Documents'), (Join-Path $GameRoot 'Saves'))
    New-Item -ItemType Directory -Path $Directories -Force | Out-Null
    Write-Utf8File (Join-Path $GameRoot 'fixture.exe') "Modhunter native smoke marker. This is not an executable and must never be launched.`r`n"
    Write-Utf8File (Join-Path $InstanceRoot 'modhunter-smoke-fixture.json') (@{
        purpose = 'isolated native MO2 CI fixture'; runId = [guid]::NewGuid().ToString(); createdAt = $StartedAt.ToString('o')
    } | ConvertTo-Json)
    foreach ($Mod in @(@{ Name = 'Smoke Enabled'; Version = '1.0.0' }, @{ Name = 'Smoke Disabled'; Version = '2.0.0' })) {
        $ModRoot = Join-Path (Join-Path $InstanceRoot 'mods') $Mod.Name
        New-Item -ItemType Directory -Path $ModRoot | Out-Null
        Write-Utf8File (Join-Path $ModRoot 'meta.ini') "[General]`r`ngameName=modhuntersmoke`r`nmodid=0`r`nversion=$($Mod.Version)`r`nrepository=Local`r`nnotes=Native smoke fixture; no game content`r`n"
        Write-Utf8File (Join-Path $ModRoot 'smoke-fixture.txt') "Harmless fixture content for $($Mod.Name).`r`n"
    }
    # One inert 42-byte modern TES4/HEDR header, no masters or content records.
    # It stays in the disabled disposable mod; no installed game data is used.
    $FixturePlugin = Join-Path $InstanceRoot 'mods/Smoke Disabled/ModhunterSmoke.esp'
    $PluginStream = [IO.MemoryStream]::new()
    $PluginWriter = [IO.BinaryWriter]::new($PluginStream)
    try {
        $PluginWriter.Write([Text.Encoding]::ASCII.GetBytes('TES4'))
        $PluginWriter.Write([uint32]18)
        $PluginWriter.Write([uint32]0) # flags
        $PluginWriter.Write([uint32]0) # form ID
        $PluginWriter.Write([uint32]0) # revision
        $PluginWriter.Write([uint16]44)
        $PluginWriter.Write([uint16]0)
        $PluginWriter.Write([Text.Encoding]::ASCII.GetBytes('HEDR'))
        $PluginWriter.Write([uint16]12)
        $PluginWriter.Write([single]1.7)
        $PluginWriter.Write([uint32]0) # declared records and groups
        $PluginWriter.Write([uint32]0x800)
        $PluginWriter.Flush()
        [IO.File]::WriteAllBytes($FixturePlugin, $PluginStream.ToArray())
    }
    finally {
        $PluginWriter.Dispose()
        $PluginStream.Dispose()
    }
    $Summary.fixture['syntheticPlugin'] = @{
        name = 'ModhunterSmoke.esp'; owner = 'Smoke Disabled'; bytes = (Get-Item -LiteralPath $FixturePlugin).Length
        sha256 = (Get-FileHash -LiteralPath $FixturePlugin -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    Write-Utf8File (Join-Path $ProfileRoot 'modlist.txt') "# This file was automatically generated by Mod Organizer.`r`n-Smoke Disabled`r`n+Smoke Enabled`r`n"
    foreach ($ProfileFile in @('plugins.txt', 'loadorder.txt', 'archives.txt', 'lockedorder.txt')) {
        Write-Utf8File (Join-Path $ProfileRoot $ProfileFile) ''
    }
    Write-Utf8File (Join-Path $PortableRoot 'portable.txt') "Modhunter isolated native CI fixture`r`n"
    New-Item -ItemType Directory -Path $GlobalMO2Root | Out-Null
    $NoRegister = "[General]`r`nnoregister=true`r`n"
    Write-Utf8File (Join-Path $GlobalMO2Root 'nxmhandler.ini') $NoRegister
    Write-Utf8File (Join-Path $PortableRoot 'nxmhandler.ini') $NoRegister
    $Summary.fixture['nxmRegistration'] = 'Disabled using the real helper noregister preference in a fresh disposable account'
    $GameIniPath = $GameRoot.Replace('\', '/')
    $BaseIniPath = $InstanceRoot.Replace('\', '/')
    $Settings = @"
[General]
first_start=false
gameName=Modhunter CI Fixture
gamePath=@ByteArray($GameIniPath)
selected_profile=@ByteArray(Modhunter Smoke)
version=2.5.2

[Settings]
base_directory=$BaseIniPath
mod_directory=%BASE_DIR%/mods
profiles_directory=%BASE_DIR%/profiles
download_directory=%BASE_DIR%/downloads
overwrite_directory=%BASE_DIR%/overwrite
cache_directory=%BASE_DIR%/cache
language=en
check_for_updates=false
category_mappings=false
autocheck_update_install=false
log_level=0
"@
    Write-Utf8File (Join-Path $PortableRoot 'ModOrganizer.ini') $Settings
    Write-Utf8File (Join-Path $RunEvidence 'fixture-settings.ini') $Settings

    # Load the unchanged production package through the observer harness only.
    # Leaving a second createPlugin() under plugins would autostart a duplicate
    # bridge and correctly force the desktop into multiple-session selection.
    $PluginDestination = Join-Path $PortableRoot 'modhunter-fixture-libs/mo2_update_arsenal'
    $SourceHashes = [ordered]@{}
    foreach ($File in (Get-ChildItem -LiteralPath $ProductionSource -File -Recurse)) {
        $Relative = [IO.Path]::GetRelativePath($ProductionSource, $File.FullName)
        if ($Relative -match '(^|[\\/])(__pycache__|data)([\\/]|$)' -or $File.Extension -eq '.pyc') { continue }
        $Destination = Join-Path $PluginDestination $Relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $Destination) -Force | Out-Null
        Copy-Item -LiteralPath $File.FullName -Destination $Destination
        if ($Relative.Replace('\', '/') -in $CriticalProductionFiles) {
            $SourceHashes[$Relative.Replace('\', '/')] = (Get-FileHash -LiteralPath $Destination -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    if ($RustCliPath) {
        $HelperDirectory = Join-Path $PluginDestination 'bin'
        New-Item -ItemType Directory -Path $HelperDirectory -Force | Out-Null
        Copy-Item -LiteralPath $RustCliPath -Destination (Join-Path $HelperDirectory 'modhunter-cli.exe')
        $InstalledArchiveTools = Join-Path $HelperDirectory 'archive-tools'
        New-Item -ItemType Directory -Path $InstalledArchiveTools -Force | Out-Null
        foreach ($Name in @('7z.exe', '7z.dll', 'License.txt', 'provenance.json')) {
            Copy-Item -LiteralPath (Join-Path $ArchiveToolsSource $Name) -Destination (Join-Path $InstalledArchiveTools $Name)
            if ((Get-FileHash -LiteralPath (Join-Path $InstalledArchiveTools $Name) -Algorithm SHA256).Hash -cne
                (Get-FileHash -LiteralPath (Join-Path $ArchiveToolsSource $Name) -Algorithm SHA256).Hash) {
                throw "Copied archive helper asset changed: $Name"
            }
        }
        $Summary.archiveTools.installedBesideCli = $true
    }
    $HelperDirectory = Join-Path $PluginDestination 'bin'
    New-Item -ItemType Directory -Path $HelperDirectory -Force | Out-Null
    Copy-Item -LiteralPath $QtBridgePath -Destination (Join-Path $HelperDirectory 'modhunter-qt-bridge.dll')
    $Summary['productionFileHashes'] = $SourceHashes
    Copy-Item -LiteralPath (Join-Path $HarnessSource 'game_modhunter_smoke.py') -Destination (Join-Path $BasicGames 'games/game_modhunter_smoke.py')
    $TestPlugin = Join-Path $PortableRoot 'plugins/modhunter_native_smoke'
    New-Item -ItemType Directory -Path $TestPlugin | Out-Null
    foreach ($Name in @('__init__.py', 'runner.py', 'audit_probe.py', 'content_probe.py')) {
        Copy-Item -LiteralPath (Join-Path $HarnessSource "modhunter_native_smoke/$Name") -Destination (Join-Path $TestPlugin $Name)
    }

    $Summary.phase = 'run-native-mo2'
    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $Executable.FullName
    $StartInfo.WorkingDirectory = $PortableRoot
    $StartInfo.UseShellExecute = $false
    $StartInfo.Environment['MODHUNTER_NATIVE_SMOKE'] = '1'
    $StartInfo.Environment['MODHUNTER_NATIVE_SMOKE_RESULT'] = $ReceiptPath
    $StartInfo.Environment['MODHUNTER_NATIVE_SMOKE_INSTANCE'] = $InstanceRoot
    $StartInfo.Environment['MODHUNTER_NATIVE_SMOKE_QT_BRIDGE_SHA256'] = $Summary.qtBridge.sha256
    if ($RustCliPath) { $StartInfo.Environment['MODHUNTER_NATIVE_SMOKE_RUST_CLI'] = $RustCliPath }
    else { $StartInfo.Environment.Remove('MODHUNTER_NATIVE_SMOKE_RUST_CLI') | Out-Null }
    if ($DesktopExecutable) {
        $StartInfo.Environment['MODHUNTER_LIVE_DESKTOP'] = [IO.Path]::GetFullPath($DesktopExecutable)
        $StartInfo.Environment['MODHUNTER_LIVE_DRIVER'] = Join-Path $PSScriptRoot 'verify_live_desktop.cjs'
    }
    $StartInfo.Environment['PYTHONNOUSERSITE'] = '1'
    $StartInfo.Environment.Remove('PYTHONHOME') | Out-Null
    $StartInfo.Environment.Remove('PYTHONPATH') | Out-Null
    $StartInfo.Environment.Remove('MODHUNTER_ARCHIVE_HELPER') | Out-Null
    foreach ($QtVariable in @('QT_PLUGIN_PATH', 'QML2_IMPORT_PATH', 'QML_IMPORT_PATH', 'QT_QPA_PLATFORM_PLUGIN_PATH', 'QTWEBENGINEPROCESS_PATH')) {
        $StartInfo.Environment.Remove($QtVariable) | Out-Null
    }
    $Process = [Diagnostics.Process]::Start($StartInfo)
    $Deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
    while (!(Test-Path -LiteralPath $ReceiptPath) -and !$Process.HasExited -and [DateTime]::UtcNow -lt $Deadline) {
        Start-Sleep -Milliseconds 250
        $Process.Refresh()
    }
    if (!(Test-Path -LiteralPath $ReceiptPath)) {
        $Process.Refresh()
        $ExitDescription = if ($Process.HasExited) { "exit code $($Process.ExitCode)" } else { 'deadline reached while MO2 was still running' }
        throw "The real MO2 plugin did not produce a native receipt: $ExitDescription. Inspect the captured MO2 logs."
    }
    $Receipt = ConvertFrom-ReceiptJson (Get-Content -LiteralPath $ReceiptPath -Raw)
    if (!$Receipt.ok -or !$Receipt.nativeMO2) { throw "Native MO2 checks failed: $($Receipt.error)" }
    $RequiredChecks = @('native_bridge_autostart', 'native_callback_subscriptions', 'native_plugin_initialized', 'native_nexus_gateway_created', 'native_nexus_qt_binding', 'native_ui_initialized',
        'native_profile_snapshot', 'loopback_health', 'protected_snapshot_authentication', 'queued_refresh',
        'queued_focus', 'native_dispatch_thread', 'native_view_selection', 'native_window_screenshot', 'compact_game_response')
    if ($RustCliPath) { $RequiredChecks += @('rust_cli_live_probe', 'rust_plugin_audit') }
    if ($DesktopExecutable) { $RequiredChecks += 'native_desktop_live_workflow' }
    foreach ($RequiredCheck in $RequiredChecks) {
        if (!(@($Receipt.checks | Where-Object { $_.name -eq $RequiredCheck -and $_.passed }).Count)) {
            throw "Native receipt omitted required passing check: $RequiredCheck"
        }
    }
    if (@($Receipt.checks | Where-Object { !$_.passed }).Count) { throw 'The native receipt contains failed checks.' }
    if (!$Process.WaitForExit(15000)) { throw 'MO2 produced a receipt but did not exit cleanly within 15 seconds.' }
    $Summary['nativeExitCode'] = $Process.ExitCode
    if ($Process.ExitCode -ne 0) { throw "MO2 exited with code $($Process.ExitCode) after the native checks." }
    $Summary['nativeReceipt'] = $Receipt
    $Summary.phase = 'completed'
    $Summary.ok = $true
}
catch {
    $Failure = $_
    $Summary['error'] = $_.Exception.Message
}
finally {
    if ($Process) {
        $Process.Refresh()
        if (!$Process.HasExited) {
            # Stop only the disposable process this script created. Never target
            # a user's separate MO2 session by executable name.
            Stop-Process -Id $Process.Id -ErrorAction SilentlyContinue
            $Process.WaitForExit(5000) | Out-Null
        }
        $Summary['nativeExitCode'] = if ($Process.HasExited) { $Process.ExitCode } else { $null }
    }
    $LogRoots = @((Join-Path $InstanceRoot 'logs'))
    if ($PortableRoot) { $LogRoots += Join-Path $PortableRoot 'logs' }
    $LogIndex = 0
    foreach ($LogRoot in $LogRoots) {
        if (Test-Path -LiteralPath $LogRoot) {
            $LogDestination = Join-Path $RunEvidence "mo2-logs-$LogIndex"
            Copy-Item -LiteralPath $LogRoot -Destination $LogDestination -Recurse -Force
        }
        $LogIndex++
    }
    $Summary['finishedAt'] = [DateTime]::UtcNow.ToString('o')
    $Summary['durationSeconds'] = [Math]::Round(([DateTime]::UtcNow - $StartedAt).TotalSeconds, 2)
    Write-Utf8File (Join-Path $RunEvidence 'result.json') ($Summary | ConvertTo-Json -Depth 20)
    Get-ChildItem -LiteralPath $RunEvidence | Copy-Item -Destination $EvidenceDirectory -Recurse -Force
    $Summary | ConvertTo-Json -Depth 20 | Write-Output
}
if ($Failure) { throw $Failure }
