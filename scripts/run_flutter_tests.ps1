# Run all Flutter tests with timing and summary
$ErrorActionPreference = "Stop"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$mobileDir = Join-Path (Split-Path -Parent $scriptDir) "mobile"

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host " Running Clinic System Pro Flutter Tests  " -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "Directory: $mobileDir" -ForegroundColor Gray

Push-Location $mobileDir

try {
    Write-Host "`n[1/2] Resolving dependencies..." -ForegroundColor Yellow
    flutter pub get

    Write-Host "`n[2/2] Running full test suite..." -ForegroundColor Yellow
    $stopwatch = [System.Diagnostics.Stopwatch]::StartNew()

    flutter test
    $testExitCode = $LASTEXITCODE

    $stopwatch.Stop()
    $elapsed = $stopwatch.Elapsed

    Write-Host "`n==========================================" -ForegroundColor Cyan
    if ($testExitCode -eq 0) {
        Write-Host " ALL TESTS PASSED!" -ForegroundColor Green
    } else {
        Write-Host " SOME TESTS FAILED (Exit Code: $testExitCode)" -ForegroundColor Red
    }
    Write-Host (" Duration: {0:mm\:ss\.ff}" -f $elapsed) -ForegroundColor Gray
    Write-Host "==========================================" -ForegroundColor Cyan

    exit $testExitCode
}
finally {
    Pop-Location
}
