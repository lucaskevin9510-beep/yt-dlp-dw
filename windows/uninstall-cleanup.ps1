# dw-managed-windows-cleanup
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][int]$ParentPid,
    [Parameter(Mandatory = $true)][string]$AppDir,
    [Parameter(Mandatory = $true)][string]$StateDir,
    [Parameter(Mandatory = $true)][string]$CommandDir
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

function Normalize-Path([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd("\")
}

function Assert-ManagedTree([string]$Path) {
    $Normalized = Normalize-Path $Path
    $LocalAppData = Normalize-Path $env:LOCALAPPDATA
    if (-not $Normalized.StartsWith($LocalAppData + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove a path outside LOCALAPPDATA: $Normalized"
    }
    if (-not (Test-Path -LiteralPath $Normalized -PathType Container)) {
        return
    }
    $Item = Get-Item -LiteralPath $Normalized -Force
    if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "Refusing to remove a reparse point: $Normalized"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $Normalized ".dw-owned") -PathType Leaf)) {
        throw "Ownership marker is missing: $Normalized"
    }
}

function Remove-ManagedTree([string]$Path) {
    $Normalized = Normalize-Path $Path
    if (-not (Test-Path -LiteralPath $Normalized)) {
        return $null
    }
    for ($Attempt = 1; $Attempt -le 20; $Attempt++) {
        try {
            Remove-Item -LiteralPath $Normalized -Recurse -Force -ErrorAction Stop
            if (-not (Test-Path -LiteralPath $Normalized)) {
                return $null
            }
        } catch {
            if ($Attempt -eq 20) {
                return "$Normalized : $($_.Exception.Message)"
            }
        }
        Start-Sleep -Milliseconds 250
    }
    return "$Normalized : removal did not complete"
}

function Remove-UserPathEntry([string]$Path) {
    $Target = Normalize-Path $Path
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ([string]::IsNullOrWhiteSpace($UserPath)) {
        return $null
    }
    $Remaining = New-Object System.Collections.Generic.List[string]
    foreach ($Entry in ($UserPath -split ";")) {
        if ([string]::IsNullOrWhiteSpace($Entry)) {
            continue
        }
        $Matches = $false
        try {
            $Matches = [string]::Equals((Normalize-Path $Entry), $Target, [StringComparison]::OrdinalIgnoreCase)
        } catch {
            $Matches = [string]::Equals($Entry.TrimEnd("\"), $Path.TrimEnd("\"), [StringComparison]::OrdinalIgnoreCase)
        }
        if (-not $Matches) {
            $Remaining.Add($Entry)
        }
    }
    try {
        [Environment]::SetEnvironmentVariable("Path", ($Remaining -join ";"), "User")
        return $null
    } catch {
        return "User PATH : $($_.Exception.Message)"
    }
}

$Failures = New-Object System.Collections.Generic.List[string]
try {
    $NormalizedApp = Normalize-Path $AppDir
    $NormalizedState = Normalize-Path $StateDir
    $NormalizedCommand = Normalize-Path $CommandDir
    if (-not $NormalizedCommand.StartsWith($NormalizedApp + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Command directory is outside the managed application directory."
    }
    Assert-ManagedTree $NormalizedApp
    Assert-ManagedTree $NormalizedState

    while (Get-Process -Id $ParentPid -ErrorAction SilentlyContinue) {
        Start-Sleep -Milliseconds 200
    }
    Start-Sleep -Milliseconds 300

    Write-Host "Completing yt-dlp-dw uninstall ..."
    $AppFailure = Remove-ManagedTree $NormalizedApp
    if ($AppFailure) {
        $Failures.Add($AppFailure)
    } else {
        $StateFailure = Remove-ManagedTree $NormalizedState
        if ($StateFailure) {
            $Failures.Add($StateFailure)
        }
        if ($env:DW_SKIP_PATH_UPDATE -ne "1") {
            $PathFailure = Remove-UserPathEntry $NormalizedCommand
            if ($PathFailure) {
                $Failures.Add($PathFailure)
            }
        }
    }
} catch {
    $Failures.Add($_.Exception.Message)
}

if ($Failures.Count -gt 0) {
    Write-Host "Uninstall is incomplete. Residual items:" -ForegroundColor Red
    foreach ($Failure in $Failures) {
        Write-Host "  $Failure" -ForegroundColor Red
    }
    exit 1
}

Write-Host "yt-dlp-dw, portable dependencies, state, cache, and tracked downloads were removed."
Write-Host "Open a new terminal to refresh PATH."
