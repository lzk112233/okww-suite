@echo off
REM ===================================================================
REM  okww-suite runner
REM  Usage:
REM    run.bat            -> open ok-ww main window (GUI)
REM    run.bat daily      -> run DailyTask
REM    run.bat TASKNAME   -> run any task class, e.g. AutoCombatTask
REM  Auto-elevates to administrator (ok-ww needs it, see README sec.12).
REM ===================================================================
chcp 65001 >nul
setlocal EnableDelayedExpansion

REM ---- 1. need admin? re-launch elevated ---------------------------
net session >nul 2>&1
if errorlevel 1 (
    echo [okww] requesting administrator privileges ...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -Verb RunAs -ArgumentList '%*'"
    exit /b 0
)

REM ---- 2. locate ok-ww ---------------------------------------------
set "ROOT=%LOCALAPPDATA%\ok-ww"
if defined OKWW_ROOT set "ROOT=%OKWW_ROOT%"

set "WORK=%ROOT%\data\apps\ok-ww\working"
set "PY=%ROOT%\data\apps\ok-ww\python\python.exe"

if not exist "%WORK%\main.py" (
    echo [okww] NOT FOUND: "%WORK%\main.py"
    echo [okww] set OKWW_ROOT to the ok-ww install folder ^(the one with data\apps\ok-ww^)
    pause
    exit /b 1
)
if not exist "%PY%" (
    echo [okww] NOT FOUND: "%PY%"
    pause
    exit /b 1
)

REM ---- 3. must be PyDirect, otherwise we are still on PostMessage ---
findstr /C:"'interaction': 'PyDirect'" "%WORK%\config.py" >nul
if errorlevel 1 (
    echo [okww] config.py is NOT PyDirect. Run setup.ps1 first:
    echo        powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
    pause
    exit /b 1
)

REM ---- 4. launch (start "" detaches from this console) --------------
cd /d "%WORK%"

if "%~1"=="" (
    echo [okww] launching GUI ...
    start "" "%PY%" main.py
) else (
    if /I "%~1"=="daily" (
        echo [okww] launching DailyTask ...
        start "" "%PY%" main.py -t DailyTask
    ) else (
        echo [okww] launching task %~1 ...
        start "" "%PY%" main.py -t %~1
    )
)

echo [okww] done. Top bar should read:  <game>  <WxH>  WGC  PyDirect
exit /b 0
