# Kitap-Upload (Bearbeitung)

## Was in das Archiv gehört

- `app.py` – Hauptprogramm
- `requirements.txt` – Abhängigkeiten (Python-Pakete)
- `Dockerfile` + `docker-compose.yml` – Containerisierung
- `templates/` – Web-Oberfläche (Bootstrap)
- `static/` – CSS/JS
- `.gitignore` – Ausschluss von .env/data/*
- `verifizieren.sh` – Prüfskript
- `.env.example` – Konfigurationsvorlage (keine echten Werte!)
- `screen/` – (optional) Screenshots, sofern Kitap sie verlangt; sonst weglassen oder als Ressource anbieten

## Was NICHT im Archiv landen darf

- `.env` mit echten Werten (MASTER_PASSWORD, API-Key, ENCRYPTION_KEY, …)
- `data/bitmaster.db` (Datenbank mit persönlichen Keys/Trades)
- `data/bitmaster.log`, `server.log` (Protokolle)
- `.DS_Store`, `__pycache__`, `.venv/` (System-/IDE-Dateien)

## Hinweis zum Passwort

Das Log-in-Passwort wird **nicht fest im Code** festgelegt. Es wird über die Umgebungsvariable
`MASTER_PASSWORD` (oder `.env`) konfiguriert.

Standardwert in der Vorlage: **bitmaster** – Beispielhaft, aber unbedingt im echten Einsatz
ändern.

Im Kitap-Upload werden nur die Dateien ausgeliefert, damit ein anderer Nutzer die App
selbst starten und einrichten kann (ohne Zugriff auf Ihre persönlichen Daten).

## Erster Schritt für die Nutzer

1. `.env.example` nach `.env` kopieren
2. Werte anpassen: `MASTER_PASSWORD`, `FLASK_SECRET_KEY`, `ENCRYPTION_KEY` (GitHub:
   `openssl rand -hex 32`)
3. Docker: `docker compose up -d --build` (Port 8050) oder lokal: `python3 -m venv .venv &&
   .venv/bin/pip install -r requirements.txt && python3 app.py`
4. Im Browser: http://localhost:8050 bzw. http://localhost:5000

## Für Portfolio-Seite (eigenen Webauftritt)

- Screenshots in `screen/` ablegen (01 bis 10 wie README_screenshots.md)
- README.md (untere Version) dient als Projektbeschreibung
- Keine API-Keys in README, screenshots oder code snippets

## Version

- In `app.py`: `APP_VERSION = "1.7.0"` (Selbstcheck + Watchdog, Namens-Fallback XRP→Ripple)
- SDK: `python-bitvavo-api` (offiziell, Bitvavo)
- Python 3.9+ (lokal) oder Python 3.11-slim (Docker)
