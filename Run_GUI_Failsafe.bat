@echo off
setlocal ENABLEDELAYEDEXPANSION
cd /d "%~dp0"

echo ===== Starte Failsafe-Launcher =====
echo Aktueller Ordner: %CD%

:: Prüfen ob Hauptskript vorhanden ist
if not exist "app\auto_ebay_upload.py" (
  echo FEHLER: app\auto_ebay_upload.py fehlt in %CD%
  pause
  exit /b 1
)

:: Virtuelle Umgebung sicherstellen
if not exist ".venv\Scripts\python.exe" (
  echo Erzeuge virtuelle Umgebung...
  py -3 -m venv .venv 2>nul || python -m venv .venv
)

if not exist ".venv\Scripts\python.exe" (
  echo FEHLER: Virtuelle Umgebung fehlt. Bitte zuerst Install_Or_Update.bat ausfuehren.
  pause
  exit /b 1
)

call ".venv\Scripts\activate"

:: Abhängigkeiten prüfen
echo Pruefe Abhaengigkeiten...
python - <<PY
try:
    import bs4, lxml, requests
    print("OK: requirements vorhanden")
except Exception:
    print("Installiere requirements...")
    import subprocess, sys
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", "app\\requirements.txt"])
PY

:: Playwright sicherstellen
echo Stelle sicher, dass Playwright Chromium installiert ist...
call ".venv\Scripts\python.exe" -m playwright install chromium
if exist ".venv\Scripts\playwright.cmd" (
  call ".venv\Scripts\playwright.cmd" install chromium
)

:: GUI starten
echo Starte GUI...
call ".venv\Scripts\python.exe" "app\auto_ebay_upload.py"
pause

