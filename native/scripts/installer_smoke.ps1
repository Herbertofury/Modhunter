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
$mo2Target = Join-Path $env:RUNNER_TEMP ('Modhunter-Installed-Plugin-QA-' + [guid]::NewGuid().ToString('N'))
function Invoke-InstalledPluginTool([string]$Script, [string]$Target, $Build, [string]$ManifestHash, [string]$Fingerprint) {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe'
    $start.UseShellExecute = $false
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    foreach ($argument in @('-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', $Script,
            '-Json', '-MO2Directory', $Target, '-ExpectedVersion', $Build.version,
            '-ExpectedCommit', $Build.commit, '-ExpectedManifestSha256', $ManifestHash)) {
        $start.ArgumentList.Add($argument)
    }
    if ($Fingerprint) { $start.ArgumentList.Add('-ExpectedInstalledFingerprint'); $start.ArgumentList.Add($Fingerprint) }
    else { $start.ArgumentList.Add('-Preview') }
    $child = [Diagnostics.Process]::Start($start)
    try {
        $stdout = $child.StandardOutput.ReadToEndAsync()
        $stderr = $child.StandardError.ReadToEndAsync()
        # Owned disposable acceptance fixture only; production does not kill
        # an installer while it is replacing a plugin or restoring a backup.
        if (!$child.WaitForExit(60000)) { throw 'Installed plugin fixture exceeded its deadline.' }
        $text = $stdout.GetAwaiter().GetResult()
        $errors = $stderr.GetAwaiter().GetResult()
        if ($child.ExitCode -ne 0 -or $errors -or !$text.StartsWith('{') -or $text.Length -gt 65536) {
            throw "Installed plugin tool did not return a successful bounded JSON receipt: $text $errors"
        }
        $result = $text | ConvertFrom-Json
        if ($result.schemaVersion -ne 1 -or !$result.ok) { throw 'Installed plugin tool returned a failed or unknown JSON receipt.' }
        return $result
    } finally {
        if (!$child.HasExited) { Stop-Process -Id $child.Id -ErrorAction SilentlyContinue }
        $child.Dispose()
    }
}
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
    $pluginResource = Join-Path $target 'mo2-installer'
    $preparedPluginResource = Join-Path $root 'native/resources/mo2-installer'
    Check 'installed_plugin_resource_matches_prepared_bundle' (
        (Test-Path -LiteralPath $pluginResource -PathType Container) -and
        (Fixture-Manifest $pluginResource) -eq (Fixture-Manifest $preparedPluginResource))
    $resourceHashes = [ordered]@{}
    foreach ($file in (Get-ChildItem -LiteralPath $pluginResource -File -Recurse | Sort-Object FullName)) {
        if ($file.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'An installed plugin resource is linked.' }
        $relative = [IO.Path]::GetRelativePath($pluginResource, $file.FullName).Replace('\', '/')
        $resourceHashes[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $report.pluginInstallerResourceHashes = $resourceHashes
    $report.pluginInstallerManifestSha256 = $resourceHashes['SHA256SUMS.txt']
    $pluginBuild = Get-Content -Raw -LiteralPath (Join-Path $pluginResource 'BUILD_INFO.json') | ConvertFrom-Json
    $report.pluginInstallerVersion = $pluginBuild.version
    $report.pluginInstallerCommit = $pluginBuild.commit
    Check 'installed_plugin_resource_identifies_candidate' ($pluginBuild.commit -eq $commit -and $pluginBuild.version -eq
        (Get-Content -Raw -LiteralPath (Join-Path $preparedPluginResource 'BUILD_INFO.json') | ConvertFrom-Json).version)
    New-Item -ItemType Directory -Path $mo2Target | Out-Null
    # A valid architecture marker only, with no executable code. Never launch it.
    $marker = [byte[]]::new(512)
    [Text.Encoding]::ASCII.GetBytes('MZ').CopyTo($marker, 0)
    [BitConverter]::GetBytes([uint32]128).CopyTo($marker, 60)
    ([byte[]]@(0x50, 0x45, 0, 0)).CopyTo($marker, 128)
    [BitConverter]::GetBytes([uint16]0x8664).CopyTo($marker, 132)
    [IO.File]::WriteAllBytes((Join-Path $mo2Target 'ModOrganizer.exe'), $marker)
    $tool = Join-Path $pluginResource 'Install_MO2_Plugin.ps1'
    $beforePreview = Fixture-Manifest $mo2Target
    $preview = Invoke-InstalledPluginTool $tool $mo2Target $pluginBuild $report.pluginInstallerManifestSha256 ''
    Check 'installed_plugin_preview_is_read_only' ($preview.status -eq 'preview' -and
        $preview.installedFingerprint -match '^[0-9a-f]{64}$' -and (Fixture-Manifest $mo2Target) -eq $beforePreview -and
        !(Test-Path -LiteralPath (Join-Path $mo2Target 'plugins')))
    $installedPlugin = Invoke-InstalledPluginTool $tool $mo2Target $pluginBuild $report.pluginInstallerManifestSha256 $preview.installedFingerprint
    $pluginDestination = Join-Path $mo2Target 'plugins/mo2_update_arsenal'
    $installedPayloadHashes = [ordered]@{}
    foreach ($relative in $resourceHashes.Keys) {
        if (!$relative.StartsWith('MO2/mo2_update_arsenal/', [StringComparison]::Ordinal)) { continue }
        $name = $relative.Substring('MO2/mo2_update_arsenal/'.Length)
        $installedFile = Join-Path $pluginDestination $name
        if (!(Test-Path -LiteralPath $installedFile -PathType Leaf)) { throw "Installed plugin payload is missing: $name" }
        $installedPayloadHashes[$name] = (Get-FileHash -LiteralPath $installedFile -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($installedPayloadHashes[$name] -ne $resourceHashes[$relative]) { throw "Installed plugin payload differs: $name" }
    }
    $report.installedPluginPayloadHashes = $installedPayloadHashes
    Check 'installed_plugin_tool_installs_exact_payload' ($installedPlugin.status -eq 'installed' -and
        $installedPlugin.version -eq $pluginBuild.version -and $installedPlugin.commit -eq $commit -and
        $installedPayloadHashes.Count -gt 0 -and
        @(Get-ChildItem -LiteralPath $pluginDestination -File -Recurse).Count -eq $installedPayloadHashes.Count)
    New-Item -ItemType Directory -Path (Join-Path $pluginDestination 'data/empty-directory') -Force | Out-Null
    $progress = Join-Path $pluginDestination 'data/chronicle.json'
    [IO.File]::WriteAllText($progress, '{"fixture":"installed-resource-upgrade","progress":2468}', [Text.UTF8Encoding]::new($false))
    $previousPlugin = Fixture-Manifest $pluginDestination
    $preview = Invoke-InstalledPluginTool $tool $mo2Target $pluginBuild $report.pluginInstallerManifestSha256 ''
    $upgraded = Invoke-InstalledPluginTool $tool $mo2Target $pluginBuild $report.pluginInstallerManifestSha256 $preview.installedFingerprint
    Check 'installed_plugin_upgrade_preserves_progress_and_backup' ($upgraded.status -eq 'updated' -and $upgraded.preservedData -and
        $upgraded.previousVersion -eq $pluginBuild.version -and $upgraded.rollback -eq 'not_needed' -and
        $upgraded.backupDirectory.StartsWith((Join-Path $mo2Target 'ModhunterBackups') + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase) -and
        (Fixture-Manifest $upgraded.backupDirectory) -eq $previousPlugin -and (Fixture-Manifest $pluginDestination) -eq $previousPlugin -and
        (Test-Path -LiteralPath (Join-Path $pluginDestination 'data/empty-directory') -PathType Container))
    $externalPluginManifest = Fixture-Manifest $mo2Target
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
    Check 'uninstall_preserves_external_mo2_plugin' ((Test-Path -LiteralPath $pluginDestination -PathType Container) -and
        (Fixture-Manifest $mo2Target) -eq $externalPluginManifest)
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
    Remove-Item -LiteralPath $mo2Target -Recurse -Force
    $report.ok = $true
} finally {
    $report | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$Output/installer-smoke.json" -Encoding utf8
    # On failure use only the uninstaller created in our unique fixture path.
    if (!$uninstalled -and (Test-Path -LiteralPath "$target/uninstall.exe" -PathType Leaf)) {
        try { Run-OwnedProcess "$target/uninstall.exe" @('/S', "_?=$target") } catch { Write-Warning 'Fixture uninstall did not complete.' }
    }
}
Get-Content -Raw -LiteralPath "$Output/installer-smoke.json"
