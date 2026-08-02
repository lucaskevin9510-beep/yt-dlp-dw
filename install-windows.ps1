# yt-dlp-dw Windows 10/11 x64 per-user installer
[CmdletBinding()]
param(
    [string]$Repository = "lucaskevin9510-beep/yt-dlp-dw",
    [string]$RepositoryRef = $env:DW_REPO_REF,
    [string]$UpdateRef = $env:DW_UPDATE_REF
)

Set-StrictMode -Version 2.0
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($RepositoryRef)) {
    $RepositoryRef = "main"
}
if ([string]::IsNullOrWhiteSpace($UpdateRef)) {
    $UpdateRef = $RepositoryRef
}
if ($Repository -notmatch "^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$") {
    throw "Install failed: invalid repository name."
}
foreach ($RefValue in @($RepositoryRef, $UpdateRef)) {
    if (
        $RefValue -notmatch "^[A-Za-z0-9._/-]+$" -or
        $RefValue.StartsWith("/") -or $RefValue.StartsWith("-") -or
        $RefValue.EndsWith("/") -or $RefValue.Contains("..") -or $RefValue.Contains("//")
    ) {
        throw "Install failed: invalid repository reference."
    }
}

$PythonVersion = "3.13.14"
$PythonArchive = "python-$PythonVersion-embed-amd64.zip"
$PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/$PythonArchive"
$PythonSha256 = "90b4e5b9898b72d744650524bff92377c367f44bd5fbd09e3148656c080ad907"
$RawBase = "https://raw.githubusercontent.com/$Repository/$RepositoryRef"
$JsDelivrBase = "https://cdn.jsdelivr.net/gh/$Repository@$RepositoryRef"
$BuiltInGithubMirrors = @(
    "https://gh-proxy.com/",
    "https://ghfast.top/",
    "https://ghproxy.net/"
)
$AppDir = if ($env:DW_APP_DIR) { $env:DW_APP_DIR } else { Join-Path $env:LOCALAPPDATA "yt-dlp-dw" }
$StateDir = if ($env:DW_STATE_DIR) { $env:DW_STATE_DIR } else { Join-Path $env:LOCALAPPDATA "yt-dlp-dw-data" }
$CommandDir = if ($env:DW_COMMAND_DIR) { $env:DW_COMMAND_DIR } else { Join-Path $AppDir "command" }
$RuntimeDir = Join-Path $AppDir "python"
$PythonExe = Join-Path $RuntimeDir "python.exe"
$DwScript = Join-Path $AppDir "dw.py"
$CleanupScript = Join-Path $AppDir "uninstall-windows.ps1"
$UpdateScript = Join-Path $AppDir "update-windows.ps1"
$Launcher = Join-Path $CommandDir "dw.cmd"
$AliasDir = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
$AliasLauncher = Join-Path $AliasDir "dw.cmd"
$AppMarker = Join-Path $AppDir ".dw-owned"
$StateMarker = Join-Path $StateDir ".dw-owned"
$MirrorFile = Join-Path $StateDir "github-mirrors.txt"
$UpdateConfig = Join-Path $StateDir "update-source.json"
$TempDir = Join-Path ([IO.Path]::GetTempPath()) ("dw-install-" + [Guid]::NewGuid().ToString("N"))
$CustomMirrorFailureText = [Text.Encoding]::UTF8.GetString(
    [Convert]::FromBase64String("5L2g5o+Q5L6b55qE6ZWc5YOP5Z+f5ZCN5LiN5Y+v5L2/55So")
)
$WorkingCustomMirrors = New-Object System.Collections.Generic.List[string]

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

function Get-ConfiguredGithubMirrors {
    $Values = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($env:DW_GITHUB_MIRRORS)) {
        foreach ($Value in ($env:DW_GITHUB_MIRRORS -split "[;,`r`n]+")) {
            $Values.Add($Value)
        }
    }
    if (Test-Path -LiteralPath $MirrorFile -PathType Leaf) {
        foreach ($Value in (Get-Content -LiteralPath $MirrorFile)) {
            $Values.Add($Value)
        }
    }

    $Mirrors = New-Object System.Collections.Generic.List[string]
    foreach ($RawValue in $Values) {
        $Mirror = $RawValue.Trim()
        if ([string]::IsNullOrWhiteSpace($Mirror) -or $Mirror.StartsWith("#") -or $Mirrors.Contains($Mirror)) {
            continue
        }
        $Probe = $Mirror.Replace("{url}", "https://github.com/")
        try {
            $Parsed = [Uri]$Probe
            if ($Parsed.Scheme -ne "https" -or [string]::IsNullOrWhiteSpace($Parsed.Host)) {
                throw "invalid mirror"
            }
            $Mirrors.Add($Mirror)
        } catch {
            Write-Warning "$CustomMirrorFailureText`: $Mirror"
        }
    }
    return @($Mirrors)
}

function Apply-GithubMirror([string]$Mirror, [string]$Uri) {
    if ($Mirror.Contains("{url}")) {
        return $Mirror.Replace("{url}", $Uri)
    }
    return $Mirror.TrimEnd("/") + "/" + $Uri
}

function Get-GithubCandidates([string]$Uri, [string]$RepositoryPath) {
    $Candidates = New-Object System.Collections.Generic.List[object]
    foreach ($Mirror in (Get-ConfiguredGithubMirrors)) {
        $Candidates.Add([pscustomobject]@{
            Uri = Apply-GithubMirror $Mirror $Uri
            Source = $Mirror
            Custom = $true
        })
    }
    $Candidates.Add([pscustomobject]@{ Uri = $Uri; Source = "GitHub official"; Custom = $false })
    if (-not [string]::IsNullOrWhiteSpace($RepositoryPath)) {
        $Candidates.Add([pscustomobject]@{
            Uri = "$JsDelivrBase/$RepositoryPath"
            Source = "https://cdn.jsdelivr.net/"
            Custom = $false
        })
    }
    foreach ($Mirror in $BuiltInGithubMirrors) {
        $Candidates.Add([pscustomobject]@{
            Uri = Apply-GithubMirror $Mirror $Uri
            Source = $Mirror
            Custom = $false
        })
    }
    return @($Candidates | Group-Object -Property Uri | ForEach-Object { $_.Group[0] })
}

function Format-ByteSize([double]$Bytes) {
    $Units = @("B", "KiB", "MiB", "GiB", "TiB")
    $Index = 0
    while ($Bytes -ge 1024 -and $Index -lt ($Units.Count - 1)) {
        $Bytes /= 1024
        $Index++
    }
    return ("{0:N1} {1}" -f $Bytes, $Units[$Index])
}

function Save-HttpFileWithProgress([string]$Uri, [string]$Destination) {
    $Response = $null
    $InputStream = $null
    $OutputStream = $null
    $LineActive = $false
    try {
        $Request = [Net.HttpWebRequest][Net.WebRequest]::Create($Uri)
        $Request.AllowAutoRedirect = $true
        $Request.UserAgent = "yt-dlp-dw-installer/1.3.0"
        $Request.Timeout = 90000
        $Request.ReadWriteTimeout = 90000
        $Response = [Net.HttpWebResponse]$Request.GetResponse()
        $InputStream = $Response.GetResponseStream()
        $OutputStream = [IO.File]::Open(
            $Destination,
            [IO.FileMode]::Create,
            [IO.FileAccess]::Write,
            [IO.FileShare]::None
        )
        $Total = [long]$Response.ContentLength
        $Downloaded = [long]0
        $Buffer = New-Object byte[] (1024 * 1024)
        $Timer = [Diagnostics.Stopwatch]::StartNew()
        $LastUpdate = [long]-1000
        $LastWidth = 0
        $Label = [IO.Path]::GetFileName($Destination)

        while (($Read = $InputStream.Read($Buffer, 0, $Buffer.Length)) -gt 0) {
            $OutputStream.Write($Buffer, 0, $Read)
            $Downloaded += $Read
            if (($Timer.ElapsedMilliseconds - $LastUpdate) -ge 250) {
                $Seconds = [Math]::Max($Timer.Elapsed.TotalSeconds, 0.001)
                $Speed = (Format-ByteSize ($Downloaded / $Seconds)) + "/s"
                if ($Total -gt 0) {
                    $Percent = [Math]::Min(($Downloaded * 100.0 / $Total), 100.0)
                    $Line = "Progress $Label`: $($Percent.ToString('F1'))% | $(Format-ByteSize $Downloaded) / $(Format-ByteSize $Total) | $Speed"
                } else {
                    $Line = "Progress $Label`: $(Format-ByteSize $Downloaded) | $Speed"
                }
                $LastWidth = [Math]::Max($LastWidth, $Line.Length)
                Write-Host ("`r  " + $Line.PadRight($LastWidth)) -NoNewline
                $LineActive = $true
                $LastUpdate = $Timer.ElapsedMilliseconds
            }
        }
        $OutputStream.Flush()
        if ($Total -gt 0 -and $Downloaded -ne $Total) {
            throw "incomplete download: received $Downloaded of $Total bytes"
        }
        $Seconds = [Math]::Max($Timer.Elapsed.TotalSeconds, 0.001)
        $Speed = (Format-ByteSize ($Downloaded / $Seconds)) + "/s"
        if ($Total -gt 0) {
            $Line = "Progress $Label`: 100.0% | $(Format-ByteSize $Downloaded) / $(Format-ByteSize $Total) | $Speed"
        } else {
            $Line = "Progress $Label`: $(Format-ByteSize $Downloaded) | $Speed"
        }
        $LastWidth = [Math]::Max($LastWidth, $Line.Length)
        Write-Host ("`r  " + $Line.PadRight($LastWidth))
        $LineActive = $false
    } finally {
        if ($OutputStream) {
            $OutputStream.Dispose()
        }
        if ($InputStream) {
            $InputStream.Dispose()
        }
        if ($Response) {
            $Response.Dispose()
        }
        if ($LineActive) {
            Write-Host ""
        }
    }
}

function Get-RemoteFile(
    [string]$Uri,
    [string]$Destination,
    [string]$RepositoryPath = "",
    [string]$Identity = ""
) {
    $IsGithub = $Uri -match "^https://(raw\.githubusercontent\.com|github\.com|api\.github\.com)/"
    $Candidates = if ($IsGithub) {
        Get-GithubCandidates $Uri $RepositoryPath
    } else {
        @([pscustomobject]@{ Uri = $Uri; Source = "official"; Custom = $false })
    }
    $LastError = "unknown error"
    foreach ($Candidate in $Candidates) {
        if (Test-Path -LiteralPath $Destination) {
            Remove-Item -LiteralPath $Destination -Force -ErrorAction SilentlyContinue
        }
        try {
            Write-Host "Downloading $($Candidate.Uri)"
            Save-HttpFileWithProgress $Candidate.Uri $Destination
            if (-not (Test-Path -LiteralPath $Destination -PathType Leaf)) {
                throw "download did not create $Destination"
            }
            if ((Get-Item -LiteralPath $Destination).Length -le 0) {
                throw "downloaded file is empty"
            }
            if (-not [string]::IsNullOrWhiteSpace($Identity) -and -not (
                Select-String -LiteralPath $Destination -SimpleMatch $Identity -Quiet
            )) {
                throw "downloaded file failed its identity check"
            }
            if ($Candidate.Custom -and -not $WorkingCustomMirrors.Contains($Candidate.Source)) {
                $WorkingCustomMirrors.Add($Candidate.Source)
            }
            if ($Candidate.Uri -ne $Uri) {
                Write-Host "Using GitHub mirror: $($Candidate.Source)"
            }
            return
        } catch {
            $LastError = $_.Exception.Message
            if ($Candidate.Custom) {
                Write-Warning "$CustomMirrorFailureText`: $($Candidate.Source)"
            }
        }
    }
    Fail "all download sources failed for $Uri ($LastError)."
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

function Test-ManagedLauncher([string]$Path) {
    return (Test-Path -LiteralPath $Path -PathType Leaf) -and (
        Select-String -LiteralPath $Path -SimpleMatch "dw-managed-launcher" -Quiet
    )
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

function Resolve-InstallerSource(
    [string]$RelativePath,
    [string]$RemotePath,
    [string]$TemporaryName,
    [string]$Identity
) {
    if (-not [string]::IsNullOrWhiteSpace($PSScriptRoot)) {
        $LocalPath = Join-Path $PSScriptRoot $RelativePath
        if (Test-Path -LiteralPath $LocalPath -PathType Leaf) {
            return (Resolve-Path -LiteralPath $LocalPath).Path
        }
    }
    $Destination = Join-Path $TempDir $TemporaryName
    Get-RemoteFile "$RawBase/$RemotePath" $Destination $RemotePath $Identity
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

    $SourceScript = Resolve-InstallerSource "src\dw.py" "src/dw.py" "dw.py" "yt-dlp-dw"
    $SourceCleanup = Resolve-InstallerSource "windows\uninstall-cleanup.ps1" "windows/uninstall-cleanup.ps1" "uninstall-cleanup.ps1" "dw-managed-windows-cleanup"
    $SourceUpdate = Resolve-InstallerSource "windows\update-runner.ps1" "windows/update-runner.ps1" "update-runner.ps1" "dw-managed-windows-update"
    if (-not (Select-String -LiteralPath $SourceScript -SimpleMatch "yt-dlp-dw" -Quiet)) {
        Fail "downloaded dw.py failed its identity check."
    }
    if (-not (Select-String -LiteralPath $SourceCleanup -SimpleMatch "dw-managed-windows-cleanup" -Quiet)) {
        Fail "downloaded cleanup helper failed its identity check."
    }
    if (-not (Select-String -LiteralPath $SourceUpdate -SimpleMatch "dw-managed-windows-update" -Quiet)) {
        Fail "downloaded update helper failed its identity check."
    }
    Install-AtomicFile $SourceScript $DwScript
    Install-AtomicFile $SourceCleanup $CleanupScript
    Install-AtomicFile $SourceUpdate $UpdateScript

    $UpdateConfigSource = Join-Path $TempDir "update-source.json"
    $UpdateConfigText = [ordered]@{
        repository = $Repository
        ref = $UpdateRef
    } | ConvertTo-Json
    [IO.File]::WriteAllText($UpdateConfigSource, $UpdateConfigText + "`n", [Text.UTF8Encoding]::new($false))
    Install-AtomicFile $UpdateConfigSource $UpdateConfig

    if ($WorkingCustomMirrors.Count -gt 0) {
        [IO.File]::WriteAllLines($MirrorFile, @($WorkingCustomMirrors), [Text.UTF8Encoding]::new($false))
    }

    if ((Test-Path -LiteralPath $Launcher -PathType Leaf) -and -not (Test-ManagedLauncher $Launcher)) {
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

    $AliasInstalled = $false
    if (Test-Path -LiteralPath $AliasDir -PathType Container) {
        if ((Test-Path -LiteralPath $AliasLauncher) -and -not (Test-ManagedLauncher $AliasLauncher)) {
            Write-Warning "$AliasLauncher already exists and is not owned by dw; it was not replaced."
        } else {
            Install-AtomicFile $LauncherSource $AliasLauncher
            $AliasInstalled = $true
        }
    } else {
        Write-Warning "$AliasDir is unavailable; the immediate command alias could not be installed."
    }

    $env:PYTHONUTF8 = "1"
    $env:PYTHONIOENCODING = "utf-8"
    Write-Host "Installing and verifying yt-dlp, Deno, FFmpeg, FFprobe, and aria2c ..."
    & $PythonExe $DwScript --install-dependencies --force
    if ($LASTEXITCODE -ne 0) {
        Fail "dependency installation returned exit code $LASTEXITCODE."
    }
    if ($env:DW_SKIP_PATH_UPDATE -ne "1") {
        Add-UserPath $CommandDir
    }

    Write-Host ""
    if ($AliasInstalled -and (Get-Command dw.cmd -ErrorAction SilentlyContinue)) {
        Write-Host "Installation complete. The dw command is available now and in new PowerShell tabs."
    } else {
        Write-Host "Installation complete. Run: dw"
        Write-Warning "If this terminal still has an old PATH, run the exact launcher below once:"
        Write-Host "& `"$Launcher`""
    }
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
