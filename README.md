# Bitvavo-Autobot

Automatisierter Krypto-Assistent für die Börse **Bitvavo** mit einer modernen
Bootstrap-Weboberfläche. Zeitgesteuerte Käufe (DCA), Handelsregeln, Trade-Historie-Import,
Kontostand, technische Indikatoren und ein Portfolio-/Steuer-Dashboard (FIFO, 1-Jahres-Frist).

Dieses Projekt dient als Portfolio-Beispiel — der Code, die Struktur und die Dokumentation
sind so aufgebaut, dass man die App lokal oder im Docker-Container starten und nach Belieben
weiterbauen kann.

Verwendet die **offizielle Bitvavo-SDK** (`python-bitvavo-api`) aus dem
Repository [bitvavo/python-bitvavo-api](https://github.com/bitvavo/python-bitvavo-api).

---

## Was die App kann

- **Dashboard**: Übersicht aller Zeitpläne, technische Indikatoren (SMA9, SMA50, RSI14,
  ATH innerhalb von 90 Tagen), Chart mit wählbarem Zeitraum (1T, 7T, 30T, 1J, ALLE) und
  wählbarem Modus (BTC-EUR oder Portfolio-Wert), Gesamtwert des Portfolios und
  verfügbares Guthaben.
- **Geführter Erststart**: Schritt-für-Schritt-Assistent (API-Key → Historie importieren →
  Portfolio prüfen → Zeitplan anlegen) mit Fortschrittsanzeige und Sprung-Button. Der Assistent
  verschwindet automatisch, wenn alle Schritte erledigt sind. Man kann ihn dauerhaft
  ausblenden.
- **Zeitgesteuerte Käufe**: Wochentag + Uhrzeit, bis zu 5 Positionen pro Plan mit
  EUR-Betrag. Optional Handelsregeln (Max-Kaufpreis, Mindestabstand zum ATH, SMA-Regel,
  RSI-Band, Max. Gesamtinvest). E-Mail-Benachrichtigung ist optional.
- **Assistent zum Anlegen/Bearbeiten**: 3-Schritte-Flow (Was kaufen? → Wann kaufen? → Sicherheitsregeln)
  mit Erklärtexten in Alltagssprache.
- **Trade-Historie-Import**: Ein Klick holt alle Bitvavo-Transaktionen (alle EUR-Märkte),
  dedupliziert anhand der Bitvavo-Trade-ID. Zeitraum wählbar (letzte 365, 1095, 3650 Tage oder
  komplette Historie seit Kontoerstellung). Optionaler CSV-Upload.
- **Transfers-Ein-/Auszahlungen**: Dedupliziert üer die Bitvavo-Tx-ID. Einzahlungen werden
  als FIFO-Kauf-Lots zum Tageskurs verbucht. Auszahlungen reduzieren nur den Bestand
  ohne Steuerereignis.
- **Portfolio & Steuern**: FIFO-Berechnung je Coin (First-In-First-Out), Aufteilung in
  steuerfrei (> 1 Jahr) / steuerpflichtig (≤ 1 Jahr). Realisierte Gewinne aus Verkäufen,
  600-€-Freigrenze, Warnung "bald steuerfrei". FIFO-CSV-Export je Steuerjahr (Excel-kompatibel).
- **Verkaufsempfehlungen**: Format mit Badges und Tooltips:
  - steuerfreie Coins im Plus
  - Kurs nahe am 90-Tage-Hoch (+ Tipp: jetzt steuerfrei verkaufen)
  - Rebalancing-Warnung (> 40 % eines Coins)
  - Gewinnziel: X % verkaufen, wenn +Y % Gewinn und steuerfrei (Standardwerte einstellbar)
  - bald steuerfrei / steuerpflichtiger Verlust
  - Verkaufen mit Bestätigung: Prozent-Eingabe → echte Bitvavo-Market-Order (benötigt
    API-Key mit Handelsrechten; bei Read-only-Key erscheint eine Warnung).
- **Einstellungen**: Bitvavo-API-Key + Secret (Secret wird verschlüsselt in der
  SQLite-Datenbank gespeichert), Berechtigungs-Check des Keys, SMTP-E-Mail-Einstellungen,
  Option "Angemeldet bleiben" (Passwort wird clientseitig verschlüsselt gespeichert),
  Steuer-/Verkaufsempfehlungs-Schwellen konfigurierbar, Assistent an/aus.
- **System-Seite**: App-Version, SDK-Version (`python-bitvavo-api`), Python-Version,
  Zeitzone, DB-/Log-Pfad mit Größe, Eintrags-Zählungen, Betriebsmodus (Simulation/Echtbetrieb),
  API-Key-Status inkl. Berechtigungen, Startup-Selbstcheck-Ergebnisse, Scheduler-Status.
- **Healthcheck**: `/health` (ohne Login) als maschinenlesbares JSON für Docker und
  `verifizieren.sh`. Ein Bash-Skript `verifizieren.sh` prüft Erreichbarkeit, /health,
  lokale Dateien und Python-Umgebung (optional `--strict`).

---

## Keine Secrets im Projekt

**Wichtig**: Das Archiv enthält keine echten Secrets. Es gibt nur eine Vorlage `.env.example`.
Das eigentliche `.env` mit Passwörtern/API-Keys/Keys gehört nicht in die Versionsverwaltung
(`.gitignore` schließt es aus).

Die Anmeldung erfolgt über ein Passwort, das über eine Umgebungsvariable (`MASTER_PASSWORD`)
oder via `.env` gesetzt wird. Das Beispiel in `.env.example` zeigt lediglich einen Platzhalter.

---

## Installation

### Voraussetzungen
- Docker mit Compose-Plugin (empfohlen)
- oder Python 3.9+ lokal

### Variante A — Docker

```bash
# 1) Konfiguration anlegen (Platzhalter — dann Werte anpassen)
cp .env.example .env
# Werte anpassen: MASTER_PASSWORD, FLASK_SECRET_KEY, ENCRYPTION_KEY
#   ENCRYPTION_KEY erzeugen z.B. mit: openssl rand -hex 32

# 2) Container bauen und starten
docker compose up -d --build

# 3) Im Browser öffnen
#    http://localhost:8050
```

Nach dem ersten Start erstmals in den **Einstellungen → Bitvavo-API** einen
API-Key + Secret hinterlegen (Secret wird verschlüsselt gespeichert).

### Variante B — Lokal mit Python

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Optional: Nur Simulation (keine echten Orders)
SIMULATION_MODE=true python3 app.py

# Normal starten (App lädt .env automatisch)
python3 app.py
```

Im Browser: http://localhost:5000

---

## Erster Aufbau

1. **API-Key + Secret** in Einstellungen → Bitvavo-API eintragen (Secret wird verschlüsselt
   gespeichert).
2. **Historie importieren** über die Trades-Ansicht (Button "Transaktionen importieren") oder
   CSV hochladen. Für vollständige FIFO-Basis kann die "Komplette Historie"-Option genutzt
   werden.
3. **Portfolio prüfen** (Kryptowährungen, Bestände, unrealisierter Gewinn/Verlust).
4. **Zeitplan anlegen** (Wizard: Was kaufen? → Wann kaufen? → Sicherheitsregeln).
5. Optional: Handelsregeln, E-Mail-Benachrichtigung, Verkaufsempfehlungen nutzen.

---

## Bildschirm-Beispiele (Portfolio)

Die Bilder im Ordner `screen/` zeigen die wichtigsten Bildschirme der App.
Alle Screenshots wurden mit **Playwright** direkt aus dem laufenden App generiert
(Simulationsmodus, keine echten API-Keys oder persönlichen Daten).

### Screenshots

| Bild | Inhalt |
|------|--------|
| [`screen/01_login.png`](screen/01_login.png) | Anmeldung |
| [`screen/02_dashboard.png`](screen/02_dashboard.png) | Dashboard (Gesamtwert, Chart, Guthaben, Kryptowährungen) |
| [`screen/03_positions.png`](screen/03_positions.png) | Meine Kryptowährungen (Positionen) |
| [`screen/04_wizard_anlegen.png`](screen/04_wizard_anlegen.png) | Zeitplan anlegen (Wizard) |
| [`screen/05_wizard_bestätigung.png`](screen/05_wizard_bestätigung.png) | Zeitplan gespeichert (Vorschau) |
| [`screen/06_trades.png`](screen/06_trades.png) | Trades |
| [`screen/07_portfolio.png`](screen/07_portfolio.png) | Portfolio & Steuern |
| [`screen/08_einstellungen.png`](screen/08_einstellungen.png) | Einstellungen (API-Key, Berechtigungen) |
| [`screen/09_system.png`](screen/09_system.png) | System |
| [`screen/10_chart_toggle.png`](screen/10_chart_toggle.png) | Chart-Ansicht (BTC-EUR / Portfolio-Wert) |

### Hinweis

Die Screenshots enthalten keine echten Daten, API-Keys oder Secrets. Für das Portfolio
muss man die Bilder mit eigenen Daten aufnehmen (z. B. Screenshots aus dem eigenen Browser).
Siehe `screen/SCREENSHOTS.md` für Details zum Umgang mit echten Daten.

---

## Konfiguration

| Variable           | Standard                    | Beschreibung                                                                  |
|--------------------|-----------------------------|-------------------------------------------------------------------------------|
| `MASTER_PASSWORD`  | (Platzhalter)              | Login-Passwort — selbst setzen                                                |
| `FLASK_SECRET_KEY` | (Platzhalter)              | Session-Schlüssel — selbst setzen                                             |
| `ENCRYPTION_KEY`   | –                           | 64 Hex-Zeichen (Fernet) für die Secret-Verschlüsselung                       |
| `SIMULATION_MODE`  | `false`                     | `true` = keine echten Orders, nur Simulation                                 |
| `FLASK_DEBUG`      | `0`                         | `1` = Debugmodus (nie im Container!)                                         |
| `DB_PATH`          | `data/bitmaster.db`         | Pfad zur SQLite-Datenbank                                                    |
| `LOG_FILE`         | `data/bitmaster.log`        | Pfad zur Logdatei                                                            |
| `TZ`               | (Containerzeit)             | Zeitzone für den Scheduler                                                   |

---

## Sicherheit

- Das Bitvavo-API-**Secret** wird mit `ENCRYPTION_KEY` (Fernet) **verschlüsselt** in der
  SQLite-Datenbank gespeichert; vorhandene Klartexte werden beim Start automatisch migriert.
- Im UI werden Key und Secret nur **maskiert** angezeigt.
- "Angemeldet bleiben" speichert das Master-Passwort clientseitig verschlüsselt im Browser
  (AES-GCM, abgeleitet aus einem serverseitigen Pepper + Zufalls-Salt). Nur auf privaten Geräten
  verwenden. Erfordert HTTPS oder `localhost` (sonst nur die Session).
- `.env` ist ausgeschlossen — nie committen.

---

## Rechtlicher Hinweis

Das Steuer-Dashboard ist eine **Hilfe zur Selbstorganisation** (FIFO, Haltefrist),
keine Steuerberatung. Für eine verbindliche Aussage einen Steuerberater konsultieren.

---

## Troubleshooting

- Fehler 309 ("signature invalid"): API-Secret fehlt oder falsch — neuen Key + Secret neu
  eintragen.
- Import wirkt fehlerhaft: System-Seite → letzte Log-Zeilen prüfen; oft fehlt das Secret
  oder die API ist temporär limitiert (429, später erneut).
- Scheduler kauft zur falschen Zeit: `TZ=Europe/Berlin` prüfen, Container/Prozess neu starten.
- Indikatoren zeigen "–": kein EUR-Markt für den Coin, keine Internetverbindung oder
  Bitvavo-API nicht erreichbar.

---

## Projektstruktur

```text
bitvavo-autobot/
├── app.py                 # Hauptprogramm
├── requirements.txt       # Python-Abhängigkeiten
├── Dockerfile             # Docker-Image definiert
├── docker-compose.yml     # Docker-Konfiguration
├── .env.example           # Konfigurationsvorlage (kein echtes .env!)
├── .gitignore            # Ausschluss von sensiblen/lokalen Dateien
├── verifizieren.sh       # Bash-Prüfskript
├── LEITFADEN.md           # Bedienungsanleitung
├── KITAP_NOTES.md         # Statusnotizen für Kitap-Upload (optional)
├── templates/             # Web-Oberfläche (Jinja2 + Bootstrap)
├── static/               # CSS/JS-Bestandteile
├── screen/               # (optional) Screenshots für Portfolio
└── data/                 # (wird ignoriert) Datenbank + Logs — nicht im Kitap!
```

---

**Wichtiger Hinweis:**

- Das **echte .env** mit Passwörtern/API-Keys/ENCRYPTION_KEY darf **nicht** im Kitap-Archiv
  landen. Es ist über `.gitignore` ausgeschlossen. Für die Weitergabe nutzt man nur
  `.env.example` und lässt den Nutzer ein eigenes `.env` erstellen.
- Der Ordner `data/` (Datenbank, Logs) wird ignoriert und gehört **nicht** ins Archiv.

---

**Hinweis zur Verwendung als Portfolio:** Das Projekt ist so aufgebaut, dass man es auf einer
eigenen Webseite einfach vorstellen kann — mit Screenshots, einer kurzen Beschreibung und der
Installationsanleitung. Es enthält keine echten Schlüssel oder Secrets und lässt sich leicht
anpassen oder erweitern.
