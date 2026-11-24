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
$capture = "$Output\fixture-desktop.png"
$captureAcknowledgement = [IO.Path]::ChangeExtension($report, 'capture-complete')
$checkpoint = "$Output\native-source-checkpoint.json"
python "$PSScriptRoot/bind_native_proof.py" --executable $Executable --checkpoint $checkpoint
if ($LASTEXITCODE -ne 0) { throw 'Native desktop source checkpoint failed.' }
Remove-Item -ErrorAction SilentlyContinue $report, $events, $capture, $captureAcknowledgement
Add-Type -AssemblyName System.Windows.Forms, System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class ModhunterSmokeWindow {
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr window, out uint processId);
}
'@
# The executable places fixture configuration and WebView2 storage under its
# own temporary root. Independently check the user's persistent preferences.
function Get-NormalPreferenceState {
  $root = Join-Path ([Environment]::GetFolderPath('ApplicationData')) 'com.modhunter.desktop'
  foreach ($name in @('native-preferences.json','watchtower.json','watchtower-notifications.json','maintenance.json')) {
    $item = Join-Path $root $name
    if (Test-Path -LiteralPath $item -PathType Leaf) {
      $hash = (Get-FileHash -LiteralPath $item -Algorithm SHA256).Hash
      "$name $hash $([IO.File]::GetLastWriteTimeUtc($item).Ticks)"
    } else {
      "$name absent"
    }
  }
}
$normalSettings = @(Get-NormalPreferenceState)
$process = Start-Process -FilePath $Executable -ArgumentList @("`"--smoke-profile=$modlist`"", "`"--smoke-output=$report`"") -RedirectStandardOutput "$Output\stdout.log" -RedirectStandardError "$Output\stderr.log" -PassThru
try {
  $deadline = (Get-Date).AddSeconds(75)
  while (!(Test-Path $report) -and !$process.HasExited -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 400; $process.Refresh() }
  if (!(Test-Path $report)) { throw "Native WebView2 smoke did not produce a report. Exit=$($process.ExitCode)" }
  $data = Get-Content -Raw $report | ConvertFrom-Json
  if (!$data.ok) { throw "Native smoke failed: $(Get-Content -Raw $report)" }
  # The app keeps its real rendered window alive until this exact process's
  # screenshot is saved. Never accept a picture of the runner after app exit.
  $activation = New-Object -ComObject WScript.Shell
  try {
    $focusDeadline = (Get-Date).AddSeconds(3)
    $focused = $false
    do {
      $process.Refresh()
      if ($process.HasExited) { throw 'Native app exited before desktop capture.' }
      $null = $activation.AppActivate($process.Id)
      Start-Sleep -Milliseconds 100
      [uint32]$foregroundOwner = 0
      $null = [ModhunterSmokeWindow]::GetWindowThreadProcessId([ModhunterSmokeWindow]::GetForegroundWindow(), [ref]$foregroundOwner)
      $focused = $foregroundOwner -eq $process.Id
    } while (!$focused -and (Get-Date) -lt $focusDeadline)
    if (!$focused) { throw 'The actual native app could not own foreground desktop capture.' }
    $bounds = [System.Windows.Forms.Screen]::PrimaryScreen.Bounds
    $image = [System.Drawing.Bitmap]::new($bounds.Width, $bounds.Height)
    $graphics = [System.Drawing.Graphics]::FromImage($image)
    try { $graphics.CopyFromScreen($bounds.Location, [System.Drawing.Point]::Empty, $bounds.Size); $image.Save($capture, [System.Drawing.Imaging.ImageFormat]::Png) }
    finally { $graphics.Dispose(); $image.Dispose() }
    if ((Get-Item -LiteralPath $capture).Length -eq 0) { throw 'Native desktop capture is empty.' }
    Set-Content -LiteralPath $captureAcknowledgement -Encoding ascii -NoNewline -Value $process.Id
  } finally { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($activation) }
  if (!$process.WaitForExit(5000)) { throw 'Native app did not acknowledge completed desktop capture.' }
  if ($process.ExitCode -ne 0) { throw "Native app rejected desktop capture. Exit=$($process.ExitCode)" }
  if (Compare-Object $normalSettings @(Get-NormalPreferenceState)) {
    throw 'The normal application settings changed while the desktop fixture was running.'
  }
  python "$PSScriptRoot/bind_native_proof.py" --executable $Executable --checkpoint $checkpoint --report $report
  if ($LASTEXITCODE -ne 0) { throw 'Native desktop receipt does not match unchanged source and executable.' }
  Get-Content -Raw $report
} finally {
  $process.Refresh()
  @{ hasExited = $process.HasExited; exitCode = if ($process.HasExited) { $process.ExitCode } else { $null }; windowTitle = $process.MainWindowTitle; windowHandle = $process.MainWindowHandle.ToInt64() } | ConvertTo-Json | Set-Content -Encoding utf8 "$Output\process.json"
  foreach ($log in @($events, "$Output\stdout.log", "$Output\stderr.log")) {
    if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 80 }
  }
  if (!$process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
}
