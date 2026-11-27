@echo off
chcp 65001 >nul
setlocal
set ROOT=%~dp0
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
"%ROOT%.venv\Scripts\python.exe" "%ROOT%run.py"
pause
