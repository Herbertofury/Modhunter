param(
    [Parameter(Mandatory=$true)][string]$Installer,
    [Parameter(Mandatory=$true)][string]$Executable,
    [string]$Output = "$PSScriptRoot/../target/installer-smoke"
)
$ErrorActionPreference = 'Stop'
# This accepts only a new, disposable GitHub Windows runner installation.
# It never upgrades or uninstalls a person's existing application.
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or !$env:RUNNER_TEMP) {
    throw 'Installer acceptance requires the disposable Windows CI runner.'
}
$Installer = (Resolve-Path -LiteralPath $Installer).Path
$Executable = (Resolve-Path -LiteralPath $Executable).Path
$root = (Resolve-Path -LiteralPath "$PSScriptRoot/../..").Path
$commit = (git -C $root rev-parse HEAD).Trim()
if (git -C $root status --porcelain --untracked-files=all) { throw 'Installer acceptance requires an unchanged candidate source.' }
$target = Join-Path $env:RUNNER_TEMP ('Modhunter-Installer-QA-' + [guid]::NewGuid().ToString('N'))
if (Test-Path -LiteralPath $target) { throw 'Installer fixture destination must not exist.' }
New-Item -ItemType Directory -Force -Path $Output | Out-Null
$Output = (Resolve-Path -LiteralPath $Output).Path
$checks = [Collections.Generic.List[object]]::new()
$report = [ordered]@{
    schemaVersion = 1; ok = $false; candidateCommit = $commit; sourceDirty = $false
    installerSha256 = (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant()
    executableSha256 = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToLowerInvariant()
    installerScriptSha256 = (Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()
    scope = 'Actual NSIS current-user installation, installed WebView2 fixture launch and uninstall on a disposable Windows CI runner; no personal installation or accounts.'
    checks = $checks
}
function Check([string]$Name, [bool]$Passed) {
    $checks.Add(@{ name = $Name; passed = $Passed })
    if (!$Passed) { throw "Installer acceptance failed: $Name" }
}
function Run-OwnedProcess([string]$File, [string[]]$Arguments, [int]$Timeout = 90000) {
    $owned = Start-Process -FilePath $File -ArgumentList $Arguments -PassThru
    try {
        if (!$owned.WaitForExit($Timeout)) { throw 'Owned installer process did not complete within its deadline.' }
        $owned.Refresh()
        if ($owned.ExitCode -ne 0) { throw "Owned installer process failed: $($owned.ExitCode)" }
    } finally {
        $owned.Refresh()
        if (!$owned.HasExited) { Stop-Process -Id $owned.Id -ErrorAction SilentlyContinue }
    }
}
function Fixture-Manifest([string]$Directory) {
    $files = [ordered]@{}
    foreach ($file in (Get-ChildItem -LiteralPath $Directory -File -Recurse | Sort-Object FullName)) {
        $relative = $file.FullName.Substring($Directory.Length).TrimStart([char[]]'\/')
        $files[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return ($files | ConvertTo-Json -Compress)
}
$uninstalled = $false
try {
    # NSIS requires /D to be last; its remainder is the complete directory.
    Run-OwnedProcess $Installer @('/S', "/D=$target")
    $installed = Join-Path $target 'Modhunter.exe'
    Check 'nsis_installs_executable' (Test-Path -LiteralPath $installed -PathType Leaf)
    $report.installedExecutableSha256 = (Get-FileHash -LiteralPath $installed -Algorithm SHA256).Hash.ToLowerInvariant()
    Check 'installed_executable_matches_tested_binary' ($report.installedExecutableSha256 -eq $report.executableSha256)
    $registration = @(
        foreach ($registryRoot in @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall',
                'HKCU:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall')) {
            if (Test-Path -LiteralPath $registryRoot) {
                foreach ($key in (Get-ChildItem -LiteralPath $registryRoot)) {
                    $entry = Get-ItemProperty -LiteralPath $key.PSPath
                    if ($entry.InstallLocation -and ([string]$entry.InstallLocation).Trim('"').TrimEnd([char[]]'\/') -eq $target.TrimEnd([char[]]'\/')) {
                        $key.PSPath
                    }
                }
            }
        }
    )
    Check 'nsis_registers_current_user_installation' ($registration.Count -eq 1)
    $helpers = [ordered]@{}
    foreach ($name in @('7z.exe', '7z.dll', 'License.txt', 'provenance.json')) {
        $packaged = Join-Path $target "archive-tools/$name"
        $source = Join-Path $root "native/resources/archive-tools/$name"
        Check "installed_archive_resource_$name" ((Test-Path -LiteralPath $packaged -PathType Leaf) -and
            ((Get-FileHash -LiteralPath $packaged -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash))
        $helpers[$name] = (Get-FileHash -LiteralPath $packaged -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $report.archiveResourceHashes = $helpers
    & "$PSScriptRoot/windows_smoke.ps1" -Executable $installed -Output "$Output/native-launch"
    $native = Get-Content -Raw -LiteralPath "$Output/native-launch/native-smoke.json" | ConvertFrom-Json
    Check 'installed_application_runs_in_webview2' ($native.ok -eq $true -and $native.candidateCommit -eq $commit -and
        $native.executableSha256 -eq $report.executableSha256 -and $native.sourceDirty -eq $false)
    $fixture = Join-Path $Output 'native-launch/fixture'
    $profileManifest = Fixture-Manifest $fixture
    $uninstaller = Join-Path $target 'uninstall.exe'
    Check 'nsis_provides_uninstaller' (Test-Path -LiteralPath $uninstaller -PathType Leaf)
    Run-OwnedProcess $uninstaller @('/S', "_?=$target")
    Check 'nsis_removes_installed_executable' (!(Test-Path -LiteralPath $installed))
    $remainingPayload = @(Get-ChildItem -LiteralPath $target -File -Recurse | Where-Object { $_.FullName -ne $uninstaller })
    Check 'nsis_removes_installed_payload' ($remainingPayload.Count -eq 0)
    Check 'nsis_removes_current_user_registration' (!(Test-Path -LiteralPath $registration[0]))
    Check 'uninstall_preserves_external_profile' ((Test-Path -LiteralPath $fixture -PathType Container) -and
        (Fixture-Manifest $fixture) -eq $profileManifest)
    # _?= makes NSIS run in place so it can be awaited. Only its owned
    # executable and empty fixture directories are removed after acceptance.
    if (Test-Path -LiteralPath $uninstaller) { Remove-Item -LiteralPath $uninstaller }
    Get-ChildItem -LiteralPath $target -Directory -Recurse | Sort-Object FullName -Descending | ForEach-Object {
        [IO.Directory]::Delete($_.FullName, $false)
    }
    [IO.Directory]::Delete($target, $false)
    $uninstalled = $true
    Check 'installer_artifacts_stayed_unchanged' (
        (Get-FileHash -LiteralPath $Installer -Algorithm SHA256).Hash.ToLowerInvariant() -eq $report.installerSha256 -and
        (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash.ToLowerInvariant() -eq $report.executableSha256)
    Check 'candidate_source_stayed_unchanged' (!(git -C $root status --porcelain --untracked-files=all))
    $report.ok = $true
} finally {
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$Output/installer-smoke.json" -Encoding utf8
    # On failure use only the uninstaller created in our unique fixture path.
    if (!$uninstalled -and (Test-Path -LiteralPath "$target/uninstall.exe" -PathType Leaf)) {
        try { Run-OwnedProcess "$target/uninstall.exe" @('/S', "_?=$target") } catch { Write-Warning 'Fixture uninstall did not complete.' }
    }
}
Get-Content -Raw -LiteralPath "$Output/installer-smoke.json"
