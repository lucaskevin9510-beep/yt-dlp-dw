# yt-dlp-dw Windows 10/11 x64 per-user installer
[CmdletBinding()]
param(
    [string]$Repository = "lucaskevin9510-beep/yt-dlp-dw",
    [string]$RepositoryRef = $env:DW_REPO_REF
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepositoryRef)) {
    $RepositoryRef = "main"
}

$PythonVersion = "3.13.14"
$PythonArchive = "python-$PythonVersion-embed-amd64.zip"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonArchive"
$PythonSha256 = "90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907"
$RawBase = "https://raw.githubusercontent.com/$Repository/$RepositoryRef"
$AppDir = if ($env:DW_APP_DIR) { $env:DW_APP_DIR } else { Join-Path $env:LOCALAPPDATA "yt-dlp-dw" }
$StateDir = if ($env:DW_STATE_DIR) { $env:DW_STATE_DIR } else { Join-Path $env:LOCALAPPDATA "yt-dlp-dw-data" }
$CommandDir = if ($env:DW_COMMAND_DIR) { $env:DW_COMMAND_DIR } else { Join-Path $AppDir "command" }
$RuntimeDir = Join-Path $AppDir "python"
$PythonExe = Join-Path $RuntimeDir "python.exe"
$DwScript = Join-Path $AppDir "dw.py"
$CleanupScript = Join-Path $AppDir "uninstall-windows.ps1"
$Launcher = Join-Path $CommandDir "dw.cmd"
$AppMarker = Join-Path $AppDir ".dw-owned"
$StateMarker = Join-Path $StateDir ".dw-owned"
$TempDir = Join-Path ([IO.Path]::GetTempPath()) ("dw-install-" + [Guid]::NewGuid().ToString("N"))

function Fail([string]$Message) {
    throw "Install failed: $Message"
}

function Assert-WindowsPlatform {
    if ($env:OS -ne "Windows_NT") {
        Fail "this installer only supports Windows 10/11."
    }
    if ([Environment]::OSVersion.Version.Major -ne 10) {
        Fail "this installer only supports Windows 10/11."
    }
    $Architecture = if ($env:PROCESSOR_ARCHITEW6432) {
        $env:PROCESSOR_ARCHITEW6432
    } else {
        $env:PROCESSOR_ARCHITECTURE
    }
    if ($Architecture -notmatch "^(AMD64|x86_64)$") {
        Fail "only Windows x64/amd64 is supported; detected $Architecture."
    }
    if ([string]::IsNullOrWhiteSpace($env:LOCALAPPDATA) -or [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        Fail "LOCALAPPDATA or USERPROFILE is unavailable."
    }
}

function Assert-OwnedDirectory([string]$Path, [string]$Marker) {
    if (-not (Test-Path -LiteralPath $Path)) {
        return
    }
    $Item = Get-Item -LiteralPath $Path -Force
    if (-not $Item.PSIsContainer) {
        Fail "$Path exists but is not a directory."
    }
    if (($Item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Fail "$Path is a reparse point; refusing to modify it."
    }
    if (-not (Test-Path -LiteralPath $Marker -PathType Leaf)) {
        Fail "$Path exists but is not owned by dw."
    }
}

function Get-RemoteFile([string]$Uri, [string]$Destination) {
    Write-Host "Downloading $Uri"
    Invoke-WebRequest -UseBasicParsing -Uri $Uri -OutFile $Destination
    if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
        Fail "download did not create $Destination."
    }
    if ((Get-Item -LiteralPath $Destination).Length -le 0) {
        Fail "downloaded file is empty: $Uri"
    }
}

function Install-AtomicFile([string]$Source, [string]$Destination) {
    $Parent = Split-Path -Parent $Destination
    [IO.Directory]::CreateDirectory($Parent) | Out-Null
    $Temporary = Join-Path $Parent ("." + [IO.Path]::GetFileName($Destination) + "." + [Guid]::NewGuid().ToString("N") + ".tmp")
    $Backup = Join-Path $Parent ("." + [IO.Path]::GetFileName($Destination) + "." + [Guid]::NewGuid().ToString("N") + ".bak")
    [IO.File]::Copy($Source, $Temporary, $true)
    try {
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            try {
                [IO.File]::Replace($Temporary, $Destination, $Backup)
            } catch {
                if ((-not (Test-Path -LiteralPath $Destination)) -and (Test-Path -LiteralPath $Backup)) {
                    [IO.File]::Move($Backup, $Destination)
                }
                throw
            }
        } else {
            [IO.File]::Move($Temporary, $Destination)
        }
    } finally {
        if (Test-Path -LiteralPath $Temporary) {
            Remove-Item -LiteralPath $Temporary -Force -ErrorAction SilentlyContinue
        }
        if ((Test-Path -LiteralPath $Destination) -and (Test-Path -LiteralPath $Backup)) {
            Remove-Item -LiteralPath $Backup -Force -ErrorAction SilentlyContinue
        }
    }
}

function Get-NormalizedPath([string]$Path) {
    return [IO.Path]::GetFullPath($Path).TrimEnd("\")
}

function Add-UserPath([string]$Path) {
    $Normalized = Get-NormalizedPath $Path
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $Entries = @()
    if (-not [string]::IsNullOrWhiteSpace($UserPath)) {
        $Entries = @($UserPath -split ";" | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
    }
    $Found = $false
    foreach ($Entry in $Entries) {
        try {
            if ([string]::Equals((Get-NormalizedPath $Entry), $Normalized, [StringComparison]::OrdinalIgnoreCase)) {
                $Found = $true
                break
            }
        } catch {
            if ([string]::Equals($Entry.TrimEnd("\"), $Path.TrimEnd("\"), [StringComparison]::OrdinalIgnoreCase)) {
                $Found = $true
                break
            }
        }
    }
    if (-not $Found) {
        $Entries += $Path
        [Environment]::SetEnvironmentVariable("Path", ($Entries -join ";"), "User")
    }

    $CurrentEntries = @($env:Path -split ";")
    $CurrentFound = $false
    foreach ($Entry in $CurrentEntries) {
        try {
            if ([string]::Equals((Get-NormalizedPath $Entry), $Normalized, [StringComparison]::OrdinalIgnoreCase)) {
                $CurrentFound = $true
                break
            }
        } catch {
        }
    }
    if (-not $CurrentFound) {
        $env:Path = "$Path;$env:Path"
    }
}

function Install-PortablePython {
    $VersionFile = Join-Path $RuntimeDir "python-version.txt"
    if ((Test-Path -LiteralPath $PythonExe -PathType Leaf) -and (Test-Path -LiteralPath $VersionFile -PathType Leaf)) {
        $InstalledVersion = (Get-Content -LiteralPath $VersionFile -Raw).Trim()
        if ($InstalledVersion -eq $PythonVersion) {
            Write-Host "Portable Python $PythonVersion is already installed."
            return
        }
    }

    $ArchivePath = Join-Path $TempDir $PythonArchive
    $StagedRuntime = Join-Path $TempDir "python-runtime"
    Get-RemoteFile $PythonUrl $ArchivePath
    $ActualHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualHash -ne $PythonSha256) {
        Fail "portable Python SHA-256 verification failed."
    }
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $StagedRuntime -Force
    if (-not (Test-Path -LiteralPath (Join-Path $StagedRuntime "python.exe") -PathType Leaf)) {
        Fail "portable Python archive does not contain python.exe."
    }
    [IO.File]::WriteAllText((Join-Path $StagedRuntime "python-version.txt"), "$PythonVersion`r`n", [Text.UTF8Encoding]::new($false))

    $BackupRuntime = Join-Path $TempDir "python-backup"
    $HadRuntime = Test-Path -LiteralPath $RuntimeDir
    try {
        if ($HadRuntime) {
            Move-Item -LiteralPath $RuntimeDir -Destination $BackupRuntime
        }
        Move-Item -LiteralPath $StagedRuntime -Destination $RuntimeDir
    } catch {
        if ((-not (Test-Path -LiteralPath $RuntimeDir)) -and (Test-Path -LiteralPath $BackupRuntime)) {
            Move-Item -LiteralPath $BackupRuntime -Destination $RuntimeDir -ErrorAction SilentlyContinue
        }
        throw
    }
    Write-Host "Installed verified portable Python $PythonVersion."
}

function Resolve-InstallerSource([string]$RelativePath, [string]$RemotePath, [string]$TemporaryName) {
    if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        $LocalPath = Join-Path $PSScriptRoot $RelativePath
        if (Test-Path -LiteralPath $LocalPath -PathType Leaf) {
            return (Resolve-Path -LiteralPath $LocalPath).Path
        }
    }
    $Destination = Join-Path $TempDir $TemporaryName
    Get-RemoteFile "$RawBase/$RemotePath" $Destination
    return $Destination
}

Assert-WindowsPlatform
[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12

Assert-OwnedDirectory $AppDir $AppMarker
Assert-OwnedDirectory $StateDir $StateMarker

[IO.Directory]::CreateDirectory($TempDir) | Out-Null
try {
    [IO.Directory]::CreateDirectory($AppDir) | Out-Null
    [IO.Directory]::CreateDirectory($StateDir) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $StateDir "cache")) | Out-Null
    [IO.Directory]::CreateDirectory((Join-Path $StateDir "tasks")) | Out-Null
    [IO.Directory]::CreateDirectory($CommandDir) | Out-Null
    [IO.File]::WriteAllText($AppMarker, "owned by yt-dlp-dw`r`n", [Text.UTF8Encoding]::new($false))
    [IO.File]::WriteAllText($StateMarker, "owned by yt-dlp-dw`r`n", [Text.UTF8Encoding]::new($false))

    Install-PortablePython

    $SourceScript = Resolve-InstallerSource "src\dw.py" "src/dw.py" "dw.py"
    $SourceCleanup = Resolve-InstallerSource "windows\uninstall-cleanup.ps1" "windows/uninstall-cleanup.ps1" "uninstall-cleanup.ps1"
    if (-not (Select-String -LiteralPath $SourceScript -SimpleMatch "yt-dlp-dw" -Quiet)) {
        Fail "downloaded dw.py failed its identity check."
    }
    if (-not (Select-String -LiteralPath $SourceCleanup -SimpleMatch "dw-managed-windows-cleanup" -Quiet)) {
        Fail "downloaded cleanup helper failed its identity check."
    }
    Install-AtomicFile $SourceScript $DwScript
    Install-AtomicFile $SourceCleanup $CleanupScript

    if ((Test-Path -LiteralPath $Launcher -PathType Leaf) -and -not (Select-String -LiteralPath $Launcher -SimpleMatch "dw-managed-launcher" -Quiet)) {
        Fail "$Launcher exists and is not owned by dw."
    }
    $LauncherText = @"
@echo off
rem dw-managed-launcher
chcp 65001 >nul
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"
"$PythonExe" "$DwScript" %*
"@
    $LauncherSource = Join-Path $TempDir "dw.cmd"
    $LauncherText = $LauncherText -replace "`r?`n", "`r`n"
    [IO.File]::WriteAllText($LauncherSource, $LauncherText, [Text.ASCIIEncoding]::new())
    Install-AtomicFile $LauncherSource $Launcher

    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    Write-Host "Installing and verifying yt-dlp, Deno, FFmpeg, and FFprobe ..."
    & $PythonExe $DwScript --install-dependencies --force
    if ($LASTEXITCODE -ne 0) {
        Fail "dependency installation returned exit code $LASTEXITCODE."
    }
    if ($env:DW_SKIP_PATH_UPDATE -ne "1") {
        Add-UserPath $CommandDir
    }

    Write-Host ""
    Write-Host "Installation complete. Run: dw"
    Write-Host "Application: $AppDir"
    Write-Host "State:       $StateDir"
    Write-Host "Downloads:   your Windows Downloads known folder"
    Write-Host "Cookies:     $env:USERPROFILE\cookies.txt"
} finally {
    $ResolvedTempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd("\")
    $ResolvedTemp = [IO.Path]::GetFullPath($TempDir).TrimEnd("\")
    if ($ResolvedTemp.StartsWith($ResolvedTempRoot + "\dw-install-", [StringComparison]::OrdinalIgnoreCase)) {
        Remove-Item -LiteralPath $ResolvedTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
