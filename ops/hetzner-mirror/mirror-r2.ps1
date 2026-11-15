# One-way, resumable Cloudflare R2 -> Hetzner Windows mirror.
# Run initially WITHOUT -Apply to preview; -Apply performs the copy.
# No deletions, no public HTTP listener, and no credentials stored in this file.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Destination,
    [string]$Remote = "r2",
    [string]$Bucket = "spicychat-archiv",
    [string]$RcloneConfig = "",
    [int]$MinimumFreeGB = 12,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

if ($Remote -notmatch '^[A-Za-z][A-Za-z0-9_-]*$') {
    throw "Remote must be a configured rclone remote name (for example r2)."
}
if ($Bucket -notmatch '^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$') {
    throw "Bucket has an invalid S3 name."
}
if ($MinimumFreeGB -lt 1) {
    throw "MinimumFreeGB must be positive."
}

$rclone = Get-Command "rclone.exe" -ErrorAction Stop
$localPath = [System.IO.Path]::GetFullPath(
    [Environment]::ExpandEnvironmentVariables($Destination)
)
$root = [System.IO.Path]::GetPathRoot($localPath)
if ($localPath.TrimEnd('\', '/') -eq $root.TrimEnd('\', '/')) {
    throw "Refusing to mirror directly into the drive root; choose a dedicated folder."
}
if (-not (Test-Path -LiteralPath $root)) {
    throw "The destination volume is not available: $root"
}
if ($RcloneConfig -and -not (Test-Path -LiteralPath $RcloneConfig -PathType Leaf)) {
    throw "The specified rclone config does not exist: $RcloneConfig"
}

$disk = New-Object System.IO.DriveInfo($root)
$freeBytes = [int64]$disk.AvailableFreeSpace
$minimumBytes = [int64]$MinimumFreeGB * 1000000000L
if ($freeBytes -lt $minimumBytes) {
    throw ("Insufficient destination free space: {0:N2} GB available, {1} GB required. " +
           "The existing bucket has hundreds of thousands of objects, so allow room for NTFS overhead.") -f
        ($freeBytes / 1000000000.0), $MinimumFreeGB
}

New-Item -Path $localPath -ItemType Directory -Force | Out-Null
$control = Join-Path $localPath ".mirror-control"
New-Item -Path $control -ItemType Directory -Force | Out-Null
$lockPath = Join-Path $control "mirror.lock"
$lock = $null
try {
    $lock = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
} catch {
    throw "Another mirror run appears to be active (could not lock $lockPath)."
}

$mode = if ($Apply) { "copy" } else { "dry-run" }
$started = [DateTime]::UtcNow.ToString("o")
$log = Join-Path $control ("mirror-" + [DateTime]::UtcNow.ToString("yyyyMMdd-HHmmss") + ".log")
$exitCode = 1

try {
    $source = $Remote + ":" + $Bucket
    $arguments = @(
        "copy", $source, $localPath,
        "--use-server-modtime",
        "--transfers", "8",
        "--checkers", "16",
        "--tpslimit", "30",
        "--tpslimit-burst", "30",
        "--retries", "5",
        "--low-level-retries", "5",
        "--stats", "5m",
        "--log-level", "INFO",
        "--log-file", $log
    )
    if ($RcloneConfig) {
        $arguments += @("--config", $RcloneConfig)
    }
    if (-not $Apply) {
        $arguments += "--dry-run"
    }

    Write-Host "$mode R2 mirror: $source -> $localPath"
    Write-Host "Uses rclone copy (never sync), so local archival files are not deleted."
    & $rclone.Source @arguments
    $exitCode = [int]$LASTEXITCODE

    $state = @{
        schemaVersion = 1
        source = $source
        destination = $localPath
        startedAt = $started
        finishedAt = [DateTime]::UtcNow.ToString("o")
        mode = $mode
        exitCode = $exitCode
        status = if ($exitCode -eq 0) { "completed" } else { "failed" }
        logPath = $log
        note = "A successful copy is not, by itself, proof of a full independently verified backup."
    }
    $statusPath = Join-Path $control "last-run.json"
    $tempPath = Join-Path $control "last-run.tmp"
    $state | ConvertTo-Json -Depth 3 | Set-Content -Path $tempPath -Encoding UTF8
    Move-Item -Path $tempPath -Destination $statusPath -Force

    if ($exitCode -ne 0) {
        Write-Warning "Mirror run failed (rclone exit code $exitCode). The next run can resume."
    } else {
        Write-Host "Mirror run completed. Status: $statusPath"
    }
} finally {
    if ($null -ne $lock) { $lock.Dispose() }
}
exit $exitCode
