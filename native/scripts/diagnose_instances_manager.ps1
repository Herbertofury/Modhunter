#requires -Version 7.2
<#
Run the current diagnostic harness against the exact already-built b79 app.
This is not release acceptance: binary source and harness source are distinct.
Only a newly owned disposable Windows CI installation is created or removed.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$ArtifactDirectory,
    [string]$Output = "$PSScriptRoot/../target/instances-manager-diagnostic"
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (!$IsWindows -or $env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or !$env:RUNNER_TEMP) {
    throw 'Native manager diagnosis requires the disposable Windows CI runner.'
}
$root = (Resolve-Path -LiteralPath "$PSScriptRoot/../..").Path
$harnessCommit = (git -C $root rev-parse HEAD).Trim()
if ($harnessCommit -cne $env:GITHUB_SHA -or (git -C $root status --porcelain --untracked-files=all)) {
    throw 'Native manager diagnosis requires the exact clean diagnostic checkout.'
}
$binaryCommit = 'b79ca8a9c54240e502cb4f8c0db458a12a65eef1'
$expectedInstaller = 'b01da125b0f90f6107e2983a4465366feea424116f639725cf477ea1d0e2712a'
$expectedExecutable = '6063489afc6db47835897f33f50d407745d3b18d1205edf6079abd0b6e6037b1'
$expectedManifest = '703311dcd0d7b1696a384aa88c55c6988aa4c36e26bed62e746a4b98ce41b13c'
$installer = Join-Path (Resolve-Path -LiteralPath $ArtifactDirectory).Path 'release/bundle/nsis/Modhunter_0.13.0_x64-setup.exe'
function Hash([string]$Path) { (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() }
if ((Hash $installer) -cne $expectedInstaller) { throw 'The downloaded installer differs from the pinned b79 binary.' }
if ((Test-Path -LiteralPath $Output) -and @(Get-ChildItem -LiteralPath $Output -Force).Count) {
    throw 'Refusing an existing diagnostic output directory.'
}
New-Item -ItemType Directory -Path $Output -Force | Out-Null
$Output = (Resolve-Path -LiteralPath $Output).Path
$token = [guid]::NewGuid().ToString('N')
$owned = Join-Path $env:RUNNER_TEMP "Modhunter-Instances-Diagnostic-$token"
if (Test-Path -LiteralPath $owned) { throw 'Owned diagnostic installation must be new.' }
New-Item -ItemType Directory -Path $owned | Out-Null
$ownerFile = Join-Path $owned 'owner.txt'
[IO.File]::WriteAllText($ownerFile, $token, [Text.UTF8Encoding]::new($false))
$target = Join-Path $owned 'desktop'
$nativeOutput = Join-Path $Output 'native-manager'
$receipt = Join-Path $Output 'instances-manager-diagnostic.json'
$errors = [Collections.Generic.List[string]]::new()
$cleanupErrors = [Collections.Generic.List[string]]::new()
$report = [ordered]@{
    schemaVersion = 1; kind = 'instances-manager-diagnostic'; ok = $false; releaseAcceptance = $false
    binarySourceCommit = $binaryCommit; binaryWorkflowRun = 44011920441; binaryArtifactId = 14296729097
    harnessSourceCommit = $harnessCommit; sourceDirty = $false
    installerSha256 = $expectedInstaller; executableSha256 = $null; bundleManifestSha256 = $null
    phase = 'verify-pinned-installer'; nativeManagerPassed = $false; nativeWorkerReceipt = 'native-manager/instances-manager.json'
    scope = 'Actual pinned b79 NSIS installation and native manager observations with the current diagnostic harness and isolated inert MO2 targets. No personal MO2, account or release acceptance.'
    bindingScope = 'Native worker checkpoints identify the diagnostic harness checkout. The compiled executable and resource identify binarySourceCommit separately; they were not rebuilt from harnessSourceCommit.'
    harnessSourceSha256 = [ordered]@{}; resourceHashes = [ordered]@{}
    uninstalled = $false; errors = $errors; cleanupErrors = $cleanupErrors
}
foreach ($relative in @('native/scripts/diagnose_instances_manager.ps1', 'native/scripts/instances_manager_smoke.ps1',
        'native/scripts/verify_instances_manager.cjs', 'native/scripts/bind_native_proof.py', '.github/workflows/instances-manager-diagnostic.yml')) {
    $report.harnessSourceSha256[$relative] = Hash (Join-Path $root $relative)
}
function Save-Receipt { $report | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $receipt -Encoding utf8 }
function Registrations {
    foreach ($registryRoot in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
            'HKCU:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
        if (Test-Path -LiteralPath $registryRoot) {
            foreach ($key in (Get-ChildItem -LiteralPath $registryRoot)) {
                if ([string]$key.GetValue('DisplayName') -eq 'Modhunter') {
                    [pscustomobject]@{ path = $key.PSPath; location = [string]$key.GetValue('InstallLocation') }
                }
            }
        }
    }
}
function Run-OwnedProcess([string]$File, [string[]]$Arguments) {
    $process = Start-Process -FilePath $File -ArgumentList $Arguments -PassThru
    try {
        if (!$process.WaitForExit(90000)) { throw 'The owned diagnostic installer exceeded its deadline.' }
        $process.Refresh()
        if ($process.ExitCode -ne 0) { throw "The owned diagnostic installer failed: $($process.ExitCode)" }
    } finally {
        $process.Refresh()
        if (!$process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
        $process.Dispose()
    }
}
Save-Receipt
[IO.File]::WriteAllText((Join-Path $Output 'DIAGNOSTIC-SCOPE.txt'),
    "Binary source: $binaryCommit`nHarness source: $harnessCommit`nThis artifact is diagnostic only. Native worker source checkpoints identify the harness checkout, not a new binary build. No receipt here replaces the full release workflow.`n", [Text.UTF8Encoding]::new($false))
$installAttempted = $false
try {
    if (@(Registrations).Count -or (Get-Process -Name Modhunter -ErrorAction SilentlyContinue)) {
        throw 'Refusing an existing Modhunter installation or process on this diagnostic runner.'
    }
    $report.phase = 'install-pinned-nsis'; Save-Receipt
    $installAttempted = $true
    # NSIS consumes the remainder of /D as its destination; keep it last.
    Run-OwnedProcess $installer @('/S', "/D=$target")
    $executable = Join-Path $target 'Modhunter.exe'
    $report.executableSha256 = Hash $executable
    if ($report.executableSha256 -cne $expectedExecutable) { throw 'NSIS installed a different executable.' }
    $resource = Join-Path $target 'mo2-installer'
    $manifest = Join-Path $resource 'SHA256SUMS.txt'
    $report.bundleManifestSha256 = Hash $manifest
    if ($report.bundleManifestSha256 -cne $expectedManifest) { throw 'Installed resources do not match the pinned b79 manifest.' }
    $declared = @{}
    foreach ($line in [IO.File]::ReadAllLines($manifest)) {
        if ($line -notmatch '^([0-9a-fA-F]{64})  (.+)$') { throw 'Invalid pinned resource manifest.' }
        $sha = $Matches[1].ToLowerInvariant(); $relative = $Matches[2]
        if ($declared.ContainsKey($relative) -or $relative.Contains('\') -or
            [IO.Path]::IsPathRooted($relative) -or $relative.Split('/') -contains '..') {
            throw 'Unsafe or repeated pinned resource path.'
        }
        $declared[$relative] = $sha
    }
    foreach ($entry in (Get-ChildItem -LiteralPath $resource -Force -Recurse)) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Installed resources contain a linked path.' }
        if ($entry.PSIsContainer) { continue }
        $relative = [IO.Path]::GetRelativePath($resource, $entry.FullName).Replace('\', '/')
        $sha = Hash $entry.FullName
        if ($relative -ne 'SHA256SUMS.txt' -and (!$declared.ContainsKey($relative) -or $declared[$relative] -cne $sha)) {
            throw 'Installed resources differ from the whole pinned manifest.'
        }
        $report.resourceHashes[$relative] = $sha
    }
    if ($report.resourceHashes.Count -ne $declared.Count + 1) { throw 'Installed resource manifest is incomplete.' }
    $build = Get-Content -LiteralPath (Join-Path $resource 'BUILD_INFO.json') -Raw | ConvertFrom-Json
    if ($build.version -cne '0.13.0' -or $build.commit -cne $binaryCommit -or
        $build.workflow -cne 'https://github.com/Herbertofury/Modhunter/actions/runs/44011920441') {
        throw 'Installed bundle build identity differs from b79.'
    }
    $report.phase = 'observe-native-manager'; Save-Receipt
    & "$PSScriptRoot/instances_manager_smoke.ps1" -Executable $executable -Output $nativeOutput
    $native = Get-Content -LiteralPath (Join-Path $nativeOutput 'instances-manager.json') -Raw | ConvertFrom-Json
    $report.nativeManagerPassed = $native.ok -eq $true
    if (!$report.nativeManagerPassed) { throw 'The exact native manager diagnostic did not pass.' }
    $report.phase = 'native-manager-complete'
} catch {
    $errors.Add($_.Exception.Message)
    $report.phase = 'native-manager-diagnostic-failed'
} finally {
    $workerReceipt = Join-Path $nativeOutput 'instances-manager.json'
    if (Test-Path -LiteralPath $workerReceipt) {
        try {
            $native = Get-Content -LiteralPath $workerReceipt -Raw | ConvertFrom-Json -AsHashtable
            $report.nativeManagerPassed = $native['ok'] -eq $true
            $report.nativeOutcome = @{ phase = $native['phase']; errors = $native['errors']; passedChecks = @($native['checks'] | Where-Object { $_['passed'] }).Count }
        } catch { $errors.Add('Could not read the preserved native receipt: ' + $_.Exception.Message) }
    }
    if ($installAttempted) {
        try {
            $uninstaller = Join-Path $target 'uninstall.exe'
            if (!(Test-Path -LiteralPath $uninstaller -PathType Leaf)) { throw 'The owned installation has no uninstaller; retained for diagnosis.' }
            Run-OwnedProcess $uninstaller @('/S', "_?=$target")
            if (Test-Path -LiteralPath (Join-Path $target 'Modhunter.exe')) { throw 'Owned uninstall left the executable behind.' }
            if (@(Registrations | Where-Object { $_.location.Trim('"').TrimEnd([char[]]'\/') -eq $target.TrimEnd([char[]]'\/') }).Count) {
                throw 'Owned uninstall left its registration behind.'
            }
            $remaining = @(Get-ChildItem -LiteralPath $target -Force -File -Recurse | Where-Object { $_.FullName -ne $uninstaller })
            if ($remaining.Count) { throw 'Owned uninstall left payload files; retained for diagnosis.' }
            if (([IO.File]::ReadAllText($ownerFile)) -cne $token) { throw 'Owned fixture marker changed; refusing directory removal.' }
            if (Test-Path -LiteralPath $uninstaller) { Remove-Item -LiteralPath $uninstaller }
            Get-ChildItem -LiteralPath $target -Directory -Recurse | Sort-Object FullName -Descending | ForEach-Object { [IO.Directory]::Delete($_.FullName, $false) }
            [IO.Directory]::Delete($target, $false)
            Remove-Item -LiteralPath $ownerFile
            [IO.Directory]::Delete($owned, $false)
            $report.uninstalled = $true
        } catch { $cleanupErrors.Add($_.Exception.Message) }
    }
    if ((Hash $installer) -cne $expectedInstaller) { $errors.Add('Downloaded installer changed during diagnosis.') }
    if ((git -C $root rev-parse HEAD).Trim() -cne $harnessCommit -or (git -C $root status --porcelain --untracked-files=all)) {
        $errors.Add('Diagnostic source changed during execution.')
    }
    $report.ok = $report.nativeManagerPassed -and $errors.Count -eq 0 -and $cleanupErrors.Count -eq 0 -and $report.uninstalled
    Save-Receipt
}
Get-Content -LiteralPath $receipt -Raw
if (!$report.ok) { throw 'Native Instances diagnostic failed; inspect its preserved actual UI, IPC and cleanup evidence.' }
