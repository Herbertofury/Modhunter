#requires -Version 7.2
<#
.SYNOPSIS
Regression checks for preserving fallback plugin data during a Windows upgrade.
.DESCRIPTION
Uses only disposable fixture packages and inert x64 PE-marker ModOrganizer.exe files.
The actual installer is invoked by Windows PowerShell 5.1, matching its .bat
entry point. No fixture executable, Python plugin or real MO2 is launched.
#>
[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (!$IsWindows) { throw 'The plugin installer regression checks require Windows.' }
$WindowsPowerShell = Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe'
if (!(Test-Path -LiteralPath $WindowsPowerShell -PathType Leaf)) { throw 'Windows PowerShell 5.1 is required to test the shipped installer entry point.' }
$Root = Join-Path ([IO.Path]::GetTempPath()) ('modhunter-installer-tests-' + [guid]::NewGuid().ToString('N'))
$Package = Join-Path $Root 'package'
$Source = Join-Path $Package 'MO2/mo2_update_arsenal'
$Installer = Join-Path $Package 'Install_MO2_Plugin.ps1'
$Passed = $false
$Checks = [Collections.Generic.List[object]]::new()
$FixtureVersion = '1.2.3'

function Write-FixtureFile([string]$Path, [string]$Text) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}

function Require([bool]$Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
}

function Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }

function Write-FixtureManifest {
    $Lines = @(Get-ChildItem -LiteralPath $Package -File -Recurse | Where-Object { $_.Name -ne 'SHA256SUMS.txt' } | Sort-Object FullName | ForEach-Object {
        $Relative = [IO.Path]::GetRelativePath($Package, $_.FullName).Replace('\', '/')
        "$(Hash $_.FullName)  $Relative"
    })
    Write-FixtureFile (Join-Path $Package 'SHA256SUMS.txt') (($Lines -join "`n") + "`n")
}

function New-MO2Fixture([string]$Name) {
    $Directory = Join-Path $Root $Name
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $Bytes = [byte[]]::new(512)
    [Text.Encoding]::ASCII.GetBytes('MZ').CopyTo($Bytes, 0)
    [BitConverter]::GetBytes([uint32]128).CopyTo($Bytes, 60)
    ([byte[]]@(0x50, 0x45, 0, 0)).CopyTo($Bytes, 128)
    [BitConverter]::GetBytes([uint16]0x8664).CopyTo($Bytes, 132)
    [IO.File]::WriteAllBytes((Join-Path $Directory 'ModOrganizer.exe'), $Bytes)
    return $Directory
}

function Invoke-FixtureInstaller([string]$Directory, [bool]$MustSucceed = $true, [string]$ExpectedError = 'Refusing to preserve linked') {
    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $WindowsPowerShell
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    foreach ($Argument in @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $Installer, '-MO2Directory', $Directory)) {
        $StartInfo.ArgumentList.Add($Argument)
    }
    $Child = [Diagnostics.Process]::Start($StartInfo)
    try {
        $StandardOutput = $Child.StandardOutput.ReadToEndAsync()
        $StandardError = $Child.StandardError.ReadToEndAsync()
        if (!$Child.WaitForExit(30000)) { throw 'Fixture plugin installer exceeded its 30 second deadline.' }
        $Output = $StandardOutput.GetAwaiter().GetResult() + $StandardError.GetAwaiter().GetResult()
        $script:LastInstallerOutput = $Output
        if ($MustSucceed) { Require ($Child.ExitCode -eq 0) "Fixture installer failed: $Output" }
        else {
            Require ($Child.ExitCode -ne 0) 'Installer accepted an invalid upgrade fixture.'
            Require ($Output -match $ExpectedError) "Installer failed for an unrelated reason: $Output"
        }
    }
    finally {
        if (!$Child.HasExited) { Stop-Process -Id $Child.Id -ErrorAction SilentlyContinue }
        $Child.Dispose()
    }
}

function Assert-NoStaging([string]$Directory) {
    Require (@(Get-ChildItem -LiteralPath $Directory -Directory -Force -Filter '.modhunter-staging-*').Count -eq 0) 'Installer left a partial staging directory.'
}

function Fixture-Tree([string]$Directory) {
    return (@(Get-ChildItem -LiteralPath $Directory -Force -Recurse | Sort-Object FullName | ForEach-Object {
        $Relative = [IO.Path]::GetRelativePath($Directory, $_.FullName).Replace('\', '/')
        if ($_.PSIsContainer) { 'd:' + $Relative } else { 'f:' + $Relative + ':' + (Hash $_.FullName) }
    }) | ConvertTo-Json -Compress)
}

function Invoke-JsonInstaller([string]$Directory, [switch]$Preview, [string]$Fingerprint,
        [hashtable]$Identity = @{}, [string]$ExpectedCode = '', [switch]$OmitIdentity) {
    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $WindowsPowerShell
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    $StartInfo.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $StartInfo.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    foreach ($Argument in @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $Installer, '-Json')) {
        $StartInfo.ArgumentList.Add($Argument)
    }
    if ($Directory) { $StartInfo.ArgumentList.Add('-MO2Directory'); $StartInfo.ArgumentList.Add($Directory) }
    if ($Preview) { $StartInfo.ArgumentList.Add('-Preview') }
    if ($Fingerprint) { $StartInfo.ArgumentList.Add('-ExpectedInstalledFingerprint'); $StartInfo.ArgumentList.Add($Fingerprint) }
    if (!$OmitIdentity) {
        $ManifestHash = if (Test-Path -LiteralPath (Join-Path $Package 'SHA256SUMS.txt')) { Hash (Join-Path $Package 'SHA256SUMS.txt') } else { '0' * 64 }
        $Expected = @{ ExpectedVersion = $FixtureVersion; ExpectedCommit = ('a' * 40)
            ExpectedManifestSha256 = $ManifestHash }
        foreach ($Key in $Identity.Keys) { $Expected[$Key] = $Identity[$Key] }
        foreach ($Key in $Expected.Keys) { $StartInfo.ArgumentList.Add('-' + $Key); $StartInfo.ArgumentList.Add($Expected[$Key]) }
    }
    $Child = [Diagnostics.Process]::Start($StartInfo)
    try {
        $Stdout = $Child.StandardOutput.ReadToEndAsync()
        $Stderr = $Child.StandardError.ReadToEndAsync()
        if (!$Child.WaitForExit(30000)) { throw 'JSON installer exceeded its fixture deadline.' }
        $Text = $Stdout.GetAwaiter().GetResult()
        $Errors = $Stderr.GetAwaiter().GetResult()
        Require (!$Errors) "JSON installer wrote unexpected stderr: $Errors"
        Require ($Text.StartsWith('{', [StringComparison]::Ordinal) -and $Text.Length -lt 65536) "Installer did not return one bounded UTF-8 JSON result: $Text"
        $Result = $Text | ConvertFrom-Json
        Require ($Result.schemaVersion -eq 1) 'Installer JSON schema changed.'
        if ($ExpectedCode) {
            $ExpectedExit = if ($ExpectedCode -in @('install_failed', 'rollback_failed')) { 1 } else { 2 }
            Require (!$Result.ok -and $Result.status -eq 'failed' -and $Result.code -eq $ExpectedCode -and $Child.ExitCode -eq $ExpectedExit) "Unexpected installer refusal: $Text"
        } else { Require ($Result.ok -and $Child.ExitCode -eq 0) "JSON installer failed: $Text" }
        return $Result
    } finally {
        if (!$Child.HasExited) { Stop-Process -Id $Child.Id -ErrorAction SilentlyContinue }
        $Child.Dispose()
    }
}

try {
    New-Item -ItemType Directory -Path $Package -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '../Install_MO2_Plugin.ps1') -Destination $Installer
    Write-FixtureFile (Join-Path $Source '__init__.py') '# new fixture plugin'
    Write-FixtureFile (Join-Path $Source 'plugin.py') '# new fixture implementation'
    Write-FixtureFile (Join-Path $Source 'version.py') ('VERSION = "' + $FixtureVersion + '"')
    Write-FixtureFile (Join-Path $Source 'web/index.html') '<html>New fixture dashboard</html>'
    Write-FixtureFile (Join-Path $Source 'bin/modhunter-cli.exe') 'New Rust helper fixture marker; never execute this file.'
    Write-FixtureFile (Join-Path $Source 'bin/modhunter-qt-bridge.dll') 'New Qt helper fixture marker; never load this file.'
    $ArchiveNames = @('7z.exe', '7z.dll', 'License.txt', 'provenance.json')
    foreach ($Name in $ArchiveNames) {
        Write-FixtureFile (Join-Path $Source "bin/archive-tools/$Name") ("Packaged archive component fixture: " + $Name + '; never execute it.')
    }
    Write-FixtureFile (Join-Path $Source 'bin/archive-tools/unrelated.exe') 'Unrelated archive folder fixture; must not be installed.'

    Write-FixtureFile (Join-Path $Source 'bin/unrelated.exe') 'Unrelated fixture marker; must not be installed.'
    Write-FixtureFile (Join-Path $Source 'bin/Qt6Core.dll') 'SDK runtime fixture marker; MO2 owns its Qt runtime.'
    Write-FixtureFile (Join-Path $Package 'BUILD_INFO.json') (@{ version = $FixtureVersion; commit = ('a' * 40) } | ConvertTo-Json)
    Write-FixtureManifest

    $Upgrade = New-MO2Fixture 'upgrade'
    $Installed = Join-Path $Upgrade 'plugins/mo2_update_arsenal'
    Write-FixtureFile (Join-Path $Installed '__init__.py') '# previous fixture plugin'
    Write-FixtureFile (Join-Path $Installed 'previous-plugin-sentinel.txt') 'Keep the original plugin in its backup.'
    Write-FixtureFile (Join-Path $Installed 'web/index.html') '<html>Previous fixture dashboard</html>'
    Write-FixtureFile (Join-Path $Installed 'bin/modhunter-cli.exe') 'Previous Rust helper fixture marker; never execute this file.'
    Write-FixtureFile (Join-Path $Installed 'bin/modhunter-qt-bridge.dll') 'Previous Qt helper fixture marker; never load this file.'
    $OldHelperHash = Hash (Join-Path $Installed 'bin/modhunter-cli.exe')
    $OldQtHelperHash = Hash (Join-Path $Installed 'bin/modhunter-qt-bridge.dll')
    $OldArchiveHashes = @{}
    foreach ($Name in $ArchiveNames) {
        $OldPath = Join-Path $Installed "bin/archive-tools/$Name"
        Write-FixtureFile $OldPath ("Previous archive component fixture: " + $Name + '; never execute it.')
        $OldArchiveHashes[$Name] = Hash $OldPath
    }

    $DataFiles = @{
        'chronicle.json' = '{"profiles":{"fixture":{"score":4321,"progress":{"rows":{"one":{"done":true}}}}}}'
        'nexus-cache.json' = '{"fixture":"cached metadata"}'
        'update-operations.jsonl' = '{"fixture":"retained operation history"}'
        'profiles/[fixture]/progress.json' = '{"fixture":"nested profile data"}'
        '.hidden-state' = 'hidden fixture state'
    }
    $DataHashes = @{}
    foreach ($Relative in $DataFiles.Keys) {
        $Path = Join-Path (Join-Path $Installed 'data') $Relative
        Write-FixtureFile $Path $DataFiles[$Relative]
        $DataHashes[$Relative] = Hash $Path
    }
    $Hidden = Join-Path $Installed 'data/.hidden-state'
    [IO.File]::SetAttributes($Hidden, [IO.FileAttributes]::Hidden)
    New-Item -ItemType Directory -Path (Join-Path $Installed 'data/empty-directory') | Out-Null
    $OldPluginHash = Hash (Join-Path $Installed '__init__.py')
    Invoke-FixtureInstaller $Upgrade
    Require ($script:LastInstallerOutput.Contains("Installed Modhunter $FixtureVersion plugin")) "Upgrade did not report the exact packaged plugin version: $script:LastInstallerOutput"
    Require ((Hash (Join-Path $Installed '__init__.py')) -eq (Hash (Join-Path $Source '__init__.py'))) 'Upgrade did not replace the plugin implementation.'
    Require ((Hash (Join-Path $Installed 'web/index.html')) -eq (Hash (Join-Path $Source 'web/index.html'))) 'Upgrade did not replace the dashboard.'
    Require ((Hash (Join-Path $Installed 'bin/modhunter-cli.exe')) -eq (Hash (Join-Path $Source 'bin/modhunter-cli.exe'))) 'Upgrade did not install the packaged Rust audit helper.'
    Require ((Hash (Join-Path $Installed 'bin/modhunter-qt-bridge.dll')) -eq (Hash (Join-Path $Source 'bin/modhunter-qt-bridge.dll'))) 'Upgrade did not install the packaged Qt bridge helper.'
    Require (!(Test-Path -LiteralPath (Join-Path $Installed 'bin/unrelated.exe'))) 'Upgrade copied an unrelated executable.'
    foreach ($Name in $ArchiveNames) {
        Require ((Hash (Join-Path $Installed "bin/archive-tools/$Name")) -eq (Hash (Join-Path $Source "bin/archive-tools/$Name"))) "Upgrade lost or changed an archive component: $Name"
    }
    Require (!(Test-Path -LiteralPath (Join-Path $Installed 'bin/archive-tools/unrelated.exe'))) 'Upgrade copied an unrelated archive executable.'

    Require (!(Test-Path -LiteralPath (Join-Path $Installed 'bin/Qt6Core.dll'))) 'Upgrade copied an SDK Qt runtime into the plugin.'
    Require (!(Test-Path -LiteralPath (Join-Path $Installed 'previous-plugin-sentinel.txt'))) 'The replaced plugin still contains an obsolete implementation sentinel.'
    $Backups = @(Get-ChildItem -LiteralPath (Join-Path $Upgrade 'ModhunterBackups') -Directory)
    Require ($Backups.Count -eq 1) 'Upgrade did not retain exactly one previous-plugin backup.'
    $Backup = $Backups[0].FullName
    Require (!$Backup.StartsWith((Join-Path $Upgrade 'plugins') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) 'The previous plugin backup is still inside the active plugins directory.'
    Require ((Hash (Join-Path $Backup '__init__.py')) -eq $OldPluginHash) 'Upgrade changed the previous plugin before backing it up.'
    Require ((Hash (Join-Path $Backup 'bin/modhunter-cli.exe')) -eq $OldHelperHash) 'Upgrade changed or lost the previous Rust helper backup.'
    Require ((Hash (Join-Path $Backup 'bin/modhunter-qt-bridge.dll')) -eq $OldQtHelperHash) 'Upgrade changed or lost the previous Qt helper backup.'
    foreach ($Name in $ArchiveNames) {
        Require ((Hash (Join-Path $Backup "bin/archive-tools/$Name")) -eq $OldArchiveHashes[$Name]) "Upgrade changed or lost the previous archive component backup: $Name"
    }

    Require (Test-Path -LiteralPath (Join-Path $Backup 'previous-plugin-sentinel.txt')) 'The previous plugin backup lost its sentinel.'
    foreach ($Relative in $DataFiles.Keys) {
        Require ((Hash (Join-Path (Join-Path $Installed 'data') $Relative)) -eq $DataHashes[$Relative]) "Upgrade changed or lost active data: $Relative"
        Require ((Hash (Join-Path (Join-Path $Backup 'data') $Relative)) -eq $DataHashes[$Relative]) "Upgrade changed or lost backed-up data: $Relative"
    }
    Require (Test-Path -LiteralPath (Join-Path $Installed 'data/empty-directory') -PathType Container) 'Upgrade lost an empty data directory.'
    Assert-NoStaging $Upgrade
    $Checks.Add(@{ name = 'upgrade_preserves_active_and_backed_up_data'; passed = $true })

    $Fresh = New-MO2Fixture 'new-install'
    Invoke-FixtureInstaller $Fresh
    $FreshPlugin = Join-Path $Fresh 'plugins/mo2_update_arsenal'
    Require ((Hash (Join-Path $FreshPlugin 'plugin.py')) -eq (Hash (Join-Path $Source 'plugin.py'))) 'New installation did not copy the plugin.'
    Require ((Hash (Join-Path $FreshPlugin 'web/index.html')) -eq (Hash (Join-Path $Source 'web/index.html'))) 'New installation did not copy the dashboard.'
    Require ((Hash (Join-Path $FreshPlugin 'bin/modhunter-cli.exe')) -eq (Hash (Join-Path $Source 'bin/modhunter-cli.exe'))) 'New installation did not copy the Rust audit helper.'
    Require ((Hash (Join-Path $FreshPlugin 'bin/modhunter-qt-bridge.dll')) -eq (Hash (Join-Path $Source 'bin/modhunter-qt-bridge.dll'))) 'New installation did not copy the Qt bridge helper.'
    Require (!(Test-Path -LiteralPath (Join-Path $FreshPlugin 'bin/unrelated.exe'))) 'New installation copied an unrelated executable.'
    foreach ($Name in $ArchiveNames) {
        Require ((Hash (Join-Path $FreshPlugin "bin/archive-tools/$Name")) -eq (Hash (Join-Path $Source "bin/archive-tools/$Name"))) "New installation lost or changed an archive component: $Name"
    }
    Require (!(Test-Path -LiteralPath (Join-Path $FreshPlugin 'bin/archive-tools/unrelated.exe'))) 'New installation copied an unrelated archive executable.'

    Require (!(Test-Path -LiteralPath (Join-Path $Fresh 'ModhunterBackups'))) 'A new installation created an unnecessary previous-plugin backup.'
    Require (!(Test-Path -LiteralPath (Join-Path $FreshPlugin 'data'))) 'New installation included unrelated fixture progress.'
    Assert-NoStaging $Fresh
    $Checks.Add(@{ name = 'new_install_has_clean_state'; passed = $true })

    # A Windows PowerShell child may inherit a host's module search path, where
    # Get-FileHash is unavailable. Shadow it only in a disposable installer copy
    # so this compatibility check remains deterministic on every Windows runner.
    $InstallerScript = [IO.File]::ReadAllText($Installer)
    $ParameterDeclaration = '$ErrorActionPreference = ''Stop'''
    Require (([regex]::Matches($InstallerScript, [regex]::Escape($ParameterDeclaration))).Count -eq 1) 'Could not identify the installer initialization boundary for the missing-cmdlet fixture.'
    $PackagedInstaller = $Installer
    $Installer = Join-Path $Package 'Install_MO2_Plugin.no-filehash-fixture.ps1'
    $NoFileHash = New-MO2Fixture 'no-get-filehash-cmdlet'
    try {
        $Shadow = "function Get-FileHash { throw 'Get-FileHash is unavailable in this fixture.' }"
        Write-FixtureFile $Installer ($InstallerScript.Replace($ParameterDeclaration, $ParameterDeclaration + "`r`n" + $Shadow))
        Invoke-FixtureInstaller $NoFileHash
        foreach ($Relative in (@('__init__.py', 'plugin.py', 'version.py', 'web/index.html', 'bin/modhunter-cli.exe', 'bin/modhunter-qt-bridge.dll') + @($ArchiveNames | ForEach-Object { 'bin/archive-tools/' + $_ }))) {
            Require ((Hash (Join-Path $NoFileHash "plugins/mo2_update_arsenal/$Relative")) -eq (Hash (Join-Path $Source $Relative))) "Missing-cmdlet installation did not preserve exact packaged bytes: $Relative"
        }
        Assert-NoStaging $NoFileHash
    }
    finally {
        Remove-Item -LiteralPath $Installer -ErrorAction SilentlyContinue
        $Installer = $PackagedInstaller
    }
    $Checks.Add(@{ name = 'sha256_does_not_require_get_filehash_cmdlet'; passed = $true })

    $MissingHelper = New-MO2Fixture 'missing-qt-helper'
    $Previous = Join-Path $MissingHelper 'plugins/mo2_update_arsenal'
    Write-FixtureFile (Join-Path $Previous '__init__.py') '# unchanged previous plugin'
    $PreviousHash = Hash (Join-Path $Previous '__init__.py')
    $QtHelper = Join-Path $Source 'bin/modhunter-qt-bridge.dll'
    Move-Item -LiteralPath $QtHelper -Destination "$QtHelper.fixture-backup"
    try { Invoke-FixtureInstaller $MissingHelper $false 'native Qt bridge helper is missing' }
    finally { Move-Item -LiteralPath "$QtHelper.fixture-backup" -Destination $QtHelper }
    Require ((Hash (Join-Path $Previous '__init__.py')) -eq $PreviousHash) 'Missing-helper preflight changed the active plugin.'
    Require (!(Test-Path -LiteralPath (Join-Path $MissingHelper 'ModhunterBackups'))) 'Missing-helper preflight moved the active plugin.'
    Assert-NoStaging $MissingHelper
    $Checks.Add(@{ name = 'missing_qt_helper_rejected_without_mutation'; passed = $true })

    # Each corrupt/mixed package must fail before touching the previous plugin,
    # its progress, its backup directory or any installation staging directory.
    foreach ($Case in @('missing-manifest', 'damaged-helper', 'missing-python', 'unlisted-python', 'mixed-version', 'missing-archive-helper', 'damaged-archive-library', 'missing-archive-license', 'missing-archive-provenance', 'unlisted-archive-helper')) {
        $Fixture = New-MO2Fixture $Case
        $Previous = Join-Path $Fixture 'plugins/mo2_update_arsenal'
        Write-FixtureFile (Join-Path $Previous '__init__.py') '# unchanged previous plugin'
        Write-FixtureFile (Join-Path $Previous 'data/chronicle.json') '{"score":9876}'
        $PreviousHash = Hash (Join-Path $Previous '__init__.py')
        $ProgressHash = Hash (Join-Path $Previous 'data/chronicle.json')
        $Changed = switch ($Case) {
            'missing-manifest' { Join-Path $Package 'SHA256SUMS.txt' }
            'damaged-helper' { Join-Path $Source 'bin/modhunter-cli.exe' }
            'missing-python' { Join-Path $Source 'plugin.py' }
            'unlisted-python' { Join-Path $Source 'unlisted.py' }
            'mixed-version' { Join-Path $Package 'BUILD_INFO.json' }
            'missing-archive-helper' { Join-Path $Source 'bin/archive-tools/7z.exe' }
            'damaged-archive-library' { Join-Path $Source 'bin/archive-tools/7z.dll' }
            'missing-archive-license' { Join-Path $Source 'bin/archive-tools/License.txt' }
            'missing-archive-provenance' { Join-Path $Source 'bin/archive-tools/provenance.json' }
            'unlisted-archive-helper' { Join-Path $Package 'SHA256SUMS.txt' }

        }
        $Original = if (Test-Path -LiteralPath $Changed) { [IO.File]::ReadAllBytes($Changed) } else { $null }
        try {
            $ExpectedError = switch ($Case) {
                'missing-manifest' { Remove-Item -LiteralPath $Changed; 'package checksum manifest is missing' }
                'damaged-helper' { Write-FixtureFile $Changed 'incomplete helper'; 'checksum does not match' }
                'missing-python' { Remove-Item -LiteralPath $Changed; 'packaged plugin file is missing' }
                'unlisted-python' { Write-FixtureFile $Changed '# absent from the manifest'; 'outside its package checksum manifest' }
                'mixed-version' { Write-FixtureFile $Changed (@{ version = '0.0.0'; commit = ('a' * 40) } | ConvertTo-Json); Write-FixtureManifest; 'build and MO2 plugin versions do not match' }
                'missing-archive-helper' { Remove-Item -LiteralPath $Changed; 'complete archive helper is missing' }
                'damaged-archive-library' { Write-FixtureFile $Changed 'changed archive library'; 'checksum does not match' }
                'missing-archive-license' { Remove-Item -LiteralPath $Changed; 'complete archive helper is missing' }
                'missing-archive-provenance' { Remove-Item -LiteralPath $Changed; 'complete archive helper is missing' }
                'unlisted-archive-helper' {
                    $Lines = @([IO.File]::ReadAllLines($Changed) | Where-Object { $_ -notmatch '  MO2/mo2_update_arsenal/bin/archive-tools/7z\.exe$' })
                    Write-FixtureFile $Changed (($Lines -join "`n") + "`n")
                    'package checksum manifest omitted a required file'
                }

            }
            Invoke-FixtureInstaller $Fixture $false $ExpectedError
        }
        finally {
            if ($null -ne $Original) { [IO.File]::WriteAllBytes($Changed, $Original) }
            elseif (Test-Path -LiteralPath $Changed) { Remove-Item -LiteralPath $Changed }
            Write-FixtureManifest
        }
        Require ((Hash (Join-Path $Previous '__init__.py')) -eq $PreviousHash) "Rejected package changed the previous plugin: $Case"
        Require ((Hash (Join-Path $Previous 'data/chronicle.json')) -eq $ProgressHash) "Rejected package changed existing progress: $Case"
        Require (!(Test-Path -LiteralPath (Join-Path $Fixture 'ModhunterBackups'))) "Rejected package moved the previous plugin: $Case"
        Assert-NoStaging $Fixture
        $Checks.Add(@{ name = $Case + '_rejected_without_mutation'; passed = $true })
    }

    # Instrument only a disposable copy of the installer to change staging
    # after the final source copy, before its real validation. Production code
    # has no test hook, and every source file still passes the first preflight.
    $InstallerScript = [IO.File]::ReadAllText($Installer)
    $FinalCopy = '    # All component bytes are staged; validate before changing the installed plugin.'
    Require (([regex]::Matches($InstallerScript, [regex]::Escape($FinalCopy))).Count -eq 1) 'Could not identify the single staging-copy boundary for deterministic corruption.'
    foreach ($Case in @('damaged-staged-helper', 'damaged-staged-archive', 'unlisted-staged-python')) {
        $Fixture = New-MO2Fixture $Case
        $Previous = Join-Path $Fixture 'plugins/mo2_update_arsenal'
        Write-FixtureFile (Join-Path $Previous '__init__.py') '# unchanged previous plugin'
        Write-FixtureFile (Join-Path $Previous 'data/chronicle.json') '{"score":6789}'
        $PreviousHash = Hash (Join-Path $Previous '__init__.py')
        $ProgressHash = Hash (Join-Path $Previous 'data/chronicle.json')
        if ($Case -eq 'damaged-staged-helper') {
            $Injected = "[IO.File]::WriteAllText((Join-Path `$staging 'bin\modhunter-cli.exe'), 'Changed after copying; never execute this fixture.')"
            $ExpectedError = 'staged plugin checksum does not match'
        }
        elseif ($Case -eq 'damaged-staged-archive') {
            $Injected = "[IO.File]::WriteAllText((Join-Path `$staging 'bin\archive-tools\7z.dll'), 'Changed after copying; never load this fixture.')"
            $ExpectedError = 'staged plugin checksum does not match'
        }
        else {
            $Injected = "[IO.File]::WriteAllText((Join-Path `$staging 'unlisted.py'), '# Added after copying; never import this fixture.')"
            $ExpectedError = 'staged plugin contains a file outside its package checksum manifest'
        }
        $PackagedInstaller = $Installer
        $Installer = Join-Path $Package 'Install_MO2_Plugin.staging-fixture.ps1'
        try {
            Write-FixtureFile $Installer ($InstallerScript.Replace($FinalCopy, $FinalCopy + "`r`n    " + $Injected))
            Invoke-FixtureInstaller $Fixture $false $ExpectedError
        }
        finally {
            Remove-Item -LiteralPath $Installer -ErrorAction SilentlyContinue
            $Installer = $PackagedInstaller
        }
        Require ((Hash (Join-Path $Previous '__init__.py')) -eq $PreviousHash) "Rejected staging changed the previous plugin: $Case"
        Require ((Hash (Join-Path $Previous 'data/chronicle.json')) -eq $ProgressHash) "Rejected staging changed existing progress: $Case"
        Require (!(Test-Path -LiteralPath (Join-Path $Fixture 'ModhunterBackups'))) "Rejected staging moved the previous plugin: $Case"
        Assert-NoStaging $Fixture
        $Checks.Add(@{ name = $Case + '_rejected_without_mutation'; passed = $true })
    }

    # The archive helper must remain a real package directory. A junction
    # must be rejected before any installed bytes or progress are moved.
    $LinkedArchive = New-MO2Fixture 'linked-archive-tools'
    $Previous = Join-Path $LinkedArchive 'plugins/mo2_update_arsenal'
    Write-FixtureFile (Join-Path $Previous '__init__.py') '# unchanged previous plugin'
    $PreviousHash = Hash (Join-Path $Previous '__init__.py')
    $ArchiveDirectory = Join-Path $Source 'bin/archive-tools'
    $ArchiveSaved = Join-Path $Root 'original-archive-tools'
    $ArchiveExternal = Join-Path $Root 'external-archive-tools'
    Copy-Item -LiteralPath $ArchiveDirectory -Destination $ArchiveExternal -Recurse
    Move-Item -LiteralPath $ArchiveDirectory -Destination $ArchiveSaved
    try {
        New-Item -ItemType Junction -Path $ArchiveDirectory -Target $ArchiveExternal | Out-Null
        Invoke-FixtureInstaller $LinkedArchive $false 'linked archive helper directory'
        Require ((Hash (Join-Path $Previous '__init__.py')) -eq $PreviousHash) 'Linked archive rejection changed the previous plugin.'
        Require (!(Test-Path -LiteralPath (Join-Path $LinkedArchive 'ModhunterBackups'))) 'Linked archive rejection moved the previous plugin.'
        foreach ($Name in $ArchiveNames) {
            Require ((Hash (Join-Path $ArchiveExternal $Name)) -eq (Hash (Join-Path $ArchiveSaved $Name))) "Linked archive rejection changed external bytes: $Name"
        }
        Assert-NoStaging $LinkedArchive
    }
    finally {
        if (Test-Path -LiteralPath $ArchiveDirectory) { [IO.Directory]::Delete($ArchiveDirectory) }
        Move-Item -LiteralPath $ArchiveSaved -Destination $ArchiveDirectory
    }
    $Checks.Add(@{ name = 'linked_archive_tools_rejected_without_mutation'; passed = $true })

    $LinkedBackup = New-MO2Fixture 'linked-backup'
    $Previous = Join-Path $LinkedBackup 'plugins/mo2_update_arsenal'
    Write-FixtureFile (Join-Path $Previous '__init__.py') '# unchanged previous plugin'
    $PreviousHash = Hash (Join-Path $Previous '__init__.py')
    $ExternalBackup = Join-Path $Root 'external-backup-target'
    Write-FixtureFile (Join-Path $ExternalBackup 'sentinel.txt') 'unchanged external backup target'
    New-Item -ItemType Junction -Path (Join-Path $LinkedBackup 'ModhunterBackups') -Target $ExternalBackup | Out-Null
    Invoke-FixtureInstaller $LinkedBackup $false 'Refusing to use a linked backup directory'
    Require ((Hash (Join-Path $Previous '__init__.py')) -eq $PreviousHash) 'Linked-backup rejection moved or changed the previous plugin.'
    Require (@(Get-ChildItem -LiteralPath $ExternalBackup -Force).Count -eq 1) 'Linked-backup rejection wrote to the external target.'
    Assert-NoStaging $LinkedBackup
    $Checks.Add(@{ name = 'linked_backup_rejected_without_mutation'; passed = $true })

    $SourceTree = Join-Path $Root 'source-tree'
    New-Item -ItemType Directory -Path (Join-Path $SourceTree 'native') -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination (Join-Path $SourceTree 'mo2_update_arsenal') -Recurse
    $PackagedInstaller = $Installer
    $Installer = Join-Path $SourceTree 'native/Install_MO2_Plugin.ps1'
    Copy-Item -LiteralPath $PackagedInstaller -Destination $Installer
    try {
        $DeveloperMO2 = New-MO2Fixture 'source-tree-install'
        Invoke-FixtureInstaller $DeveloperMO2
        Require ((Hash (Join-Path $DeveloperMO2 'plugins/mo2_update_arsenal/plugin.py')) -eq (Hash (Join-Path $Source 'plugin.py'))) 'Documented source-tree installation stopped working.'
        foreach ($Name in $ArchiveNames) {
            Require ((Hash (Join-Path $DeveloperMO2 "plugins/mo2_update_arsenal/bin/archive-tools/$Name")) -eq (Hash (Join-Path $Source "bin/archive-tools/$Name"))) "Source-tree installation lost an archive component: $Name"
        }

        Assert-NoStaging $DeveloperMO2
    }
    finally { $Installer = $PackagedInstaller }
    $Checks.Add(@{ name = 'source_tree_install_with_compiled_helpers'; passed = $true })

    foreach ($Case in @('linked-data-root', 'linked-data-child')) {
        $Fixture = New-MO2Fixture $Case
        $Previous = Join-Path $Fixture 'plugins/mo2_update_arsenal'
        Write-FixtureFile (Join-Path $Previous '__init__.py') '# unchanged previous plugin'
        $PreviousHash = Hash (Join-Path $Previous '__init__.py')
        $External = Join-Path $Fixture 'external-fixture-data'
        Write-FixtureFile (Join-Path $External 'sentinel.json') '{"fixture":"outside linked data; must remain unchanged"}'
        $ExternalHash = Hash (Join-Path $External 'sentinel.json')
        $Link = Join-Path $Previous 'data'
        if ($Case -eq 'linked-data-child') {
            Write-FixtureFile (Join-Path $Previous 'data/chronicle.json') '{"fixture":"original progress"}'
            $OriginalProgressHash = Hash (Join-Path $Previous 'data/chronicle.json')
            $Link = Join-Path $Previous 'data/linked-cache'
        }
        New-Item -ItemType Junction -Path $Link -Target $External | Out-Null
        Invoke-FixtureInstaller $Fixture $false
        Require ((Hash (Join-Path $Previous '__init__.py')) -eq $PreviousHash) 'Rejected upgrade changed the active plugin.'
        Require ((Hash (Join-Path $External 'sentinel.json')) -eq $ExternalHash) 'Rejected upgrade changed the linked fixture target.'
        Require (!(Test-Path -LiteralPath (Join-Path $Fixture 'ModhunterBackups'))) 'Rejected upgrade moved the existing plugin into a backup.'
        Require ([bool]((Get-Item -LiteralPath $Link -Force).Attributes -band [IO.FileAttributes]::ReparsePoint)) 'Rejected upgrade changed the original link.'
        if ($Case -eq 'linked-data-child') {
            Require ((Hash (Join-Path $Previous 'data/chronicle.json')) -eq $OriginalProgressHash) 'Rejected upgrade changed the original progress.'
        }
        Assert-NoStaging $Fixture
        $Checks.Add(@{ name = $Case + '_rejected_without_mutation'; passed = $true })
    }
    # The nested desktop resource contains exactly the files the installer
    # owns, rather than the development sources retained in the legacy ZIP.
    $JsonPackage = Join-Path $Root 'json-package'
    New-Item -ItemType Directory -Path (Join-Path $JsonPackage 'MO2') -Force | Out-Null
    Copy-Item -LiteralPath $Source -Destination (Join-Path $JsonPackage 'MO2/mo2_update_arsenal') -Recurse
    Copy-Item -LiteralPath $Installer -Destination (Join-Path $JsonPackage 'Install_MO2_Plugin.ps1')
    Copy-Item -LiteralPath (Join-Path $Package 'BUILD_INFO.json') -Destination $JsonPackage
    $Package = $JsonPackage
    $Source = Join-Path $Package 'MO2/mo2_update_arsenal'
    $Installer = Join-Path $Package 'Install_MO2_Plugin.ps1'
    foreach ($Relative in @('bin/unrelated.exe', 'bin/Qt6Core.dll', 'bin/archive-tools/unrelated.exe')) {
        Remove-Item -LiteralPath (Join-Path $Source $Relative)
    }
    Write-FixtureManifest
    $OriginalJsonScript = [IO.File]::ReadAllText($Installer)
    $JsonFresh = New-MO2Fixture 'JSON fresh 日本語 [selected]'
    $BeforeTarget = Fixture-Tree $JsonFresh
    $BeforePackage = Fixture-Tree $Package
    $PreviewResult = Invoke-JsonInstaller -Directory $JsonFresh -Preview
    Require ($PreviewResult.status -eq 'preview' -and $PreviewResult.code -eq 'ready' -and $PreviewResult.installedFingerprint -match '^[0-9a-f]{64}$') 'Preview omitted its reviewed target fingerprint.'
    Require ($PreviewResult.targetDirectory -ceq $JsonFresh -and $PreviewResult.backupRoot -ceq (Join-Path $JsonFresh 'ModhunterBackups')) 'Preview changed the selected Unicode target.'
    Require ((Fixture-Tree $JsonFresh) -eq $BeforeTarget -and (Fixture-Tree $Package) -eq $BeforePackage) 'Read-only preview changed the target or package.'
    $InstalledResult = Invoke-JsonInstaller -Directory $JsonFresh -Fingerprint $PreviewResult.installedFingerprint
    Require ($InstalledResult.status -eq 'installed' -and !$InstalledResult.backupDirectory -and !$InstalledResult.preservedData) 'Fresh JSON installation reported an upgrade.'
    Require ((Fixture-Tree $Package) -eq $BeforePackage) 'JSON installation changed the verified resource bundle.'
    Assert-NoStaging $JsonFresh
    $Checks.Add(@{ name = 'json_preview_is_read_only_and_unicode_fresh_install_matches'; passed = $true })

    $JsonPlugin = Join-Path $JsonFresh 'plugins/mo2_update_arsenal'
    Write-FixtureFile (Join-Path $JsonPlugin 'data/chronicle.json') '{"progress":12345}'
    New-Item -ItemType Directory -Path (Join-Path $JsonPlugin 'data/empty-directory') | Out-Null
    $OldTree = Fixture-Tree $JsonPlugin
    $PreviewResult = Invoke-JsonInstaller -Directory $JsonFresh -Preview
    Require ($PreviewResult.previousVersion -eq $FixtureVersion) 'Preview did not read the current installed version.'
    $Updated = Invoke-JsonInstaller -Directory $JsonFresh -Fingerprint $PreviewResult.installedFingerprint
    Require ($Updated.status -eq 'updated' -and $Updated.preservedData -and $Updated.backupDirectory -and $Updated.rollback -eq 'not_needed') 'JSON upgrade omitted its progress or backup result.'
    Require ((Fixture-Tree $Updated.backupDirectory) -eq $OldTree -and (Fixture-Tree $JsonPlugin) -eq $OldTree) 'JSON upgrade changed backed-up or active plugin progress.'
    $Checks.Add(@{ name = 'json_upgrade_preserves_complete_progress_and_backup'; passed = $true })

    $PreviewResult = Invoke-JsonInstaller -Directory $JsonFresh -Preview
    Write-FixtureFile (Join-Path $JsonPlugin 'data/chronicle.json') '{"progress":54321}'
    $ChangedTree = Fixture-Tree $JsonFresh
    Invoke-JsonInstaller -Directory $JsonFresh -Fingerprint $PreviewResult.installedFingerprint -ExpectedCode 'target_changed' | Out-Null
    Require ((Fixture-Tree $JsonFresh) -eq $ChangedTree) 'Stale-preview refusal modified the target.'
    $Checks.Add(@{ name = 'json_stale_preview_rejects_progress_change_without_mutation'; passed = $true })
    $NormalPreview = Invoke-JsonInstaller -Directory $JsonFresh -Preview
    $ExtendedPreview = Invoke-JsonInstaller -Directory ('\\?\' + $JsonFresh) -Preview
    Require ($ExtendedPreview.targetDirectory -ceq $JsonFresh -and $ExtendedPreview.installedFingerprint -eq $NormalPreview.installedFingerprint) 'Rust canonical Windows paths did not resolve to the same reviewed target.'
    $Checks.Add(@{ name = 'json_rust_canonical_drive_path_matches_normal_target'; passed = $true })
    foreach ($Case in @('unknown', 'x86', 'arm64')) {
        $Unsupported = New-MO2Fixture ('json-unsupported-' + $Case)
        $Executable = Join-Path $Unsupported 'ModOrganizer.exe'
        if ($Case -eq 'unknown') { Write-FixtureFile $Executable 'Not a supported executable header; never run.' }
        else {
            $Bytes = [IO.File]::ReadAllBytes($Executable)
            $Machine = if ($Case -eq 'x86') { [uint16]0x014c } else { [uint16]0xaa64 }
            [BitConverter]::GetBytes($Machine).CopyTo($Bytes, 132)
            [IO.File]::WriteAllBytes($Executable, $Bytes)
        }
        $BeforeUnsupported = Fixture-Tree $Unsupported
        Invoke-JsonInstaller -Directory $Unsupported -Preview -ExpectedCode 'unsupported_target' | Out-Null
        Invoke-JsonInstaller -Directory $Unsupported -Fingerprint ('0' * 64) -ExpectedCode 'unsupported_target' | Out-Null
        Require ((Fixture-Tree $Unsupported) -eq $BeforeUnsupported) "Unsupported executable refusal changed its target: $Case"
        $Checks.Add(@{ name = 'json_' + $Case + '_target_refused_without_mutation'; passed = $true })
    }

    foreach ($Case in @('no-target', 'no-identity', 'no-fingerprint', 'wrong-version', 'wrong-commit', 'wrong-manifest')) {
        $BeforeTarget = Fixture-Tree $JsonFresh
        switch ($Case) {
            'no-target' { Invoke-JsonInstaller -Preview -ExpectedCode 'invalid_request' | Out-Null }
            'no-identity' { Invoke-JsonInstaller -Directory $JsonFresh -Preview -OmitIdentity -ExpectedCode 'invalid_request' | Out-Null }
            'no-fingerprint' { Invoke-JsonInstaller -Directory $JsonFresh -ExpectedCode 'invalid_request' | Out-Null }
            'wrong-version' { Invoke-JsonInstaller -Directory $JsonFresh -Preview -Identity @{ExpectedVersion='0.0.0'} -ExpectedCode 'package_changed' | Out-Null }
            'wrong-commit' { Invoke-JsonInstaller -Directory $JsonFresh -Preview -Identity @{ExpectedCommit=('b' * 40)} -ExpectedCode 'package_changed' | Out-Null }
            'wrong-manifest' { Invoke-JsonInstaller -Directory $JsonFresh -Preview -Identity @{ExpectedManifestSha256=('0' * 64)} -ExpectedCode 'package_changed' | Out-Null }
        }
        Require ((Fixture-Tree $JsonFresh) -eq $BeforeTarget) "JSON refusal changed its target: $Case"
        $Checks.Add(@{ name = 'json_' + $Case + '_rejected_without_mutation'; passed = $true })
    }

    $PackageBytes = @{}
    foreach ($File in (Get-ChildItem -LiteralPath $Package -File -Recurse)) { $PackageBytes[$File.FullName] = [IO.File]::ReadAllBytes($File.FullName) }
    foreach ($Case in @('missing-manifest', 'damaged-helper', 'changed-script', 'mixed-build', 'unlisted-file', 'duplicate-path', 'unsafe-manifest-path', 'oversized-version')) {
        $BeforeTarget = Fixture-Tree $JsonFresh
        $Extra = Join-Path $Package 'unlisted.txt'
        try {
            switch ($Case) {
                'missing-manifest' { Remove-Item -LiteralPath (Join-Path $Package 'SHA256SUMS.txt') }
                'damaged-helper' { Write-FixtureFile (Join-Path $Source 'bin/modhunter-cli.exe') 'Corrupted inert helper' }
                'changed-script' { Write-FixtureFile $Installer ($OriginalJsonScript + "`n# Unmanifested script mutation`n") }
                'mixed-build' {
                    Write-FixtureFile (Join-Path $Package 'BUILD_INFO.json') (@{version='0.0.0';commit=('a' * 40)} | ConvertTo-Json)
                    Write-FixtureManifest
                }
                'unlisted-file' { Write-FixtureFile $Extra 'Unmanifested fixture data' }
                'duplicate-path' {
                    $Manifest = Join-Path $Package 'SHA256SUMS.txt'
                    $Line = ([IO.File]::ReadAllLines($Manifest))[0]
                    [IO.File]::AppendAllText($Manifest, $Line + "`n", [Text.UTF8Encoding]::new($false))
                }
                'unsafe-manifest-path' {
                    [IO.File]::AppendAllText((Join-Path $Package 'SHA256SUMS.txt'), ('0' * 64) + "  ../outside`n", [Text.UTF8Encoding]::new($false))
                }
                'oversized-version' { Write-FixtureFile (Join-Path $Source 'version.py') ('#' * 65537) }
            }
            Invoke-JsonInstaller -Directory $JsonFresh -Preview -ExpectedCode 'package_invalid' | Out-Null
            Require ((Fixture-Tree $JsonFresh) -eq $BeforeTarget) "JSON package rejection modified its target: $Case"
        } finally {
            if (Test-Path -LiteralPath $Extra) { Remove-Item -LiteralPath $Extra }
            foreach ($Path in $PackageBytes.Keys) { [IO.File]::WriteAllBytes($Path, $PackageBytes[$Path]) }
        }
        $Checks.Add(@{ name = 'json_' + $Case + '_package_refused_without_mutation'; passed = $true })
    }

    $LinkedTarget = Join-Path $Root 'linked-selected-root'
    New-Item -ItemType Junction -Path $LinkedTarget -Target $JsonFresh | Out-Null
    try {
        $BeforeTarget = Fixture-Tree $JsonFresh
        Invoke-JsonInstaller -Directory $LinkedTarget -Preview -ExpectedCode 'unsafe_path' | Out-Null
        Require ((Fixture-Tree $JsonFresh) -eq $BeforeTarget) 'Linked target preview mutated the real target.'
    } finally { [IO.Directory]::Delete($LinkedTarget) }
    $Checks.Add(@{ name = 'json_linked_target_root_rejected_without_mutation'; passed = $true })

    $Sha = [Security.Cryptography.SHA256]::Create()
    try { $LockDigest = [BitConverter]::ToString($Sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($JsonFresh.ToUpperInvariant()))).Replace('-', '').ToLowerInvariant() }
    finally { $Sha.Dispose() }
    $Held = [Threading.Mutex]::new($false, ('Local\ModhunterPlugin-' + $LockDigest))
    Require ($Held.WaitOne(0)) 'Could not acquire the disposable fixture lock.'
    try { Invoke-JsonInstaller -Directory $JsonFresh -Preview -ExpectedCode 'target_busy' | Out-Null }
    finally { $Held.ReleaseMutex(); $Held.Dispose() }
    $Checks.Add(@{ name = 'json_same_target_installers_are_excluded'; passed = $true })

    # Simulated process observations use real opened fixture paths. No inert
    # ModOrganizer.exe marker is ever executed. Instrument only this copy.
    $Unrelated = New-MO2Fixture 'known-unrelated-running-mo2'
    $Boundary = '$ErrorActionPreference = ''Stop'''
    foreach ($Case in @('selected-process', 'unknown-process', 'process-query-failure', 'unrelated-process', 'started-before-commit')) {
        $Fixture = New-MO2Fixture ('json-' + $Case)
        $ObservedPath = if ($Case -eq 'unrelated-process') { Join-Path $Unrelated 'ModOrganizer.exe' } else { Join-Path $Fixture 'ModOrganizer.exe' }
        $PathExpression = if ($Case -eq 'unknown-process') { '$null' } else { "'" + $ObservedPath.Replace("'", "''") + "'" }
        $Guard = if ($Case -eq 'started-before-commit') { 'if (!$script:FixtureProcessProbe) { $script:FixtureProcessProbe = 1; return };' } else { '' }
        $Injected = '$script:FixtureProcessProbe = 0; function Get-Process { param($Name, $ErrorAction) ' + $Guard + ' [pscustomobject]@{ Path = ' + $PathExpression + '; Id = 12345 } }'
        if ($Case -eq 'process-query-failure') { $Injected = 'function Get-Process { param($Name, $ErrorAction) throw ''Fixture process enumeration failed.'' }' }
        try {
            Write-FixtureFile $Installer ($OriginalJsonScript.Replace($Boundary, $Boundary + "`n" + $Injected))
            Write-FixtureManifest
            if ($Case -eq 'selected-process') { Invoke-JsonInstaller -Directory $Fixture -Preview -ExpectedCode 'mo2_running' | Out-Null }
            elseif ($Case -in @('unknown-process', 'process-query-failure')) { Invoke-JsonInstaller -Directory $Fixture -Preview -ExpectedCode 'process_ambiguous' | Out-Null }
            else {
                $Reviewed = Invoke-JsonInstaller -Directory $Fixture -Preview
                if ($Case -eq 'unrelated-process') {
                    $Result = Invoke-JsonInstaller -Directory $Fixture -Fingerprint $Reviewed.installedFingerprint
                    Require ($Result.status -eq 'installed') 'A known unrelated MO2 process blocked the selected application.'
                } else {
                    Invoke-JsonInstaller -Directory $Fixture -Fingerprint $Reviewed.installedFingerprint -ExpectedCode 'mo2_running' | Out-Null
                    Require (!(Test-Path -LiteralPath (Join-Path $Fixture 'plugins/mo2_update_arsenal'))) 'The late process check allowed replacement.'
                    Assert-NoStaging $Fixture
                }
            }
        } finally { Write-FixtureFile $Installer $OriginalJsonScript; Write-FixtureManifest }
        $Checks.Add(@{ name = 'json_' + $Case + '_process_guard'; passed = $true })
    }

    $RollbackFixture = New-MO2Fixture 'json-rollback'
    $RollbackPlugin = Join-Path $RollbackFixture 'plugins/mo2_update_arsenal'
    Write-FixtureFile (Join-Path $RollbackPlugin '__init__.py') '# original rollback fixture'
    Write-FixtureFile (Join-Path $RollbackPlugin 'data/progress.json') '{"preserve":true}'
    $OriginalTree = Fixture-Tree $RollbackPlugin
    $Injected = @'
function Move-Item {
    param([string]$LiteralPath, [string]$Destination)
    if ([IO.Path]::GetFileName($LiteralPath).StartsWith('.modhunter-staging-', [StringComparison]::Ordinal)) { throw 'Fixture replacement failure.' }
    Microsoft.PowerShell.Management\Move-Item -LiteralPath $LiteralPath -Destination $Destination
}
'@
    try {
        Write-FixtureFile $Installer ($OriginalJsonScript.Replace($Boundary, $Boundary + "`n" + $Injected))
        Write-FixtureManifest
        $Reviewed = Invoke-JsonInstaller -Directory $RollbackFixture -Preview
        $Failure = Invoke-JsonInstaller -Directory $RollbackFixture -Fingerprint $Reviewed.installedFingerprint -ExpectedCode 'install_failed'
        Require ($Failure.rollback -eq 'restored' -and (Fixture-Tree $RollbackPlugin) -eq $OriginalTree) 'Failed JSON replacement did not restore all prior data.'
        Assert-NoStaging $RollbackFixture
    } finally { Write-FixtureFile $Installer $OriginalJsonScript; Write-FixtureManifest }
    $Checks.Add(@{ name = 'json_replacement_failure_restores_original_plugin_and_progress'; passed = $true })
    $Passed = $true
    @{ ok = $true; test = 'windows-plugin-installer'; runtime = 'Windows PowerShell 5.1'; checks = $Checks.ToArray() } | ConvertTo-Json -Depth 6
}
finally {
    if ($Passed) { Remove-Item -LiteralPath $Root -Recurse -Force }
    else { Write-Host "Installer regression fixtures retained for diagnosis: $Root" }
}
