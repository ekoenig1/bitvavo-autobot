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
  sowie Gesundheit (`/health`, Logs, Prozess, Port).
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
| `screen/01_login.png` | Login |
| `screen/02_dashboard.png` | Dashboard (Gesamtwert, Chart, Guthaben, Kryptowährungen) |
| `screen/03_portfolio.png` | Portfolio & Steuern |
| `screen/04_schedule.png` | Zeitpläne (DCA) |
| `screen/05_simulation.png` | Simulation |
| `screen/06_settings.png` | Einstellungen |
| `screen/07_chart.png` | Chart-Ansicht |

### Hinweis

Die Screenshots wurden in einer authentifizierten **Playwright-Session** mit
`SIMULATION_MODE=true` aufgenommen und enthalten **keine echten API-Keys, Secrets
oder persönlichen Daten**.

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

## Haftungsausschluss und Einschränkungen

Dieses Projekt dient ausschließlich **Demonstrations- und Lernzwecken**. Es handelt sich nicht um eine produktionsreife Handelssoftware und nicht um eine Steuerberatung.

- **Keine Steuerberatung**: Das enthaltene Steuer-Dashboard (FIFO-Berechnung, Haltefrist, 600-€-Freigrenze) ist eine unverbindliche Hilfestellung zur Selbstorganisation und ersetzt keine individuelle steuerliche Beratung durch einen zugelassenen Steuerberater oder Wirtschaftsprüfer.
- **Keine Anlageberatung**: Die berechneten Indikatoren, Signale und Verkaufsempfehlungen basieren auf historischen Daten und Heuristiken. Sie begründen keine Kauf- oder Verkaufsempfehlung im rechtlichen Sinne.
- **Verwendung auf eigene Gefahr**: Der Einsatz mit echten API-Keys und Echtgeld-Orders erfolgt ausschließlich auf eigenes Risiko. Der Autor übernimmt keine Haftung für finanzielle Verluste, Fehlkonfigurationen, Datenverlust oder Schäden jeglicher Art, die aus der Nutzung dieser Software entstehen können.

---

## Sicherheitswarnung — nur für lokale Testumgebungen

Diese Software wurde für den **ausschließlichen Betrieb in lokalen, vertrauenswürdigen Testumgebungen** konzipiert. Sie ist **nicht für den produktiven Einsatz auf öffentlich erreichbaren Servern** geeignet und durchläuft derzeit keine formale Sicherheitsprüfung nach industriellen Standards.

- **Kein produktiver Einsatz**: Betreiben Sie diese Anwendung **nicht** in Netzsegmenten, die aus dem Internet erreichbar sind, und exponieren Sie sie **nicht** ohne weitere Absicherung über Firewalls, Reverse-Proxies oder VPNs.
- **Fehlende Sicherheitsprüfung**: Es wurden bislang keine Penetrationstests, Code-Audits oder formalen Sicherheitszertifizierungen durchgeführt. Schwachstellen sind nicht ausgeschlossen.
- **Erforderliche Maßnahmen vor jedem Einsatz**: Vor einer Nutzung außerhalb einer isolierten lokalen Testmaschine sind mindestens folgende Schritte durchzuführen:
  - Formale Sicherheitsprüfung des gesamten Codes, insbesondere der Authentifizierung, Session-Verwaltung, Eingabevalidierung und Kryptografie
  - Härtung der Flask-Konfiguration (HTTPS, HSTS, sichere Cookies, CORS-Einschränkungen)
  - Absicherung des Datenbankzugriffs und der API-Keys (HSM, Secret-Manager, keine Klartextspeicherung)
  - Regelmäßige Abhängigkeitsprüfungen (`pip-audit`, `safety`) und Updates
  - Intrusion-Detection, Logging-Monitoring und Incident-Response-Prozess
- **Sensible Daten**: `.env`, API-Keys, Secrets und die SQLite-Datenbank enthalten hochsensible Informationen. Sie dürfen **niemals** in Versionsverwaltungen, öffentlichen Repositories oder unsicheren Speichern abgelegt werden.

Durch die Nutzung bestätigen Sie, dass Sie die Risiken verstanden haben und die Software nur in der vorgesehenen isolierten Testumgebung einsetzen.

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
├── app.py                 # Flask-App
├── requirements.txt       # Python-Abhängigkeiten
├── Dockerfile             # Container-Image
├── docker-compose.yml     # Compose-Definition
├── .env.example           # Konfigurationsvorlage (kein echtes .env!)
├── .gitignore            # Ausschluss von sensiblen/lokalen Dateien
├── templates/             # Web-Oberfläche (Jinja2 + Bootstrap)
├── static/               # CSS/JS-Bestandteile
├── screen/               # README-Screenshots
└── data/                 # Datenbank + Logs — wird ignoriert, nicht im Git
```

---

**Wichtiger Hinweis:**

- Das **echte .env** mit Passwörtern/API-Keys/ENCRYPTION_KEY darf **nicht** in Versionsverwaltungen
  landen. Es ist über `.gitignore` ausgeschlossen. Für die Weitergabe nutzt man nur
  `.env.example` und lässt den Nutzer ein eigenes `.env` erstellen.
- Der Ordner `data/` (Datenbank, Logs) wird ignoriert und gehört **nicht** ins Repository.
