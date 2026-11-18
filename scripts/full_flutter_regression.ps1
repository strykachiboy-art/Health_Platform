& {
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
        "$env:USERPROFILE\Documents\clinic-system-p25-offline-contracts"
    }

    $mobile = Join-Path $repo 'mobile'
    $logsRoot = "$env:USERPROFILE\Downloads\CSP_Full_Flutter_Regression"
    $stamp = Get-Date -Format 'yyyyMMdd_HHmmss_fff'
    $runDir = Join-Path $logsRoot $stamp

    Write-Host "=================================================="
    Write-Host "CLINIC SYSTEM PRO - FULL FLUTTER REGRESSION"
    Write-Host "HOST-ONLY / NO DEVICE / NO SOURCE MUTATION"
    Write-Host "=================================================="

    if (!(Test-Path (Join-Path $mobile 'pubspec.yaml'))) {
        throw "FLUTTER_PROJECT_NOT_FOUND"
    }

    if (!(Get-Command flutter -ErrorAction SilentlyContinue)) {
        throw "FLUTTER_COMMAND_NOT_FOUND"
    }

    if (!(Get-Command git -ErrorAction SilentlyContinue)) {
        throw "GIT_COMMAND_NOT_FOUND"
    }

    New-Item -ItemType Directory -Path $runDir -Force | Out-Null

    $before = @(
        & git -C $repo status --porcelain=v1 --untracked-files=all
    )
    if ($LASTEXITCODE -ne 0) {
        throw "GIT_PREFLIGHT_FAILED"
    }

    $branch = & git -C $repo branch --show-current
    $head = & git -C $repo rev-parse HEAD

    Write-Host "BRANCH=$branch"
    Write-Host "HEAD=$head"
    Write-Host "LOG_DIRECTORY=$runDir"

    $analyzeLog = Join-Path $runDir '01_flutter_analyze.log'
    $testLog = Join-Path $runDir '02_full_flutter_test.log'

    $gate1 = 'NOT_RUN'
    $gate2 = 'NOT_RUN'
    $failure = $null

    Push-Location $mobile

    try {
        Write-Host ""
        Write-Host "===== GATE 1: FULL FLUTTER ANALYZER ====="

        $ErrorActionPreference = 'Continue'

        & flutter analyze --no-pub 2>&1 |
            Tee-Object -FilePath $analyzeLog

        $analyzeExit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'

        if ($analyzeExit -ne 0) {
            $gate1 = "FAIL_EXIT_$analyzeExit"
            throw "FULL_FLUTTER_ANALYZER_FAILED"
        }

        $gate1 = 'PASS'

        Write-Host ""
        Write-Host "===== GATE 2: ALL FLUTTER TESTS ====="

        $ErrorActionPreference = 'Continue'

        & flutter test --no-pub --reporter expanded test 2>&1 |
            Tee-Object -FilePath $testLog

        $testExit = $LASTEXITCODE
        $ErrorActionPreference = 'Stop'

        if ($testExit -ne 0) {
            $gate2 = "FAIL_EXIT_$testExit"
            throw "FULL_FLUTTER_REGRESSION_FAILED"
        }

        $gate2 = 'PASS'
    }
    catch {
        $failure = $_.Exception.Message
    }
    finally {
        $ErrorActionPreference = 'Stop'
        Pop-Location
    }

    Write-Host ""
    Write-Host "===== GATE 3: SOURCE PRESERVATION ====="

    $after = @(
        & git -C $repo status --porcelain=v1 --untracked-files=all
    )
    if ($LASTEXITCODE -ne 0) {
        throw "GIT_POSTTEST_CHECK_FAILED"
    }

    $sourcePreserved = (
        (@($before) -join "`n") -ceq
        (@($after) -join "`n")
    )

    $gate3 = if ($sourcePreserved) { 'PASS' } else { 'FAIL' }

    Write-Host ""
    Write-Host "=================================================="
    Write-Host "FULL FLUTTER REGRESSION RESULTS"
    Write-Host "=================================================="
    Write-Host "FULL_ANALYZER=$gate1"
    Write-Host "FULL_FLUTTER_TESTS=$gate2"
    Write-Host "SOURCE_PRESERVATION=$gate3"
    Write-Host "ANDROID_DEVICE_TESTS=NOT_RUN"
    Write-Host "BACKEND_DATABASE=UNTOUCHED"
    Write-Host "COMMIT_PUSH=NO"
    Write-Host "ANALYZER_LOG=$analyzeLog"
    Write-Host "TEST_LOG=$testLog"

    if ($failure -or !$sourcePreserved) {
        Write-Host "FULL_FLUTTER_REGRESSION=RED"
        Write-Host "FAILURE=$failure"
        throw "FULL_REGRESSION_STOP_INSPECT_LOGS"
    }

    Write-Host "FULL_FLUTTER_REGRESSION=GREEN"
    Write-Host "ALL_HOST_TESTS_PASSED=YES"
    Write-Host "=================================================="
}
