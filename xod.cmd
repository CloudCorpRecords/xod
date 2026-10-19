@echo off
rem Xod in the terminal (app\cli.py) on Windows. Uses the same data, models and keys as the desktop app.
rem   xod "question"      xod      xod status      (add this folder to PATH: see docs\TERMINAL.md)
setlocal
set "PY=%~dp0.venv\Scripts\python.exe"
if not exist "%PY%" if defined XOD_PYTHON set "PY=%XOD_PYTHON%"
if not exist "%PY%" (
  echo Xod's Python environment isn't set up yet: run .\run.ps1 once. 1>&2
  exit /b 1
)
set PYTHONUTF8=1
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
"%PY%" -m app.cli %*
exit /b %ERRORLEVEL%
