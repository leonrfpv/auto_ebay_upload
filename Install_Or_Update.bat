@echo off
setlocal ENABLEDELAYEDEXPANSION
title auto_ebay_upload Installer / Updater
cd /d "%~dp0"

:: Prüfen ob Python vorhanden ist
where python >nul 2>&1
if errorlevel 1 (
  echo Python nicht gefunden. Versuche Installation via winget...
  winget --version >nul 2>&1 || (
    echo winget ist nicht verfuegbar. Bitte installiere Python 3.11+ manuell und starte erneut.
    pause
    exit /b 1
  )
  winget install -e --id Python.Python.3.11
  if errorlevel 1 (
    echo Python-Installation fehlgeschlagen.
    pause
    exit /b 1
  )
)

:: Virtuelle Umgebung anlegen
if not exist .venv (
  echo Erzeuge virtuelle Umgebung...
  python -m venv .venv
  if errorlevel 1 (
    echo Konnte keine virtuelle Umgebung erstellen.
    pause
    exit /b 1
  )
)

call .venv\Scripts\activate
python -m pip install --upgrade pip

:: Abhängigkeiten installieren
if exist app\requirements.txt (
  echo Installiere Abhaengigkeiten...
  pip install -r app\requirements.txt
)

:: Playwright Browser installieren
echo Stelle sicher, dass Playwright Chromium installiert ist...
call .venv\Scripts\python.exe -m playwright install chromium
if exist ".venv\Scripts\playwright.cmd" (
  call ".venv\Scripts\playwright.cmd" install chromium
)

:: .env-Datei vorbereiten
if not exist app\.env (
  echo Erzeuge app\.env aus Vorlage...
  copy app\.env.sample app\.env >nul
)

echo.
echo Starte GUI...
call .venv\Scripts\python.exe app\auto_ebay_upload.py
echo.
pause

