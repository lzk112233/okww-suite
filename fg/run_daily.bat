@echo off
chcp 65001 >nul
set WORK=C:\Users\lzk11\AppData\Local\ok-ww\data\apps\ok-ww\working
set PY=C:\Users\lzk11\AppData\Local\ok-ww\data\apps\ok-ww\python\pythonw.exe
cd /d "%WORK%"
echo [okww] launching daily task (headless, admin) ...
start "" "%PY%" main.py -t DailyTask -e -h
exit /b 0
