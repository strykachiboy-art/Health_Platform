@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "MOBILE_DIR=%SCRIPT_DIR%..\mobile"

echo ==========================================
echo  Running Clinic System Pro Flutter Tests
echo ==========================================
echo Directory: %MOBILE_DIR%

pushd "%MOBILE_DIR%"
if errorlevel 1 (
    echo Failed to access mobile directory.
    exit /b 1
)

echo.
echo [1/2] Resolving dependencies...
call flutter pub get
if errorlevel 1 (
    echo flutter pub get failed.
    popd
    exit /b %errorlevel%
)

echo.
echo [2/2] Running full test suite...
call flutter test
set "TEST_EXIT_CODE=%errorlevel%"

popd

echo.
echo ==========================================
if "%TEST_EXIT_CODE%"=="0" (
    echo  ALL TESTS PASSED!
) else (
    echo  SOME TESTS FAILED (Exit Code: %TEST_EXIT_CODE%)
)
echo ==========================================

exit /b %TEST_EXIT_CODE%
