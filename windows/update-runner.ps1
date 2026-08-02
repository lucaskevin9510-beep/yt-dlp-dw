# dw-managed-windows-update
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][int]$ParentPid,
    [Parameter(Mandatory = $true)][string]$Installer,
    [Parameter(Mandatory = $true)][string]$StateDir,
    [Parameter(Mandatory = $true)][string]$Repository,
    [Parameter(Mandatory = $true)][string]$RepositoryRef
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Normalize-Path([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd("\")
}

$ExitCode = 1
$NormalizedInstaller = $null
$UpdateDir = $null
try {
    if ($Repository -notmatch "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") {
        throw "Invalid update repository."
    }
    if (
        $RepositoryRef -notmatch "^[A-Za-z0-9._/-]+$" -or
        $RepositoryRef.StartsWith("/") -or $RepositoryRef.StartsWith("-") -or
        $RepositoryRef.EndsWith("/") -or $RepositoryRef.Contains("..") -or
        $RepositoryRef.Contains("//")
    ) {
        throw "Invalid update repository reference."
    }

    $NormalizedState = Normalize-Path $StateDir
    $NormalizedInstaller = Normalize-Path $Installer
    $UpdateDir = Normalize-Path (Join-Path $NormalizedState "update")
    if (-not (Test-Path -LiteralPath $NormalizedState -PathType Container)) {
        throw "The managed state directory is missing."
    }
    $StateItem = Get-Item -LiteralPath $NormalizedState -Force
    if (($StateItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "The managed state directory is a reparse point."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $NormalizedState ".dw-owned") -PathType Leaf)) {
        throw "The managed state directory has no ownership marker."
    }
    if (-not $NormalizedInstaller.StartsWith($UpdateDir + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "The update installer is outside the managed update directory."
    }
    if (-not (Test-Path -LiteralPath $NormalizedInstaller -PathType Leaf)) {
        throw "The update installer is missing."
    }
    $InstallerItem = Get-Item -LiteralPath $NormalizedInstaller -Force
    if (($InstallerItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "The update installer is a reparse point."
    }
    if (-not (Select-String -LiteralPath $NormalizedInstaller -SimpleMatch "yt-dlp-dw Windows 10/11" -Quiet)) {
        throw "The update installer failed its identity check."
    }

    while (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        Start-Sleep -Milliseconds 200
    }
    Start-Sleep -Milliseconds 300

    Write-Host "Updating yt-dlp-dw from $Repository@$RepositoryRef ..."
    & $NormalizedInstaller -Repository $Repository -RepositoryRef $RepositoryRef -UpdateRef $RepositoryRef
    if (-not $?) {
        throw "The update installer returned a failure."
    }
    Write-Host "yt-dlp-dw update completed. Run dw again to use the new version."
    $ExitCode = 0
} catch {
    Write-Host "yt-dlp-dw update failed: $($_.Exception.Message)" -ForegroundColor Red
} finally {
    if ($NormalizedInstaller -and (Test-Path -LiteralPath $NormalizedInstaller -PathType Leaf)) {
        Remove-Item -LiteralPath $NormalizedInstaller -Force -ErrorAction SilentlyContinue
    }
    if ($UpdateDir -and (Test-Path -LiteralPath $UpdateDir -PathType Container)) {
        $Remaining = @(Get-ChildItem -LiteralPath $UpdateDir -Force -ErrorAction SilentlyContinue)
        if ($Remaining.Count -eq 0) {
            Remove-Item -LiteralPath $UpdateDir -Force -ErrorAction SilentlyContinue
        }
    }
}

exit $ExitCode
