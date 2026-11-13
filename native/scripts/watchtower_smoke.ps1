param(
  [Parameter(Mandatory=$true)][string]$Executable,
  [string]$Output = "$env:TEMP\modhunter-watchtower-smoke"
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

# The compiled application owns this disposable HTTP fixture. Ordinary tracked
# pages still require public HTTPS origins; no real account is used by this run.
New-Item -ItemType Directory -Force -Path $Output | Out-Null
$Executable = (Resolve-Path -LiteralPath $Executable).Path
$Output = (Resolve-Path -LiteralPath $Output).Path
$report = Join-Path $Output 'watchtower-smoke.json'
$events = Join-Path $Output 'watchtower-smoke.events.jsonl'
Remove-Item -LiteralPath $report, $events -ErrorAction SilentlyContinue
$required = @(
  'trusted_manager_ipc',
  'remote_ipc_denied',
  'session_persistent_reopen',
  'session_account_isolation',
  'session_reset_generation',
  'authenticated_page_capture',
  'authenticated_page_change',
  'selected_text_capture',
  'incomplete_capture_rejected',
  'stale_operation_rejected',
  'cancelled_capture_rejected',
  'same_session_popup'
  'public_https_end_to_end'
)
$process = Start-Process -FilePath $Executable -ArgumentList @("`"--watchtower-smoke-output=$report`"") -RedirectStandardOutput (Join-Path $Output 'stdout.log') -RedirectStandardError (Join-Path $Output 'stderr.log') -PassThru
try {
  $deadline = [DateTime]::UtcNow.AddSeconds(120)
  while (!(Test-Path -LiteralPath $report) -and !$process.HasExited -and [DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 250
    $process.Refresh()
  }
  if (!(Test-Path -LiteralPath $report)) { throw "Native Watchtower fixture did not produce a receipt. Exit=$($process.ExitCode)" }
  $data = Get-Content -LiteralPath $report -Raw | ConvertFrom-Json
  if ($data.ok -isnot [bool] -or !$data.ok -or $data.nativeWebView2 -isnot [bool] -or !$data.nativeWebView2 -or $data.watchtowerSchema -ne 1) {
    throw 'Native Watchtower receipt does not prove successful schema 1 WebView2 execution.'
  }
  $seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
  foreach ($check in @($data.checks)) {
    if ($check.name -isnot [string] -or !$check.name -or !$seen.Add($check.name) -or $check.passed -isnot [bool] -or !$check.passed) {
      throw 'Native Watchtower receipt contains a failed, invalid or duplicate check.'
    }
  }
  foreach ($name in $required) {
    if (!$seen.Contains($name)) { throw "Native Watchtower receipt omitted required passing check: $name" }
  }
  while (!$process.HasExited -and [DateTime]::UtcNow -lt $deadline) {
    Start-Sleep -Milliseconds 250
    $process.Refresh()
  }
  if (!$process.HasExited) { throw 'Native Watchtower process did not exit cleanly within 120 seconds.' }
  $process.WaitForExit()
  if ($process.ExitCode -ne 0) { throw "Native Watchtower process exited with code $($process.ExitCode)." }
  Get-Content -LiteralPath $report -Raw
} finally {
  $process.Refresh()
  @{
    hasExited = $process.HasExited
    exitCode = if ($process.HasExited) { $process.ExitCode } else { $null }
    scope = 'Owned local account fixtures and an anonymous public HTTPS baseline; no real account login'
  } | ConvertTo-Json | Set-Content -Encoding utf8 (Join-Path $Output 'process.json')
  foreach ($log in @($events, (Join-Path $Output 'stdout.log'), (Join-Path $Output 'stderr.log'))) {
    if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 80 }
  }
  if (!$process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
}
