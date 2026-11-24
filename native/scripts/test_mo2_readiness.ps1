#requires -Version 7.2
# Static-preflight regression only; no real MO2, Qt, game or account is run.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Script = (Resolve-Path (Join-Path $PSScriptRoot '../Check_MO2_Readiness.ps1')).Path
$PS51 = Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe'
$Root = Join-Path ([IO.Path]::GetTempPath()) ('modhunter preflight ' + [guid]::NewGuid().ToString('N'))
$Package = Join-Path $Root 'package'
$PackageScript = Join-Path $Package 'Check_MO2_Readiness.ps1'
$PackageVersionFile = Join-Path $Package 'MO2/mo2_update_arsenal/version.py'
$BuildFile = Join-Path $Package 'BUILD_INFO.json'
$InstalledVersionFile = Join-Path $Root 'plugins/mo2_update_arsenal/version.py'
$FixtureVersion = '9.8.7'
function Write-FixtureFile([string]$Path, [string]$Text) {
    New-Item -ItemType Directory -Path (Split-Path -Parent $Path) -Force | Out-Null
    [IO.File]::WriteAllText($Path, $Text, [Text.UTF8Encoding]::new($false))
}
function Write-FixtureManifest {
    $Lines = @(Get-ChildItem -LiteralPath $Package -File -Recurse | Where-Object { $_.Name -ne 'SHA256SUMS.txt' } | Sort-Object FullName | ForEach-Object {
        $Relative = [IO.Path]::GetRelativePath($Package, $_.FullName).Replace('\', '/')
        "$((Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash)  $Relative"
    })
    Write-FixtureFile (Join-Path $Package 'SHA256SUMS.txt') (($Lines -join "`n") + "`n")
}
function Reset-PackageFixture {
    foreach ($Relative in @('Modhunter.exe', 'modhunter-cli.exe',
        'MO2/mo2_update_arsenal/qt_bridge.py', 'MO2/mo2_update_arsenal/version_evidence.py',
        'MO2/mo2_update_arsenal/bin/modhunter-cli.exe', 'MO2/mo2_update_arsenal/bin/modhunter-qt-bridge.dll')) {
        Write-FixtureFile (Join-Path $Package $Relative) 'Inert package marker; never execute or load this file.'
    }
    Write-FixtureFile $PackageVersionFile ('VERSION = "' + $FixtureVersion + '"')
    Write-FixtureFile $InstalledVersionFile ('VERSION = "' + $FixtureVersion + '"')
    Write-FixtureFile $BuildFile (@{ version = $FixtureVersion; commit = ('a' * 40) } | ConvertTo-Json)
    Write-FixtureManifest
}
function Fixture-Snapshot {
    return (@(Get-ChildItem -LiteralPath $Root -File -Recurse | Sort-Object FullName | ForEach-Object {
        $_.FullName + ':' + (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
    }) -join "`n")
}
function Assert-PackageReadiness([string]$Case, [bool]$PackageGood, [bool]$InstalledGood, [string]$Version, [switch]$PluginOnly) {
    $BeforeCheck = Fixture-Snapshot
    $StartInfo = [Diagnostics.ProcessStartInfo]::new()
    $StartInfo.FileName = $PS51
    $StartInfo.UseShellExecute = $false
    $StartInfo.RedirectStandardOutput = $true
    $StartInfo.RedirectStandardError = $true
    foreach ($Argument in @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PackageScript, '-MO2Directory', $Root, '-Json')) {
        $StartInfo.ArgumentList.Add($Argument)
    }
    if ($PluginOnly) { $StartInfo.ArgumentList.Add('-PluginOnly') }
    $Child = [Diagnostics.Process]::Start($StartInfo)
    try {
        $StandardOutput = $Child.StandardOutput.ReadToEndAsync()
        $StandardError = $Child.StandardError.ReadToEndAsync()
        if (!$Child.WaitForExit(30000)) { throw "$Case readiness exceeded its 30 second deadline." }
        $Text = $StandardOutput.GetAwaiter().GetResult()
        $ErrorText = $StandardError.GetAwaiter().GetResult()
        # The MO2 executable remains an inert text marker in every case. A
        # matching plugin/package must not turn this into runtime acceptance.
        if ($Child.ExitCode -ne 2) { throw "$Case failed unexpectedly: $Text $ErrorText" }
        $Result = $Text | ConvertFrom-Json
        if ($Result.readyToInstall -or $Result.readyForConnectionTest -or $Result.exactBetaSuffixObserved) { throw "$Case fabricated MO2 runtime readiness." }
        $PackageChecks = @($Result.checks | Where-Object { $_.name -eq 'package_checksums' })
        $InstalledChecks = @($Result.checks | Where-Object { $_.name -eq 'plugin_installed' })
        if ($PackageChecks.Count -ne 1 -or $PackageChecks[0].passed -ne $PackageGood -or
            $InstalledChecks.Count -ne 1 -or $InstalledChecks[0].passed -ne $InstalledGood -or
            [string]$Result.modhunterVersion -cne $Version) { throw "$Case returned incorrect release identity: $Text" }
        if ($Text.Contains($Root)) { throw "$Case exported a personal path." }
        if ((Fixture-Snapshot) -cne $BeforeCheck) { throw "$Case changed package or installation data." }
    } finally {
        if (!$Child.HasExited) { Stop-Process -Id $Child.Id -ErrorAction SilentlyContinue }
        $Child.Dispose()
    }
}
try {
    New-Item -ItemType Directory -Path $Root | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $Root 'mods') | Out-Null
    [IO.File]::WriteAllText((Join-Path $Root 'ModOrganizer.exe'), 'Inert marker, never executed')
    [IO.File]::WriteAllText((Join-Path $Root 'mods/keep.txt'), 'Unchanged mod fixture')
    $Before = @(Get-ChildItem -LiteralPath $Root -File -Recurse | Sort-Object FullName | ForEach-Object { (Get-FileHash -LiteralPath $_.FullName).Hash }) -join ','
    $Output = & $PS51 -NoProfile -ExecutionPolicy Bypass -File $Script -MO2Directory $Root -Json
    if ($LASTEXITCODE -ne 2) { throw 'Invalid fixture did not return review/unsupported.' }
    $Report = ($Output -join "`n") | ConvertFrom-Json
    if ($Report.readyToInstall -or $Report.readyForConnectionTest -or $Report.exactBetaSuffixObserved) { throw 'Static fixture fabricated readiness or beta identity.' }
    if (@($Report.checks).Count -ne 5) { throw 'Static fixture omitted readiness checks.' }
    if (($Output -join "`n").Contains($Root)) { throw 'Static diagnostic exported a personal path.' }
    $After = @(Get-ChildItem -LiteralPath $Root -File -Recurse | Sort-Object FullName | ForEach-Object { (Get-FileHash -LiteralPath $_.FullName).Hash }) -join ','
    if ($Before -ne $After -or (Test-Path -LiteralPath (Join-Path $Root 'plugins'))) { throw 'Preflight modified its selected installation.' }
    $Output = & $PS51 -NoProfile -ExecutionPolicy Bypass -File $Script -Json
    if ($LASTEXITCODE -ne 1) { throw 'Noninteractive preflight without a folder did not reject the request.' }
    $ErrorReport = ($Output -join "`n") | ConvertFrom-Json
    if ($ErrorReport.readyToInstall -or !$ErrorReport.error) { throw 'Failed preflight fabricated success.' }

    New-Item -ItemType Directory -Path $Package | Out-Null
    Copy-Item -LiteralPath $Script -Destination $PackageScript
    Reset-PackageFixture
    Assert-PackageReadiness 'Matching future release' $true $true $FixtureVersion

    # Use the actual source version as well as an unrelated future version;
    # a new release must never require editing the readiness checker again.
    $VersionSource = [IO.File]::ReadAllText((Join-Path $PSScriptRoot '../../mo2_update_arsenal/version.py'))
    $VersionMatches = [regex]::Matches($VersionSource, '(?m)^\s*VERSION\s*=\s*["'']([0-9]+\.[0-9]+\.[0-9]+)["'']\s*$')
    if ($VersionMatches.Count -ne 1) { throw 'Cannot identify the current plugin version fixture.' }
    $FixtureVersion = $VersionMatches[0].Groups[1].Value
    Reset-PackageFixture
    Assert-PackageReadiness 'Matching current release' $true $true $FixtureVersion

    Write-FixtureFile $InstalledVersionFile 'VERSION = "0.0.0"'
    Assert-PackageReadiness 'Installed plugin from another release' $true $false $FixtureVersion
    Remove-Item -LiteralPath $InstalledVersionFile
    Assert-PackageReadiness 'Plugin not yet installed' $true $false $FixtureVersion

    foreach ($Case in @('mixed-build-version', 'malformed-build-json', 'missing-build-version', 'invalid-build-commit',
        'duplicate-plugin-version', 'tampered-build', 'missing-version-manifest', 'tampered-helper')) {
        Reset-PackageFixture
        switch ($Case) {
            'mixed-build-version' {
                Write-FixtureFile $BuildFile (@{ version = '0.0.0'; commit = ('a' * 40) } | ConvertTo-Json)
                Write-FixtureManifest
            }
            'malformed-build-json' { Write-FixtureFile $BuildFile '{broken'; Write-FixtureManifest }
            'missing-build-version' { Write-FixtureFile $BuildFile (@{ commit = ('a' * 40) } | ConvertTo-Json); Write-FixtureManifest }
            'invalid-build-commit' {
                Write-FixtureFile $BuildFile (@{ version = $FixtureVersion; commit = 'unverified' } | ConvertTo-Json)
                Write-FixtureManifest
            }
            'duplicate-plugin-version' {
                Write-FixtureFile $PackageVersionFile ((('VERSION = "' + $FixtureVersion + '"') + "`n") * 2)
                Write-FixtureManifest
            }
            'tampered-build' { Write-FixtureFile $BuildFile (@{ version = $FixtureVersion; commit = ('b' * 40) } | ConvertTo-Json) }
            'missing-version-manifest' {
                $ManifestPath = Join-Path $Package 'SHA256SUMS.txt'
                $Lines = @([IO.File]::ReadAllLines($ManifestPath) | Where-Object { $_ -notmatch '  MO2/mo2_update_arsenal/version\.py$' })
                Write-FixtureFile $ManifestPath (($Lines -join "`n") + "`n")
            }
            'tampered-helper' { Write-FixtureFile (Join-Path $Package 'MO2/mo2_update_arsenal/bin/modhunter-cli.exe') 'Changed helper marker' }
        }
        Assert-PackageReadiness $Case $false $false ''
    }
    Reset-PackageFixture
    foreach ($DesktopFile in @('Modhunter.exe', 'modhunter-cli.exe')) {
        Remove-Item -LiteralPath (Join-Path $Package $DesktopFile)
    }
    $PluginRoot = Join-Path $Package 'MO2/mo2_update_arsenal'
    $InstalledRoot = Split-Path -Parent $InstalledVersionFile
    foreach ($Name in @('Install_MO2_Plugin.ps1', 'Install_MO2_Plugin.bat')) {
        Write-FixtureFile (Join-Path $Package $Name) 'Inert installer fixture.'
    }
    foreach ($Name in @('__init__.py', 'plugin.py', 'web/index.html', 'bin/archive-tools/7z.exe',
            'bin/archive-tools/7z.dll', 'bin/archive-tools/License.txt', 'bin/archive-tools/provenance.json')) {
        Write-FixtureFile (Join-Path $PluginRoot $Name) 'Inert plugin file.'
    }
    foreach ($File in (Get-ChildItem -LiteralPath $PluginRoot -File -Recurse)) {
        $Name = [IO.Path]::GetRelativePath($PluginRoot, $File.FullName)
        Write-FixtureFile (Join-Path $InstalledRoot $Name) ([IO.File]::ReadAllText($File.FullName))
    }
    Write-FixtureFile $BuildFile (@{ component = 'mo2-plugin'; version = $FixtureVersion; commit = ('a' * 40) } | ConvertTo-Json)
    Write-FixtureManifest
    Assert-PackageReadiness 'Full package mode still requires the desktop binaries' $false $false ''
    Assert-PackageReadiness 'Complete plugin-only package and installed payload' $true $true $FixtureVersion -PluginOnly
    Write-FixtureFile (Join-Path $InstalledRoot 'bin/archive-tools/License.txt') 'Changed installed bytes.'
    Assert-PackageReadiness 'Changed installed component' $true $false $FixtureVersion -PluginOnly
    Write-FixtureFile (Join-Path $PluginRoot 'plugin.py') 'Changed package source.'
    Assert-PackageReadiness 'Changed packaged source' $false $false '' -PluginOnly
    Write-Output 'PASS: Windows PowerShell 5.1 checks full and plugin-only packages, detects changed installation bytes, keeps invalid runtimes in review and preserves the fixture.'
} finally {
    if (Test-Path -LiteralPath $Root) { Remove-Item -LiteralPath $Root -Recurse -Force }
}
# The final child deliberately failed. Declare harness success only after all
# assertions and cleanup complete, instead of leaking that expected exit code
# into GitHub Actions' generated pwsh wrapper. A thrown assertion skips this.
exit 0
