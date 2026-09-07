# Screenshots & Daten

## Was du hier siehst

Du findest Screenshots der wichtigsten Bildschirme von Bitvavo-Autobot im Ordner `screen/`.

### Verfügbare Screenshots

| Bild | Inhalt | Hinweis |
|------|--------|---------|
| `01_login.png` | Anmeldung | Platzhalter |
| `02_dashboard.png` | Dashboard (Gesamtwert, Chart, Guthaben, Kryptowährungen) | Platzhalter |
| `03_positions.png` | Meine Kryptowährungen (Positionen) | Platzhalter |
| `04_wizard_anlegen.png` | Zeitplan anlegen (Wizard) | Platzhalter |
| `05_wizard_bestätigung.png` | Zeitplan gespeichert (Vorschau) | Platzhalter |
| `06_trades.png` | Trades | Platzhalter |
| `07_portfolio.png` | Portfolio & Steuern | Platzhalter |
| `08_einstellungen.png` | Einstellungen (API-Key, Berechtigungen) | Platzhalter |
| `09_system.png` | System | Platzhalter |
| `10_chart_toggle.png` | Chart-Ansicht (BTC-EUR / Portfolio-Wert) | Platzhalter |

### Hinweis zu den Daten

- **Echte Daten** (z. B. eigene Coins, Trades, Bestände, Zeitpläne) werden in der Datenbank gespeichert.
- **Screenshots** können manuell aufgenommen werden, sobald die App mit eigenen Daten läuft.
- **Keine Secrets** in den Screenshots: Scheinbar muss kein echter API-Key oder Secret
  sichtbar sein. Im besten Fall werden echte Coins/Bestände/Balance angezeigt, aber keine API-Keys.

### Eigene Screenshots aufnehmen

1. App starten (`docker compose up -d --build` oder `python3 app.py`)
2. Im Browser (z. B. Firefox, Chrome) mit der App verbinden
3. Screenshots übernehmen (z. B. mit Snipping Tool, Screenshot-Tool des Browsers,
   oder Selenium/Playwright)
4. Screenshots im Ordner `screen/` ablegen (PNG, sinnvoll komprimiert)

### Automatisierte Screenshots (optional)

Es gibt ein Skript `screen/screenshot_tool.py`, das Screenshots mit Selenium
aufnehmen kann. Voraussetzung: `selenium` installiert, ein Browser (Firefox oder Chrome)
und der jeweilige Driver (geckodriver, chromedriver) verfügbar.

```bash
pip install selenium
python3 screen/screenshot_tool.py
```

Achtung: Selenium öffnet einen Browser, der ggf. private Daten (API-Key, Trades,
Bestände) lädt. Verwenden Sie eigene Testdaten oder entfernen Sie sensible Angaben
vor einer Veröffentlichung.

## Verweis auf Portfolio-Webseite

Die Screenshots sind für die eigene Webseite gedacht:

- README.md verweist auf die Bilder (Markdown-Links)
- LEITFADEN.md gibt einen Überblick über die Funktion (für die Beschreibung)
- Screenshots sollten keine API-Keys oder Secrets enthalten

### Empfehlung

Bevor Sie Screenshots veröffentlichen:

- Prüfen, ob API-Keys, Secrets oder persönliche Daten sichtbar sind
- Gegebenenfalls sensitive Angaben abdecken oder Screenshots mit Testdaten aufnehmen
- Layout/Größe anpassen (mindestens 1200 px Breite für gute Lesbarkeit)
