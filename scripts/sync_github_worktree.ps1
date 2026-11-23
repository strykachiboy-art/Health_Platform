$ErrorActionPreference = 'Stop'

$scriptDir = $PSScriptRoot
if (-not $scriptDir -and $MyInvocation -and $MyInvocation.MyCommand -and $MyInvocation.MyCommand.Path) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

$repo = if ($scriptDir -and (Test-Path (Join-Path $scriptDir '..\mobile\pubspec.yaml'))) {
    (Resolve-Path (Join-Path $scriptDir '..')).Path
} elseif (Test-Path "$env:USERPROFILE\Documents\clinic-system\mobile\pubspec.yaml") {
    "$env:USERPROFILE\Documents\clinic-system"
} else {
    Get-Location
}

Write-Host "=================================================="
Write-Host "CLINIC SYSTEM PRO - GITHUB WORKTREE SYNC UTILITY"
Write-Host "=================================================="

if (!(Get-Command git -ErrorAction SilentlyContinue)) {
    throw "GIT_COMMAND_NOT_FOUND"
}

# Pre-flight working directory status check
$uncommitted = @(
    & git -C $repo status --porcelain=v1
)

$branch = & git -C $repo branch --show-current
if (-not $branch) {
    $branch = "DETACHED_HEAD"
}

$localHead = & git -C $repo rev-parse HEAD

Write-Host "REPOSITORY=$repo"
Write-Host "CURRENT_BRANCH=$branch"
Write-Host "LOCAL_HEAD=$localHead"

Write-Host ""
Write-Host "===== FETCHING LATEST GITHUB REFERENCES ====="
& git -C $repo fetch origin
if ($LASTEXITCODE -ne 0) {
    throw "GIT_FETCH_FAILED"
}

# Determine tracking branch / remote commit
$remoteBranch = "origin/$branch"
$remoteHead = & git -C $repo rev-parse $remoteBranch 2>$null

if (-not $remoteHead) {
    Write-Host "STATUS=NO_REMOTE_TRACKING_BRANCH"
    Write-Host "Branch $branch does not exist on origin."
    exit 0
}

Write-Host "REMOTE_HEAD=$remoteHead"

if ($localHead -eq $remoteHead) {
    Write-Host ""
    Write-Host "=================================================="
    Write-Host "STATUS: ALREADY UP TO DATE (Local matches GitHub)"
    Write-Host "=================================================="
    exit 0
}

# Check merge base to see if local is behind remote
$base = & git -C $repo merge-base HEAD $remoteBranch

if ($base -eq $localHead) {
    Write-Host ""
    Write-Host "STATUS: LOCAL IS BEHIND GITHUB (Updates available)"

    if ($uncommitted.Count -gt 0) {
        Write-Host "WARNING: Uncommitted changes detected in working tree."
        Write-Host "Skipping automatic merge to protect uncommitted work."
        Write-Host "Please commit or stash your changes, then run: git merge --ff-only $remoteBranch"
        exit 0
    }

    Write-Host "Executing Fast-Forward Merge..."
    & git -C $repo merge --ff-only $remoteBranch
    if ($LASTEXITCODE -ne 0) {
        throw "FAST_FORWARD_MERGE_FAILED"
    }

    $newHead = & git -C $repo rev-parse HEAD
    Write-Host ""
    Write-Host "=================================================="
    Write-Host "SUCCESS: WORKTREE UPDATED TO MATCH GITHUB!"
    Write-Host "NEW_LOCAL_HEAD=$newHead"
    Write-Host "=================================================="
} elseif ($base -eq $remoteHead) {
    Write-Host ""
    Write-Host "=================================================="
    Write-Host "STATUS: LOCAL IS AHEAD OF GITHUB (Unpushed local commits)"
    Write-Host "=================================================="
} else {
    Write-Host ""
    Write-Host "=================================================="
    Write-Host "STATUS: DIVERGED (Local and remote have different commits)"
    Write-Host "=================================================="
}
