#requires -Version 7.5
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$StagedRoot,
    [Parameter(Mandatory)][string]$PinnedStockArchive
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
if (!$IsWindows -or $env:GITHUB_ACTIONS -cne 'true' -or $env:RUNNER_ENVIRONMENT -cne 'github-hosted' -or
    $env:GITHUB_REPOSITORY -cne 'Herbertofury/Modhunter' -or
    $env:GITHUB_REF -cne 'refs/heads/diagnostics/stock-mo2-source-ab-ec9-89b' -or
    $env:GITHUB_EVENT_NAME -cne 'push') { throw 'Requires a new GitHub-hosted diagnostic runner' }
$Root = [IO.Path]::GetFullPath($StagedRoot)
$Ownership = [IO.Path]::GetFullPath($env:RUNNER_TEMP).TrimEnd('\') + '\'
if (!$Root.StartsWith($Ownership, [StringComparison]::OrdinalIgnoreCase) -or
    !(Test-Path -LiteralPath (Join-Path $Root 'payload-verification.json') -PathType Leaf)) {
    throw 'Expected the staged synthetic diagnostic under RUNNER_TEMP'
}
$GlobalMO2Root = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'ModOrganizer'
$NxM = 'Registry::HKEY_CLASSES_ROOT\nxm'
$Work = Join-Path $Root 'identical-profile-root'
$Receipts = Join-Path $Root 'receipts'
$Done = Join-Path $Root 'finished-work'
if ((Test-Path -LiteralPath $GlobalMO2Root) -or (Test-Path -LiteralPath $NxM) -or
    (Test-Path -LiteralPath $Work) -or (Test-Path -LiteralPath $Receipts) -or (Test-Path -LiteralPath $Done)) {
    throw 'Existing MO2 handler/state or trial workspace; refuse to proceed'
}
New-Item -ItemType Directory -Path $Receipts, $Done | Out-Null

function Reset-OwnedNoRegisterMarker {
    # Only a marker which this clean hosted trial had just generated may be
    # removed. Never recurse, touch registry, clean other settings, or proceed
    # if official MO2 created anything else in the global stand-in.
    if (Test-Path -LiteralPath $NxM) { throw 'Unexpected registration, stop without modifying it' }
    $Folder = Get-Item -LiteralPath $GlobalMO2Root -Force
    if (!$Folder.PSIsContainer -or ($Folder.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Unexpected linked disposable global root; preserved'
    }
    $Contents = @(Get-ChildItem -LiteralPath $Folder.FullName -Force)
    if ($Contents.Count -ne 1 -or $Contents[0].Name -cne 'nxmhandler.ini' -or $Contents[0].PSIsContainer -or
        ($Contents[0].Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'Unexpected disposable global MO2 state; preserved, comparison stopped'
    }
    $Marker = $Contents[0]
    if ($Marker.Length -ne 28 -or
        [Convert]::ToHexString([IO.File]::ReadAllBytes($Marker.FullName)) -cne '5B47656E6572616C5D0D0A6E6F72656769737465723D747275650D0A') {
        throw 'The generated no-registration marker changed; comparison stopped'
    }
    [IO.File]::Delete($Marker.FullName)
    [IO.Directory]::Delete($Folder.FullName, $false)
}

$Arms = @('A', 'B', 'B', 'A')
for ($i = 0; $i -lt $Arms.Count; $i++) {
    $Arm = $Arms[$i]
    $Label = 'trial-{0:d2}-{1}' -f ($i + 1), $Arm
    $TrialEvidence = Join-Path $Root $Label
    & (Join-Path $Root "arm-$Arm/stock_scale.ps1") -RepositoryRoot (Join-Path $Root "input-$Arm") `
        -QtBridgePath (Join-Path $Root 'controlled-qt-bridge.dll') -PinnedMo2Archive $PinnedStockArchive `
        -WorkDirectory $Work -EvidenceDirectory $TrialEvidence -TimeoutSeconds 240 | Out-Null
    # Strictly preserve previous receipts and collect only two JSONs, never
    # stock logs, temporary files or synthetic profile backups.
    $Review = Join-Path $Receipts $Label
    New-Item -ItemType Directory -Path $Review | Out-Null
    foreach ($File in @('native-receipt.json','result.json')) {
        Copy-Item -LiteralPath (Join-Path $TrialEvidence $File) -Destination (Join-Path $Review $File)
    }
    Reset-OwnedNoRegisterMarker
    Move-Item -LiteralPath $Work -Destination (Join-Path $Done $Label)
}
python (Join-Path $PSScriptRoot 'compare.py') --staged $Root
if ($LASTEXITCODE -ne 0) { throw 'Stock comparison not qualified for causal interpretation' }
