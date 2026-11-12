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
Remove-Item -ErrorAction SilentlyContinue $report
$process = Start-Process -FilePath $Executable -ArgumentList @("`"--smoke-profile=$modlist`"", "`"--smoke-output=$report`"") -PassThru
try {
  $deadline = (Get-Date).AddSeconds(75)
  while (!(Test-Path $report) -and !$process.HasExited -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 400; $process.Refresh() }
  if (!(Test-Path $report)) { throw "Native WebView2 smoke did not produce a report. Exit=$($process.ExitCode)" }
  $data = Get-Content -Raw $report | ConvertFrom-Json
  if (!$data.ok) { throw "Native smoke failed: $(Get-Content -Raw $report)" }
  Get-Content -Raw $report
} finally {
  $process.Refresh()
  if (!$process.HasExited) { Stop-Process -Id $process.Id -ErrorAction SilentlyContinue }
}
