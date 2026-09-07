#!/usr/bin/env bash
#
# verifizieren.sh – Bitvavo-Autobot Installation prüfen
#
# Zeigt auf einen Blick: App-Version, SDK-Version, Zeitzone, DB-/Log-Pfad,
# Trades/Zeitpläne (via /health) und prüft die lokalen Dateien.
#
# Nutzung:
#   ./verifizieren.sh             # prüfen (Warnungen brechen nicht ab)
#   ./verifizieren.sh --strict    # Exit-Code 1, sobald etwas fehlt
#
# Konfigurierbar über Umgebungsvariablen:
#   BASE_URL=http://127.0.0.1:5000   # laufende Instanz (lokal oder Docker)
#
set -u

BASE_URL="${BASE_URL:-http://127.0.0.1:5000}"
STRICT=0
if [ "${1:-}" = "--strict" ]; then
  STRICT=1
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FAILED=0
WARNED=0

fail()   { FAILED=1; echo "  [FEHLER]   $1"; }
warn()   { WARNED=1; echo "  [WARNUNG] $1"; }
ok()     { echo "  [OK] $1"; }

echo "================================================================"
echo " Bitvavo-Autobot – Verifizierung"
echo " Ziel: $BASE_URL"
echo "================================================================"

# ---------- 1) HTTP-Erreichbarkeit ----------
echo
echo ">> 1) Erreichbarkeit der App"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "$BASE_URL/login" 2>/dev/null || echo "000")
if [ "$HTTP_CODE" = "200" ]; then
  ok "App antwortet (HTTP $HTTP_CODE auf /login)"
else
  fail "App nicht erreichbar (HTTP $HTTP_CODE auf /login) – läuft der Server? BASE_URL prüfen!"
fi

# ---------- 2) Health-Endpoint ----------
echo
echo ">> 2) Health-Endpoint (/health)"
TMP_HEALTH="$(mktemp)"
if curl -fsS --max-time 10 "$BASE_URL/health" -o "$TMP_HEALTH" 2>/dev/null; then
  if command -v python3 >/dev/null 2>&1; then
    PY_OUT=$(python3 - "$TMP_HEALTH" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    try:
        h = json.load(fh)
    except Exception as e:
        print(f"  [FEHLER] /health nicht als JSON lesbar: {e}")
        sys.exit(1)
def j(key):
    return h.get(key, "?")
print(f"  [OK] App: {j('app')} {j('version')}  |  SDK: python-bitvavo-api {j('sdk')}")
print(f"  [OK] Zeitzone: {j('timezone')}  (Serverzeit {j('server_time')})")
print(f"  [OK] DB-Pfad: {j('db_path')}  (existiert: {j('db_exists')})")
print(f"  [OK] Log-Pfad: {j('log_path')}  (existiert: {j('log_exists')})")
mode = "Simulation" if h.get("simulation_mode") else "Echtbetrieb"
enc = "aktiv" if h.get("encryption_enabled") else "INAKTIV (Secrets im Klartext!)"
print(f"  [OK] Modus: {mode}  |  Secret-Verschlüsselung: {enc}")
counts = h.get("counts") or {}
print(f"  [OK] Trades in DB: {counts.get('trades', '?')}  |  Zeitpläne: {counts.get('schedules', '?')}")
if h.get("status") != "ok":
    print("  [FEHLER] status != ok")
    sys.exit(1)
PY
)
    PY_RC=$?
    if [ "$PY_RC" -eq 0 ]; then
      echo "$PY_OUT"
    else
      fail "/health liefert ungültigen Status (s. o.)"
      echo "$PY_OUT" | sed 's/^/  /'
    fi
  else
    ok "python3 fehlt auf dem Host – /health-Rohdaten:"
    head -c 900 "$TMP_HEALTH"
    echo
  fi
else
  fail "/health nicht erreichbar unter $BASE_URL/health"
fi
rm -f "$TMP_HEALTH"

# ---------- 3) Lokale DB-/Log-Dateien ----------
echo
echo ">> 3) Lokale Dateien (Skript-Ordner: $SCRIPT_DIR)"
if [ -f "$SCRIPT_DIR/data/bitmaster.db" ]; then
  ok "Datenbank vorhanden: data/bitmaster.db"
else
  warn "data/bitmaster.db nicht gefunden – DB-Pfad über DB_PATH/Volume prüfen"
fi
if [ -f "$SCRIPT_DIR/data/bitmaster.log" ]; then
  ok "Log vorhanden: data/bitmaster.log"
else
  warn "data/bitmaster.log nicht gefunden – Log-Pfad über LOG_FILE prüfen"
fi

# ---------- 4) Lokale Abhängigkeiten (nur lokaler Betrieb) ----------
echo
echo ">> 4) Lokale Python-Umgebung"
if [ -d "$SCRIPT_DIR/.venv" ]; then
  if [ -x "$SCRIPT_DIR/.venv/bin/python" ]; then
    VENV_VER=$("$SCRIPT_DIR/.venv/bin/python" -c 'import importlib.metadata as m; print(m.version("python-bitvavo-api"))' 2>/dev/null)
    if [ -n "$VENV_VER" ]; then
      ok ".venv vorhanden, python-bitvavo-api $VENV_VER"
    else
      warn ".venv vorhanden, aber python-bitvavo-api nicht installiert – 'pip install -r requirements.txt' ausführen"
    fi
  else
    warn ".venv unvollständig"
  fi
else
  warn "Kein .venv (normal im Docker-Betrieb). Lokal: 'python3 -m venv .venv && .venv/bin/pip install -r requirements.txt'"
fi

# ---------- Zusammenfassung ----------
echo
echo "================================================================"
if [ "$FAILED" = "1" ]; then
  echo " ERGEBNIS: FEHLER gefunden"
  echo "================================================================"
  exit 1
elif [ "$WARNED" = "1" ]; then
  echo " ERGEBNIS: OK mit Warnungen"
  if [ "$STRICT" = "1" ]; then
    echo " (--strict aktiv -> Exit-Code 1)"
    echo "================================================================"
    exit 1
  fi
  echo "================================================================"
  exit 0
else
  echo " ERGEBNIS: Alles in Ordnung ✓"
  echo "================================================================"
  exit 0
fi
