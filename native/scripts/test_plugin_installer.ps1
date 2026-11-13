#requires -Version 7.2
<#
.SYNOPSIS
Regression checks for preserving fallback plugin data during a Windows upgrade.
.DESCRIPTION
Uses only disposable fixture packages and text-marker ModOrganizer.exe files.
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
    Write-FixtureFile (Join-Path $Directory 'ModOrganizer.exe') 'Text fixture marker; never execute this file.'
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

try {
    New-Item -ItemType Directory -Path $Package -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot '../Install_MO2_Plugin.ps1') -Destination $Installer
    Write-FixtureFile (Join-Path $Source '__init__.py') '# new fixture plugin'
    Write-FixtureFile (Join-Path $Source 'plugin.py') '# new fixture implementation'
    Write-FixtureFile (Join-Path $Source 'version.py') ('VERSION = "' + $FixtureVersion + '"')
    Write-FixtureFile (Join-Path $Source 'web/index.html') '<html>New fixture dashboard</html>'
    Write-FixtureFile (Join-Path $Source 'bin/modhunter-cli.exe') 'New Rust helper fixture marker; never execute this file.'
    Write-FixtureFile (Join-Path $Source 'bin/modhunter-qt-bridge.dll') 'New Qt helper fixture marker; never load this file.'
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
    Require ($script:LastInstallerOutput.Contains("Installed Modhunter $FixtureVersion plugin")) 'Upgrade did not report the exact packaged plugin version.'
    Require ((Hash (Join-Path $Installed '__init__.py')) -eq (Hash (Join-Path $Source '__init__.py'))) 'Upgrade did not replace the plugin implementation.'
    Require ((Hash (Join-Path $Installed 'web/index.html')) -eq (Hash (Join-Path $Source 'web/index.html'))) 'Upgrade did not replace the dashboard.'
    Require ((Hash (Join-Path $Installed 'bin/modhunter-cli.exe')) -eq (Hash (Join-Path $Source 'bin/modhunter-cli.exe'))) 'Upgrade did not install the packaged Rust audit helper.'
    Require ((Hash (Join-Path $Installed 'bin/modhunter-qt-bridge.dll')) -eq (Hash (Join-Path $Source 'bin/modhunter-qt-bridge.dll'))) 'Upgrade did not install the packaged Qt bridge helper.'
    Require (!(Test-Path -LiteralPath (Join-Path $Installed 'bin/unrelated.exe'))) 'Upgrade copied an unrelated executable.'
    Require (!(Test-Path -LiteralPath (Join-Path $Installed 'bin/Qt6Core.dll'))) 'Upgrade copied an SDK Qt runtime into the plugin.'
    Require (!(Test-Path -LiteralPath (Join-Path $Installed 'previous-plugin-sentinel.txt'))) 'The replaced plugin still contains an obsolete implementation sentinel.'
    $Backups = @(Get-ChildItem -LiteralPath (Join-Path $Upgrade 'ModhunterBackups') -Directory)
    Require ($Backups.Count -eq 1) 'Upgrade did not retain exactly one previous-plugin backup.'
    $Backup = $Backups[0].FullName
    Require (!$Backup.StartsWith((Join-Path $Upgrade 'plugins') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) 'The previous plugin backup is still inside the active plugins directory.'
    Require ((Hash (Join-Path $Backup '__init__.py')) -eq $OldPluginHash) 'Upgrade changed the previous plugin before backing it up.'
    Require ((Hash (Join-Path $Backup 'bin/modhunter-cli.exe')) -eq $OldHelperHash) 'Upgrade changed or lost the previous Rust helper backup.'
    Require ((Hash (Join-Path $Backup 'bin/modhunter-qt-bridge.dll')) -eq $OldQtHelperHash) 'Upgrade changed or lost the previous Qt helper backup.'
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
    Require (!(Test-Path -LiteralPath (Join-Path $Fresh 'ModhunterBackups'))) 'A new installation created an unnecessary previous-plugin backup.'
    Require (!(Test-Path -LiteralPath (Join-Path $FreshPlugin 'data'))) 'New installation included unrelated fixture progress.'
    Assert-NoStaging $Fresh
    $Checks.Add(@{ name = 'new_install_has_clean_state'; passed = $true })

    # A Windows PowerShell child may inherit a host's module search path, where
    # Get-FileHash is unavailable. Shadow it only in a disposable installer copy
    # so this compatibility check remains deterministic on every Windows runner.
    $InstallerScript = [IO.File]::ReadAllText($Installer)
    $ParameterDeclaration = 'param([string]$MO2Directory)'
    Require ($InstallerScript.StartsWith($ParameterDeclaration, [StringComparison]::Ordinal)) 'Could not identify the installer parameter declaration for the missing-cmdlet fixture.'
    $PackagedInstaller = $Installer
    $Installer = Join-Path $Package 'Install_MO2_Plugin.no-filehash-fixture.ps1'
    $NoFileHash = New-MO2Fixture 'no-get-filehash-cmdlet'
    try {
        $Shadow = "function Get-FileHash { throw 'Get-FileHash is unavailable in this fixture.' }"
        Write-FixtureFile $Installer ($InstallerScript.Replace($ParameterDeclaration, $ParameterDeclaration + "`r`n" + $Shadow))
        Invoke-FixtureInstaller $NoFileHash
        foreach ($Relative in @('__init__.py', 'plugin.py', 'version.py', 'web/index.html', 'bin/modhunter-cli.exe', 'bin/modhunter-qt-bridge.dll')) {
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
    foreach ($Case in @('missing-manifest', 'damaged-helper', 'missing-python', 'unlisted-python', 'mixed-version')) {
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
        }
        $Original = if (Test-Path -LiteralPath $Changed) { [IO.File]::ReadAllBytes($Changed) } else { $null }
        try {
            $ExpectedError = switch ($Case) {
                'missing-manifest' { Remove-Item -LiteralPath $Changed; 'package checksum manifest is missing' }
                'damaged-helper' { Write-FixtureFile $Changed 'incomplete helper'; 'checksum does not match' }
                'missing-python' { Remove-Item -LiteralPath $Changed; 'packaged plugin file is missing' }
                'unlisted-python' { Write-FixtureFile $Changed '# absent from the manifest'; 'outside its package checksum manifest' }
                'mixed-version' { Write-FixtureFile $Changed (@{ version = '0.0.0'; commit = ('a' * 40) } | ConvertTo-Json); Write-FixtureManifest; 'build and MO2 plugin versions do not match' }
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
    $FinalCopy = "    Copy-Item -LiteralPath `$qtHelper -Destination (Join-Path `$staging 'bin\modhunter-qt-bridge.dll')"
    Require (([regex]::Matches($InstallerScript, [regex]::Escape($FinalCopy))).Count -eq 1) 'Could not identify the single staging-copy boundary for deterministic corruption.'
    foreach ($Case in @('damaged-staged-helper', 'unlisted-staged-python')) {
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
    $Passed = $true
    @{ ok = $true; test = 'windows-plugin-installer'; runtime = 'Windows PowerShell 5.1'; checks = $Checks.ToArray() } | ConvertTo-Json -Depth 6
}
finally {
    if ($Passed) { Remove-Item -LiteralPath $Root -Recurse -Force }
    else { Write-Host "Installer regression fixtures retained for diagnosis: $Root" }
}
