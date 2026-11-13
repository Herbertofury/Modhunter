#requires -Version 7.2
# Static-preflight regression only; no real MO2, Qt, game or account is run.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$Script = (Resolve-Path (Join-Path $PSScriptRoot '../Check_MO2_Readiness.ps1')).Path
$PS51 = Join-Path $env:WINDIR 'System32/WindowsPowerShell/v1.0/powershell.exe'
$Root = Join-Path ([IO.Path]::GetTempPath()) ('modhunter preflight ' + [guid]::NewGuid().ToString('N'))
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
    Write-Output 'PASS: Windows PowerShell 5.1 parses and runs the preflight; invalid binary/missing runtime/package remain review; no installation data changed; noninteractive errors stay credential-free.'
} finally {
    if (Test-Path -LiteralPath $Root) { Remove-Item -LiteralPath $Root -Recurse -Force }
}
# The final child deliberately failed. Declare harness success only after all
# assertions and cleanup complete, instead of leaking that expected exit code
# into GitHub Actions' generated pwsh wrapper. A thrown assertion skips this.
exit 0
