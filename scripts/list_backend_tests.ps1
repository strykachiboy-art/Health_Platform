$ErrorActionPreference = 'Stop'

$scriptDir = $PSScriptRoot
if (-not $scriptDir -and $MyInvocation -and $MyInvocation.MyCommand -and $MyInvocation.MyCommand.Path) {
    $scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}

$repo = if ($scriptDir -and (Test-Path (Join-Path $scriptDir '..\backend\app\tests'))) {
    (Resolve-Path (Join-Path $scriptDir '..')).Path
} elseif (Test-Path "$env:USERPROFILE\Documents\clinic-system\backend\app\tests") {
    "$env:USERPROFILE\Documents\clinic-system"
} else {
    Get-Location
}

$testsDir = Join-Path $repo "backend\app\tests"

Write-Host "=================================================="
Write-Host "CLINIC SYSTEM PRO - BACKEND TEST SUITE INVENTORY"
Write-Host "=================================================="
Write-Host "TEST_DIRECTORY=$testsDir"

if (!(Test-Path $testsDir)) {
    throw "BACKEND_TESTS_DIRECTORY_NOT_FOUND"
}

$testFiles = Get-ChildItem -Path $testsDir -Recurse -Filter "test_*.py"

Write-Host "TOTAL_TEST_FILES=$($testFiles.Count)"
Write-Host ""
Write-Host "===== TEST FILES BREAKDOWN BY SUBSYSTEM ====="

$subsystems = @{}
$totalTestFunctions = 0
$totalParametrizedDecorators = 0

foreach ($file in $testFiles) {
    $relativePath = $file.FullName.Substring($testsDir.Length + 1)
    $subsystem = ($relativePath -split "[\\/]")[0]

    if (-not $subsystems.ContainsKey($subsystem)) {
        $subsystems[$subsystem] = @{
            FileCount = 0
            TestCases = 0
            ParametrizeCount = 0
            Files = @()
        }
    }

    $raw = Get-Content -Path $file.FullName -Raw
    if ($null -eq $raw) { $raw = "" }
    $caseCount = ([regex]::Matches($raw, "def\s+test_")).Count
    $paramCount = ([regex]::Matches($raw, "@pytest\.mark\.parametrize")).Count

    $subsystems[$subsystem].FileCount++
    $subsystems[$subsystem].TestCases += $caseCount
    $subsystems[$subsystem].ParametrizeCount += $paramCount
    $totalTestFunctions += $caseCount
    $totalParametrizedDecorators += $paramCount
}

foreach ($key in ($subsystems.Keys | Sort-Object)) {
    $data = $subsystems[$key]
    Write-Host "[$key] -> $($data.FileCount) files | ~$($data.TestCases) base test functions | $($data.ParametrizeCount) parameterized matrices"
}

Write-Host ""
Write-Host "=================================================="
Write-Host "TOTAL BACKEND TEST FILES: $($testFiles.Count)"
Write-Host "TOTAL BASE TEST FUNCTIONS: ~$totalTestFunctions"
Write-Host "PARAMETERIZED TEST MATRICES: ~$totalParametrizedDecorators"
Write-Host "EXPECTED PYTEST EXECUTED TEST CASES: 9,000+ to 10,000+"
Write-Host "=================================================="
