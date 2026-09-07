# Bitvavo-Autobot – Bedienungsanleitung (Leitfaden)

## 1. Was ist Bitvavo-Autobot?

Ein kleiner Python-basierter Dienst mit Bootstrap-Oberfläche, der beim Umgang mit
der Börse **Bitvavo** hilft:

- Zeitgesteuerte Käufe (z. B. wöchentlich zum festen Uhrzeitpunkt)
- Trade-Historie importieren (automatisch per API, optional CSV)
- Portfolio & Steueroptimierung (FIFO, 1-Jahres-Regel, Verkaufsempfehlungen)
- Technische Indikatoren (SMA9, SMA50, RSI14, ATH in 90 Tagen)
- System-Status und Healthcheck

## 2. Schnellstart (Lokales Setup)

### Voraussetzungen

- Python 3.9 oder neuer
- Optional: Docker (empfohlen)

### Installation(en)

#### A) Docker (einfachste Variante)

```bash
cd bitvavo-autobot
cp .env.example .env
# In .env: MASTER_PASSWORD, FLASK_SECRET_KEY, ENCRYPTION_KEY bearbeiten
docker compose up -d --build
```

Öffnen: http://localhost:8050

#### B) Lokal mit Python

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python3 app.py
```

Öffnen: http://localhost:5000

### ersten Login

Passwort: Wert aus `MASTER_PASSWORD` (in `.env`).
Standard-Beispielwert ist `bitmaster` – **bitte im eigenen Einsatz ändern**.

## 3. Erster Aufbau (Eigenen Betrieb erstellen)

1. **API-Key + Secret** in Einstellungen → Bitvavo-API eintragen (Secret wird verschlüsselt gespeichert)
2. **Historie importieren** (Trades-Ansicht): Button „Transaktionen importieren” – oder CSV hochladen
3. **Portfolio prüfen** – Kryptowährungen, Bestände, unrealisierter Gewinn/Verlust
4. **Zeitplan anlegen** – Wochentag + Uhrzeit + Positionen (bis zu 5 Coins, EUR-Betrag)
5. Optional: Handelsregeln, E-Mail-Benachrichtigung

## 4. Wichtige Funktionen im Überblick

### Zeitpläne (DCA)

- Wochentag + Uhrzeit
- Pro Plan mehrere Coins (Betrag in EUR)
- Regeln: Max-Preis, ATH-Abstand, SMA9/50, RSI-Band, max. Gesamtinvest
- „Vorschau”-Button zeigt die nächsten 3 geplanten Käufe simuliert

### Trades & Import

- Button „Transaktionen importieren”: holt alle EUR-Transaktionen (Käufe/Verkäufe, Transfers, Gebühren)
- Option „Komplette Historie”: alle Tage seit Kontoerstellung (rückwärts, etwas länger)
- Transfers-Import (Ein-/Auszahlungen): Einzahlungen → neue Lots zum Tageskurs (FIFO-Steuerbasis!), Auszahlungen → nur Bestandsminderung

### Portfolio & Steuern (FIFO)

- Für jeden Coin: zugeordnete Lots (FIFO)
- Steuerfrei: Haltezeit > 1 Jahr
- Steuerpflichtig: Haltezeit ≤ 1 Jahr
- Angaben: unrealisierter Gewinn/Verlust, steuerfreier/geprüfter Gewinn, 600-€-Freigrenze
- Warnung: bald steuerfrei (Haltefrist endet in X Tagen)

### Verkaufsempfehlungen

- Steuerfreie Coins im Plus
- Kurs nahe ATH → „jetzt steuerfrei verkaufen”
- Rebalancing-Warnung (> 40 % eines Coins am Gesamtportfolio)
- Gewinnziel: 25 % verkaufen, wenn +50 % Gewinn und steuerfrei
- Bald steuerfrei / steuerpflichtiger Verlustwarnungen

### Bestätigter Verkauf (echt)

- Pro Empfehlung: Prozent-Eingabe → echte Market-Order
- Only mit API-Key, der Handelsberechtigung hat (bei Read-only: Warnung statt Buttons)

## 5. Sicherheit

- API-Secret verschlüsselt (Fernet) in der SQLite-Datenbank gespeichert
- Bei lokaler Nutzung: Browser-Speicher vs. Session-Cookie beachten
- .env nie commiten
- Passwort im Browser verschlüsselt speichern („Angemeldet bleiben”) – nur auf privaten Geräten

## 6. Screen-Flows (für Screenshots / Portfolio)

- Login → Dashboard → Zeitplan anlegen (Wizard) → Trades importieren → Portfolio → Einstellungen → System
- Empfehlungen: Portfolio-Seite zeigt Warnungen/Badges
- System: Version, SDK, Zeitzone, DB-Pfad, API-Status

### Screenshots

Die Screenshots liegen im Ordner `screen/` und sind in der README verknüpft:

- [`screen/01_login.png`](screen/01_login.png) – Anmeldung
- [`screen/02_dashboard.png`](screen/02_dashboard.png) – Dashboard (Gesamtwert, Chart, Guthaben, Kryptowährungen)
- [`screen/03_positions.png`](screen/03_positions.png) – Meine Kryptowährungen (Positionen)
- [`screen/04_wizard_anlegen.png`](screen/04_wizard_anlegen.png) – Zeitplan anlegen (Wizard)
- [`screen/05_wizard_bestätigung.png`](screen/05_wizard_bestätigung.png) – Zeitplan gespeichert (Vorschau)
- [`screen/06_trades.png`](screen/06_trades.png) – Trades
- [`screen/07_portfolio.png`](screen/07_portfolio.png) – Portfolio & Steuern
- [`screen/08_einstellungen.png`](screen/08_einstellungen.png) – Einstellungen (API-Key, Berechtigungen)
- [`screen/09_system.png`](screen/09_system.png) – System
- [`screen/10_chart_toggle.png`](screen/10_chart_toggle.png) – Chart-Ansicht (BTC-EUR / Portfolio-Wert)

### Hinweis

Die Screenshots enthalten keine echten Daten, API-Keys oder Secrets. Für das Portfolio
muss man die Bilder mit eigenen Daten aufnehmen. Siehe `screen/README_screenshots.md`.

## 7. Troubleshooting

- Fehler 309 („signature invalid”): API-Secret fehlt oder falsch → neuen Key + Secret erneut eintragen
- Import fehlerhaft: Log-Ansicht (System → letzte Log-Zeilen) prüfen
- Scheduler falsch: Zeitzone (Environment/TZ) prüfen, ggf. Neustart
- Indikatoren „–”: kein EUR-Markt, keine Verbindung zur API

## 8. Hinweis (Steuer)

Das Steuer-Dashboard ist eine **Hilfe zur Selbstorganisation** (FIFO, Haltefrist), keine
Steuerberatung. Für eine verbindliche Auslegung einen Steuerberater konsultieren.
