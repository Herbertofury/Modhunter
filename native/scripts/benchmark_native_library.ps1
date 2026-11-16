param(
    [Parameter(Mandatory=$true)][string]$Executable,
    [string]$Output = "$PSScriptRoot/../target/native-library-performance"
)
$ErrorActionPreference = 'Stop'
if ($env:GITHUB_ACTIONS -ne 'true' -or $env:RUNNER_OS -ne 'Windows' -or !$env:RUNNER_TEMP) {
    throw 'Native library performance requires a disposable Windows GitHub runner.'
}
$Executable = (Resolve-Path -LiteralPath $Executable).Path
if ([IO.Path]::GetFileName($Executable) -cne 'Modhunter.exe') { throw 'Expected the exact shipped Modhunter.exe.' }
if (Get-Process -Name Modhunter -ErrorAction SilentlyContinue) { throw 'Close the previous owned native acceptance process before this independent benchmark.' }
$root = (Resolve-Path -LiteralPath "$PSScriptRoot/../..").Path
New-Item -ItemType Directory -Force -Path $Output | Out-Null
$Output = (Resolve-Path -LiteralPath $Output).Path
$token = [guid]::NewGuid().ToString('N')
$owned = Join-Path $env:RUNNER_TEMP "modhunter-native-library-$token"
New-Item -ItemType Directory -Path $owned | Out-Null
$stopwatch = [Diagnostics.Stopwatch]::StartNew()
$reportPath = Join-Path $Output 'native-library-performance.json'
$checkpoint = Join-Path $Output 'native-source-checkpoint.json'
$fixture = Join-Path $owned 'catalog.json'
$browserProfile = Join-Path $owned 'webview2'
$discoveryRoot = Join-Path $owned 'local-discovery'
New-Item -ItemType Directory -Path $browserProfile, $discoveryRoot | Out-Null
$policies = [Collections.Generic.List[object]]::new()
$folders = [Collections.Generic.List[object]]::new()
$cleanupErrors = [Collections.Generic.List[string]]::new()
$native = $null
$worker = $null
$failure = $null
$isolation = [ordered]@{ schemaVersion = 1; ok = $false; marker = $token; ownedRoot = $owned; restoredFolders = @(); policyValuesRemoved = 0 }

function Manifest([string]$Directory) {
    if (!(Test-Path -LiteralPath $Directory)) { return $null }
    if ((Get-Item -LiteralPath $Directory).Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing linked native configuration.' }
    $entries = [ordered]@{}
    foreach ($entry in (Get-ChildItem -LiteralPath $Directory -Force -Recurse | Sort-Object FullName)) {
        if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing linked content in native configuration.' }
        $relative = $entry.FullName.Substring($Directory.Length).TrimStart([char[]]'\/')
        $entries[$relative] = if ($entry.PSIsContainer) { '<directory>' } else { (Get-FileHash -LiteralPath $entry.FullName -Algorithm SHA256).Hash.ToLowerInvariant() }
        if ($stopwatch.Elapsed.TotalSeconds -gt 555) { throw 'Native benchmark exceeded the cleanup reserve.' }
    }
    return ($entries | ConvertTo-Json -Compress)
}
function Set-OwnedPolicy([string]$Name, [string]$Value) {
    if ($Name -notin @('UserDataFolder', 'AdditionalBrowserArguments')) { throw 'Unexpected native test policy.' }
    $key = "HKLM:\SOFTWARE\Policies\Microsoft\Edge\WebView2\$Name"
    if ((Test-Path -LiteralPath $key) -and (Get-Item -LiteralPath $key).GetValueNames().Contains('Modhunter.exe')) {
        throw "Refusing an existing application WebView2 policy: $Name"
    }
    New-Item -Path $key -Force | Out-Null
    New-ItemProperty -LiteralPath $key -Name 'Modhunter.exe' -Value $Value -PropertyType String | Out-Null
    $policies.Add(@{ key = $key; value = $Value })
    if ((Get-ItemPropertyValue -LiteralPath $key -Name 'Modhunter.exe') -cne $Value) { throw 'Temporary WebView2 policy readback failed.' }
}
function Stop-OwnedProcess($Process) {
    if (!$Process) { return }
    $Process.Refresh()
    if (!$Process.HasExited) {
        $null = $Process.CloseMainWindow()
        if (!$Process.WaitForExit(5000)) {
            & taskkill.exe /PID $Process.Id /T /F | Out-Null
            if (!$Process.WaitForExit(5000)) { throw "Owned benchmark process $($Process.Id) did not exit." }
        }
    }
}
try {
    python "$PSScriptRoot/bind_native_proof.py" --executable $Executable --checkpoint $checkpoint
    if ($LASTEXITCODE -ne 0) { throw 'Native benchmark requires clean, bound candidate source.' }
    node "$PSScriptRoot/measure_native_library.cjs" "--write-fixture=$fixture"
    if ($LASTEXITCODE -ne 0) { throw 'Exact shared native benchmark fixture generation failed.' }
    # Tauri uses Windows known-folder APIs. APPDATA overrides alone do not isolate
    # these paths. Preserve each actual folder by a same-volume sibling rename;
    # restore its complete manifest after the owned native process has exited.
    $known = @(
        (Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'com.modhunter.desktop'),
        (Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'com.modhunter.desktop')
    ) | Select-Object -Unique
    foreach ($directory in $known) {
        $saved = "$directory.native-library-preserved-$token"
        if (Test-Path -LiteralPath $saved) { throw 'Preservation destination already exists.' }
        $before = Manifest $directory
        $entry = @{ path = $directory; saved = $saved; manifest = $before; moved = $false; created = $false }
        $folders.Add($entry)
        if (Test-Path -LiteralPath $directory) {
            Move-Item -LiteralPath $directory -Destination $saved
            $entry.moved = $true
        }
        New-Item -ItemType Directory -Path $directory | Out-Null
        $entry.created = $true
        Set-Content -LiteralPath (Join-Path $directory 'native-library-owner.txt') -Value $token -Encoding ascii -NoNewline
    }
    $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
    $listener.Start()
    $port = $listener.LocalEndpoint.Port
    $listener.Stop()
    # This exact application policy is required by elevated WebView2 runtimes.
    # --enable-automation permits read-only executable/profile identity via CDP;
    # no runtime downgrade, security relaxation or product configuration is used.
    $browserArguments = "--remote-debugging-port=$port --remote-debugging-address=127.0.0.1 --enable-automation"
    Set-OwnedPolicy 'UserDataFolder' $browserProfile
    Set-OwnedPolicy 'AdditionalBrowserArguments' $browserArguments
    $environment = @{}
    foreach ($name in @('LOCALAPPDATA', 'WEBVIEW2_USER_DATA_FOLDER', 'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS', 'MODHUNTER_NATIVE_SMOKE')) {
        $environment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    }
    try {
        # LOCALAPPDATA scopes only discovery; actual known folders were handled
        # explicitly above. Never connect to the previous live MO2 session.
        $env:LOCALAPPDATA = $discoveryRoot
        $env:WEBVIEW2_USER_DATA_FOLDER = $browserProfile
        $env:WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS = $browserArguments
        $env:MODHUNTER_NATIVE_SMOKE = '1'
        $native = Start-Process -FilePath $Executable -WorkingDirectory ([IO.Path]::GetDirectoryName($Executable)) -PassThru -RedirectStandardOutput "$Output/native-stdout.log" -RedirectStandardError "$Output/native-stderr.log"
        $workerArguments = @(
            "`"$PSScriptRoot/measure_native_library.cjs`"", "`"--exe=$Executable`"", "--pid=$($native.Id)",
            "--endpoint=http://127.0.0.1:$port/", "`"--fixture=$fixture`"", "`"--output=$reportPath`"", "`"--browser-profile=$browserProfile`""
        )
        $worker = Start-Process -FilePath 'node.exe' -ArgumentList $workerArguments -WorkingDirectory $root -WindowStyle Hidden -PassThru -RedirectStandardOutput "$Output/measurement.log" -RedirectStandardError "$Output/measurement-errors.log"
    } finally {
        foreach ($name in $environment.Keys) { [Environment]::SetEnvironmentVariable($name, $environment[$name], 'Process') }
    }
    $remaining = [Math]::Min(480000, [Math]::Max(0, 540000 - [int]$stopwatch.ElapsedMilliseconds))
    if (!$worker.WaitForExit($remaining)) { throw 'Native renderer worker exceeded the bounded benchmark deadline.' }
    if ($worker.ExitCode -ne 0) { throw "Native renderer measurement failed with exit $($worker.ExitCode)." }
    $native.Refresh()
    if ($native.HasExited) { throw 'Native application exited before measurement completion.' }
    $result = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json
    if ($result.ok -ne $true -or $result.nativeProcessId -ne $native.Id) { throw 'Native renderer measurement did not prove the owned application.' }
    python "$PSScriptRoot/bind_native_proof.py" --executable $Executable --checkpoint $checkpoint --report $reportPath
    if ($LASTEXITCODE -ne 0) { throw 'Native performance receipt source/executable binding failed.' }
} catch {
    $failure = $_.Exception.Message
} finally {
    foreach ($process in @($worker, $native)) {
        try { Stop-OwnedProcess $process } catch { $cleanupErrors.Add($_.Exception.Message) }
    }
    for ($i = $policies.Count - 1; $i -ge 0; $i--) {
        $policy = $policies[$i]
        try {
            if ((Get-ItemPropertyValue -LiteralPath $policy.key -Name 'Modhunter.exe') -cne $policy.value) { throw 'Temporary policy changed; refusing to remove another owner value.' }
            Remove-ItemProperty -LiteralPath $policy.key -Name 'Modhunter.exe'
            $isolation.policyValuesRemoved++
        } catch { $cleanupErrors.Add($_.Exception.Message) }
    }
    for ($i = $folders.Count - 1; $i -ge 0; $i--) {
        $entry = $folders[$i]
        try {
            if ($entry.created -and (Test-Path -LiteralPath $entry.path)) {
                $marker = Join-Path $entry.path 'native-library-owner.txt'
                if (!(Test-Path -LiteralPath $marker) -or (Get-Content -LiteralPath $marker -Raw) -cne $token) { throw 'Native fixture folder owner changed; preserved folder retained for recovery.' }
                Remove-Item -LiteralPath $entry.path -Recurse -Force
            }
            if ($entry.moved) { Move-Item -LiteralPath $entry.saved -Destination $entry.path }
            if ((Manifest $entry.path) -cne $entry.manifest) { throw 'Original native configuration did not restore byte for byte.' }
            $isolation.restoredFolders += @{ path = $entry.path; originalExisted = ($null -ne $entry.manifest); manifestMatched = $true }
        } catch { $cleanupErrors.Add($_.Exception.Message) }
    }
    $isolation.ok = $cleanupErrors.Count -eq 0
    $isolation.cleanupErrors = $cleanupErrors.ToArray()
    $isolation.elapsedMs = $stopwatch.ElapsedMilliseconds
    $isolation | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath "$Output/isolation.json" -Encoding utf8
    if (Test-Path -LiteralPath $reportPath) { $result = Get-Content -LiteralPath $reportPath -Raw | ConvertFrom-Json }
    else { $result = [pscustomobject]@{ schemaVersion = 1; ok = $false; scope = 'Windows native renderer benchmark'; errors = @() } }
    $result | Add-Member -NotePropertyName isolation -NotePropertyValue $isolation -Force
    $result | Add-Member -NotePropertyName launcherSha256 -NotePropertyValue ((Get-FileHash -LiteralPath $PSCommandPath -Algorithm SHA256).Hash.ToLowerInvariant()) -Force
    if ($failure -or !$isolation.ok) {
        $result.ok = $false
        $result.errors = @($result.errors) + @($failure) + $cleanupErrors.ToArray() | Where-Object { $_ }
    }
    $result | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $reportPath -Encoding utf8
    foreach ($log in @("$Output/measurement.log", "$Output/measurement-errors.log")) {
        if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 40 }
    }
}
if ($failure) { throw $failure }
if ($cleanupErrors.Count) { throw ($cleanupErrors -join '; ') }
Get-Content -LiteralPath $reportPath -Raw
