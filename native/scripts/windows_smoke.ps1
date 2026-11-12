param([Parameter(Mandatory=$true)][string]$Executable, [string]$Output = "$env:TEMP\modhunter-native-smoke")
$ErrorActionPreference = 'Stop'
New-Item -ItemType Directory -Force -Path "$Output\fixture\profiles\Native QA", "$Output\fixture\mods" | Out-Null
Set-Content -Encoding UTF8 -Path "$Output\fixture\ModOrganizer.ini" -Value "[General]`ngameName=Skyrim Special Edition`n[Settings]`n"
$entries = @('# Native Windows WebView2 fixture only')
for ($i=3; $i -ge 1; $i--) {
  $name = "Native Fixture $i"
  New-Item -ItemType Directory -Force -Path "$Output\fixture\mods\$name" | Out-Null
  Set-Content -Encoding UTF8 -Path "$Output\fixture\mods\$name\meta.ini" -Value "[General]`nversion=1.0`nnewestVersion=2.0`nmodid=$i`n"
  $entries += "+$name"
}
$modlist = "$Output\fixture\profiles\Native QA\modlist.txt"
Set-Content -Encoding UTF8 -Path $modlist -Value $entries
$report = "$Output\native-smoke.json"
$events = "$Output\native-smoke.events.jsonl"
Remove-Item -ErrorAction SilentlyContinue $report, $events
$process = Start-Process -FilePath $Executable -ArgumentList @("`"--smoke-profile=$modlist`"", "`"--smoke-output=$report`"") -RedirectStandardOutput "$Output\stdout.log" -RedirectStandardError "$Output\stderr.log" -PassThru
try {
  $deadline = (Get-Date).AddSeconds(75)
  while (!(Test-Path $report) -and !$process.HasExited -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 400; $process.Refresh() }
  if (!(Test-Path $report)) { throw "Native WebView2 smoke did not produce a report. Exit=$($process.ExitCode)" }
  $data = Get-Content -Raw $report | ConvertFrom-Json
  if (!$data.ok) { throw "Native smoke failed: $(Get-Content -Raw $report)" }
  Get-Content -Raw $report
} finally {
  $process.Refresh()
  @{ hasExited = $process.HasExited; exitCode = if ($process.HasExited) { $process.ExitCode } else { $null }; windowTitle = $process.MainWindowTitle; windowHandle = $process.MainWindowHandle.ToInt64() } | ConvertTo-Json | Set-Content -Encoding utf8 "$Output\process.json"
  foreach ($log in @($events, "$Output\stdout.log", "$Output\stderr.log")) {
    if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 80 }
  }
  try {
    Add-Type -AssemblyName System.Windows.Forms, System.Drawing
    $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $image = [System.Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
    $graphics = [System.Drawing.Graphics]::FromImage($image)
    try { $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size); $image.Save("$Output\fixture-desktop.png", [System.Drawing.Imaging.ImageFormat]::Png) }
    finally { $graphics.Dispose(); $image.Dispose() }
  } catch { Write-Warning "Could not capture the native fixture desktop: $_" }
  if (!$process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
}
