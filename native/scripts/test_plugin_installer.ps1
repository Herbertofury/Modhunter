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

function Write-FixtureFile([string]$Path, [string]$Text) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}

function Require([bool]$Condition, [string]$Message) {
    if (!$Condition) { throw $Message }
}

function Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash }

function New-MO2Fixture([string]$Name) {
    $Directory = Join-Path $Root $Name
    Write-FixtureFile (Join-Path $Directory 'ModOrganizer.exe') 'Text fixture marker; never execute this file.'
    return $Directory
}

function Invoke-FixtureInstaller([string]$Directory, [bool]$MustSucceed = $true) {
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
        if ($MustSucceed) { Require ($Child.ExitCode -eq 0) "Fixture installer failed: $Output" }
        else {
            Require ($Child.ExitCode -ne 0) 'Installer accepted a linked data directory.'
            Require ($Output -match 'Refusing to preserve linked') "Installer failed for an unrelated reason: $Output"
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
    Write-FixtureFile (Join-Path $Source 'web/index.html') '<html>New fixture dashboard</html>'
    Write-FixtureFile (Join-Path $Source 'bin/modhunter-cli.exe') 'New Rust helper fixture marker; never execute this file.'
    Write-FixtureFile (Join-Path $Source 'bin/unrelated.exe') 'Unrelated fixture marker; must not be installed.'

    $Upgrade = New-MO2Fixture 'upgrade'
    $Installed = Join-Path $Upgrade 'plugins/mo2_update_arsenal'
    Write-FixtureFile (Join-Path $Installed '__init__.py') '# previous fixture plugin'
    Write-FixtureFile (Join-Path $Installed 'previous-plugin-sentinel.txt') 'Keep the original plugin in its backup.'
    Write-FixtureFile (Join-Path $Installed 'web/index.html') '<html>Previous fixture dashboard</html>'
    Write-FixtureFile (Join-Path $Installed 'bin/modhunter-cli.exe') 'Previous Rust helper fixture marker; never execute this file.'
    $OldHelperHash = Hash (Join-Path $Installed 'bin/modhunter-cli.exe')
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
    Require ((Hash (Join-Path $Installed '__init__.py')) -eq (Hash (Join-Path $Source '__init__.py'))) 'Upgrade did not replace the plugin implementation.'
    Require ((Hash (Join-Path $Installed 'web/index.html')) -eq (Hash (Join-Path $Source 'web/index.html'))) 'Upgrade did not replace the dashboard.'
    Require ((Hash (Join-Path $Installed 'bin/modhunter-cli.exe')) -eq (Hash (Join-Path $Source 'bin/modhunter-cli.exe'))) 'Upgrade did not install the packaged Rust audit helper.'
    Require (!(Test-Path -LiteralPath (Join-Path $Installed 'bin/unrelated.exe'))) 'Upgrade copied an unrelated executable.'
    Require (!(Test-Path -LiteralPath (Join-Path $Installed 'previous-plugin-sentinel.txt'))) 'The replaced plugin still contains an obsolete implementation sentinel.'
    $Backups = @(Get-ChildItem -LiteralPath (Join-Path $Upgrade 'ModhunterBackups') -Directory)
    Require ($Backups.Count -eq 1) 'Upgrade did not retain exactly one previous-plugin backup.'
    $Backup = $Backups[0].FullName
    Require (!$Backup.StartsWith((Join-Path $Upgrade 'plugins') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) 'The previous plugin backup is still inside the active plugins directory.'
    Require ((Hash (Join-Path $Backup '__init__.py')) -eq $OldPluginHash) 'Upgrade changed the previous plugin before backing it up.'
    Require ((Hash (Join-Path $Backup 'bin/modhunter-cli.exe')) -eq $OldHelperHash) 'Upgrade changed or lost the previous Rust helper backup.'
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
    Require (!(Test-Path -LiteralPath (Join-Path $FreshPlugin 'bin/unrelated.exe'))) 'New installation copied an unrelated executable.'
    Require (!(Test-Path -LiteralPath (Join-Path $Fresh 'ModhunterBackups'))) 'A new installation created an unnecessary previous-plugin backup.'
    Require (!(Test-Path -LiteralPath (Join-Path $FreshPlugin 'data'))) 'New installation included unrelated fixture progress.'
    Assert-NoStaging $Fresh
    $Checks.Add(@{ name = 'new_install_has_clean_state'; passed = $true })

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
