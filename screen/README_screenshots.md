# Screenshots für Bitvavo-Autobot

Dieser Ordner enthält die Screenshots, die das Programm als Portfolio darstellen sollen.
Jeder Screenshot hat einen kurzen Dateinamen (z. B. `01_login.png`) und wird im README
Referenziert. Hinweise zur Aufnahme:

- Zeige **deine eigenen Daten** (nicht das Screenshot-Beispiel unten).
- Keine API-Keys, Secrets oder persönlichen Kontobalancen im Klartext.
- Kapture die komplette Seite (Browser-Elemente wie Menüleiste, Karten, Tabellen).
- Dateigröße: PNG oder JPEG, sinnvoll komprimiert (~200–800 KB).
- Mindestbreite: rund 1200 px (damit auf Webseite gut lesbar).

## Geplant / empfohlen

| Bild | Inhalt | Hinweis |
|------|--------|---------|
| `01_login.png` | Login-Seite (Anmeldung – Bitvavo-Autobot) | Demo des gesicherten Zugangs |
| `02_dashboard.png` | Dashboard mit Gesamtwert, Chart (Portfolio-Wert), Guthaben, Positionsliste | Herz der App |
| `03_positions.png` | Positionsübersicht mit Kryptowährung, Preis, 24h, unrealisierter GuV, Guthaben | Detail anschaulich |
| `04_wizard_anlegen.png` | Wizard „Neuen Zeitplan anlegen“ (Schritt 1: Positionen, Dropdown mit allen Coins) | Benutzerfreundlichkeit |
| `05_wizard_bestätigung.png` | Wizard-Zusammenfassung + Vorschau (nächste 3 Käufe simuliert) | Vorhersage/Planung |
| `06_trades.png` | Trades-Ansicht mit Liste (relevant: Trades, Transfers) | Historie |
| `07_portfolio.png`  | Portfolio-Dashboard (FIFO, steuerfrei/pflichtig, Verkaufsempfehlungen) | Steueroptimierung |
| `08_einstellungen.png` | Einstellungen: API-Key, Secret verschlüsselt, Berechtigungen | Sicherheit |
| `09_system.png`     | System-Seite (Version, SDK, Zeitzone, DB-Pfad, API-Status) | Metadaten/Health |
| `10_chart_toggle.png` | Chart-Ansicht mit Umschalter BTC-EUR ↔ Portfolio-Wert | Charting-Funktion |

## Hinweis zu Anfangsdaten

Die Demo-Datenbank enthält **294 echte Trades** aus dem eigenen Bitvavo-Konto (importiert,
dedupliziert). Für das Portfolio brauchen Sie nur:

- Echten Bitvavo-API-Key (Read-only genügt für Portfolio/Demo)
- API-Secret (wird in der Datenbank verschlüsselt)

Das Passwort für den Login wird über `MASTER_PASSWORD` (in `.env`) festgelegt – beim Kitap-Archiv
nicht im Klartext weitergeben, nur die Vorlage `.env.example` enthalten.

## Hinweis zum Stapel

Wenn Sie viele Screenshots aufnehmen, sortieren Sie sie:

- `screen/` (app-Angabe)
- Bildnamen alphabetisch nach Relevanz (01, 02 …)
- Keine sensiblen Informationen im Bild (Maskierung oder Ausschnitt prüfen)

## Rechtliches

Screenshots dürfen keine API-Informationen (Schlüssel, Secrets) zeigen. Auch
Preise/Bestände sind dann okay, wenn sie **Ihre eigenen** Öffentlich-Einfacher-Daten sind
und Sie damit einverstanden sind. Es empfiehlt sich, im README darauf zu verweisen:
„Screenshots zeigen eine Beispielinstanz."
