# auto_ebay_upload

Ein Tool zum **automatisierten Erstellen von eBay-Listings** aus Produktlisten (CSV oder manuell).  
Besonders nützlich z. B. für Händler von Düngemitteln oder ähnlichen Produkten mit vielen Varianten.

## Features
- **GUI** (grafische Oberfläche) mit:
  - Dry-Run (Testmodus ohne Upload)
  - CSV-Upload für viele Produkte auf einmal
  - Varianten-Modus: einzelne Listings oder Variationslisting
  - Preis-Logik: fester Preis (CSV/GUI) oder eBay-Durchschnitt −10 %
  - Vorschau mit Bildern + HTML-Ansicht im Browser
  - Protokoll-Export (CSV/XLSX)
  - SourceURL-Eingabe (optional)
  - JS-Rendering (Playwright) für Shops wie hortitec.de
  - Varianten-spezifischer Bildfilter
  - Automatische Übersetzung ins Deutsche, wenn Quelle anderssprachig ist

- **Datenquellen**:
  - Hortitec-Shop (mit JavaScript-Rendering)
  - Herstellerseiten (Fallback, z. B. hesi.nl)
  - Kuratierte Fallback-Beschreibungen, falls keine Beschreibung gefunden wird

- **Logs**:
  - `logs/session.log` – Ablaufprotokoll
  - `logs/gui_error.log` – GUI-Fehler

## Installation (Windows 11)

- **1. Projekt klonen oder ZIP entpacken:**
   ```bash
   git clone https://github.com/leonrfpv/auto_ebay_upload.git
   cd auto_ebay_upload

- **2. Installer ausführen**:
  - Doppelklick  `Install_Or_Update.bat`
  - Erstellt ein virtuelles Python-Umfeld (.venv), installiert alle Abhängigkeiten und Playwright/Chromium.

- **3. Grafisches Benutzerinterface Starten:**
  - `Run_GUI.bat` → normale Nutzung
  - `Run_GUI_Debug.bat` → startet mit Logausgabe
  - `Run_GUI_Failsafe.bat` → Notfall-Startskript

- **4. Konfiguration:**
  - In app/.env trägst du deine eBay-API-Daten ein (Vorlage: .env.sample).
  - Standardmäßig läuft alles im SANDBOX-Modus, bis du EBAY_ENV=PROD setzt.

- **5. Beispiel:**
  - Eine Beispiel-CSV liegt in `input/products_test.csv`.
  - Damit kannst du sofort einen Dry-Run machen und die HTML-Vorschau im Browser öffnen.
  
