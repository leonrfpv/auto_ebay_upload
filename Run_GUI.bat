@echo off
setlocal
cd /d "%~dp0"

if not exist .venv (
  echo Bitte zuerst Install_Or_Update.bat ausfuehren.
  pause
  exit /b 1
)

call .venv\Scripts\activate
call .venv\Scripts\python.exe app\auto_ebay_upload.py

