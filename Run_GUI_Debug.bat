@echo off
setlocal
cd /d "%~dp0"

if not exist .venv (
  echo Bitte zuerst Install_Or_Update.bat ausfuehren.
  pause
  exit /b 1
)

set PYTHONUNBUFFERED=1

call .venv\Scripts\activate
echo Starte Debug-Run. Logs werden in logs\session.log und logs\gui_error.log geschrieben.

call .venv\Scripts\python.exe app\auto_ebay_upload.py 1>>"logs\session.log" 2>>"logs\gui_error.log"

echo Beendet. Druecken Sie eine Taste...
pause

