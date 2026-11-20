@echo off
REM okww-suite runner -- see QUICKSTART.md
REM  Order: (1) scheduled task, no UAC  (2) elevate via UAC  (3) run as-is
REM  Set OKWW_NO_ELEVATE=1 to skip the admin check (only when already elevated).
chcp 65001 >nul
title okww-suite
setlocal

set "ROOT=%LOCALAPPDATA%\ok-ww"
if defined OKWW_ROOT set "ROOT=%OKWW_ROOT%"
set "WORK=%ROOT%\data\apps\ok-ww\working"
set "PY=%ROOT%\data\apps\ok-ww\python\python.exe"

if not exist "%WORK%\main.py" goto nopath
if not exist "%PY%" goto nopy

findstr /C:"'interaction': 'PyDirect'" "%WORK%\config.py" >nul
if errorlevel 1 goto notpy

set "TASKNAME=okww-suite-gui"
if /I "%~1"=="daily" set "TASKNAME=okww-suite-daily"

if defined OKWW_NO_ELEVATE goto local

schtasks /query /tn "%TASKNAME%" >nul 2>&1
if errorlevel 1 goto elevate
echo [okww] starting via scheduled task "%TASKNAME%" (no UAC prompt)
schtasks /run /tn "%TASKNAME%" >nul 2>&1
if errorlevel 1 goto elevate
goto wait

:elevate
fltmc >nul 2>&1
if not errorlevel 1 goto local
echo.
echo [okww] Administrator rights are required -- the game runs elevated, and a
echo        non-elevated process has its input silently dropped by UIPI.
echo.
echo [okww] Tip: run "setup.ps1 -InstallTask" once to get a shortcut that never
echo        asks for UAC again.
echo.
echo [okww] A UAC prompt is about to appear -- click YES.
echo.
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { Start-Process -FilePath '%~f0' -Verb RunAs -ErrorAction Stop; exit 0 } catch { Write-Host '[okww] elevation failed or was cancelled.' -ForegroundColor Red; exit 1 }"
if errorlevel 1 goto elevfail
echo [okww] elevated window started. This window closes now.
ping -n 4 127.0.0.1 >nul 2>&1
exit /b 0

:elevfail
echo.
echo [okww] ============================================================
echo [okww] The UAC prompt was cancelled, so nothing was started.
echo [okww] Fix: right-click run.bat and choose "Run as administrator".
echo [okww] ============================================================
pause
exit /b 1

:local
set "ARGS="
if /I "%~1"=="daily" goto setdaily
if not "%~1"=="" set "ARGS=-t %~1"
goto launch

:setdaily
set "ARGS=-t DailyTask"

:launch
echo [okww] launching %PY%
echo [okww] args      %ARGS%
echo [okww] workdir   %WORK%
cd /d "%WORK%"
start "" "%PY%" main.py %ARGS%
goto wait

:wait
echo [okww] waiting for python.exe ...
set "N=0"
:waitloop
ping -n 3 127.0.0.1 >nul 2>&1
set /a N+=2
tasklist /fi "imagename eq python.exe" 2>nul | findstr /I "python.exe" >nul
if not errorlevel 1 goto started
if %N% LSS 24 goto waitloop
goto noproc

:started
echo.
echo [okww] running:
tasklist /fi "imagename eq python.exe" 2>nul | findstr /I "python.exe"
echo.
echo [okww] The ok-ww top bar should read:  [game]  [WxH]  WGC  PyDirect
echo [okww] Closing this window in 6 seconds ...
ping -n 7 127.0.0.1 >nul 2>&1
exit /b 0

:nopath
echo.
echo [okww] NOT FOUND: "%WORK%\main.py"
echo [okww] set the folder that contains data\apps\ok-ww, e.g.:
echo         set OKWW_ROOT=D:\path\to\ok-ww
pause
exit /b 1

:nopy
echo.
echo [okww] NOT FOUND: "%PY%"
pause
exit /b 1

:notpy
echo.
echo [okww] config.py is not PyDirect yet, so ok-ww would still use PostMessage.
echo [okww] Run this once as administrator:
echo         powershell -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
pause
exit /b 1

:noproc
echo.
echo [okww] ============================================================
echo [okww] python.exe did not appear after 24 seconds.
echo [okww] Check the ok-ww log:
echo         %WORK%\logs\ok-script.log
echo [okww] ============================================================
pause
exit /b 1
