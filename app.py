import os
import io
import csv
import time
import datetime
import threading
import schedule
import sqlite3
import logging
import smtplib
import base64
import hashlib

from datetime import timedelta
from flask import (
    Flask, request, render_template, redirect,
    url_for, flash, session, Response, jsonify
)

# Version der App (wird auf der System-Seite und in /health angezeigt)
APP_VERSION = "1.7.0"
START_TIME = time.time()
# Ergebnisse des Startup-Selbstchecks (für /health und System-Seite)
STARTUP_ERRORS = []
SCHEDULER_THREAD = None

# Version der offiziellen Bitvavo-SDK (python-bitvavo-api)
try:
    from importlib.metadata import version as _pkg_version
    SDK_VERSION = _pkg_version("python-bitvavo-api")
except Exception:
    SDK_VERSION = "unbekannt"

# Schwellen für die Verkaufsempfehlungen auf dem Portfolio-Dashboard
REBALANCE_SHARE = 0.40      # Warnung, wenn ein Coin > 40 % des Portfolios ausmacht
ATH_NEAR_PCT = -5.0         # "nahe am ATH" = innerhalb von 5 % unter dem 90-Tage-Hoch
MIN_REC_VALUE_EUR = 10.0    # erst ab diesem Wert erscheinen Verkaufsempfehlungen
from python_bitvavo_api.bitvavo import Bitvavo
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Verschlüsselung des API-Secrets (Fernet aus der cryptography-Bibliothek)
try:
    from cryptography.fernet import Fernet
    HAVE_CRYPTO = True
except ImportError:
    Fernet = None
    HAVE_CRYPTO = False

########################################
# 0) .env automatisch laden (falls vorhanden)
#    Echte Umgebungsvariablen (Docker, export) haben Vorrang.
########################################
def _load_dotenv(path=".env"):
    if not os.path.exists(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                # split only on first "=" to support "a=b=c"
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if len(val) >= 2 and val[0] == val[-1] and val[0] in ('"', "'"):
                    val = val[1:-1]
                if key and key not in os.environ:
                    os.environ[key] = val
    except OSError:
        pass


_load_dotenv()

########################################
# 0b) Pfade & Konfiguration (vor dem Logging!)
#     -> Im Docker-Container über Umgebungsvariablen gesetzt
########################################
DB_NAME = os.environ.get("DB_PATH") or "data/bitmaster.db"
LOG_FILE = os.environ.get("LOG_FILE") or "data/bitmaster.log"

# Verzeichnisse automatisch anlegen (wichtig im Docker-Container)
os.makedirs(os.path.dirname(os.path.abspath(DB_NAME)) or ".", exist_ok=True)
os.makedirs(os.path.dirname(os.path.abspath(LOG_FILE)) or ".", exist_ok=True)

########################################
# 1) Logging konfigurieren
#    -> Logging in eine Datei und in die Konsole.
########################################
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler(LOG_FILE),        # Log in Datei
        logging.StreamHandler()               # Zusätzlich in Konsole
    ]
)

########################################
# 2) Flask-App
########################################
app = Flask(__name__)

# SECRET_KEY und MASTER_PASSWORD über ENV-Variablen einstellbar
# (leere Env-Werte fallen auf die Defaults zurück)
app.secret_key = os.environ.get("FLASK_SECRET_KEY") or "SUPER_GEHEIM_FUER_SESSION"
MASTER_PASSWORD = os.environ.get("MASTER_PASSWORD") or "bitmaster"

# "Angemeldet bleiben": Session-Cookie hält 30 Tage (statt bis Browser-Schließen)
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=30)

# Pepper für die clientseitige Verschlüsselung des gespeicherten Passworts
# (AES-GCM-Schlüssel wird im Browser aus diesem Wert + Zufalls-Salt abgeleitet)
LOGIN_PEPPER = hashlib.sha256(
    (app.secret_key + ":remember-login").encode("utf-8")
).hexdigest()

# SIMULATION_MODE kann z.B. mit export SIMULATION_MODE=true aktiviert werden
SIMULATION_MODE = os.environ.get("SIMULATION_MODE", "false").lower() in ["true", "1", "yes"]

# Debug-Modus über Umgebungsvariable steuerbar (im Docker standardmäßig AUS)
DEBUG_MODE = os.environ.get("FLASK_DEBUG", "0").lower() in ["1", "true", "yes"]

# ENCRYPTION_KEY: 64 Hex-Zeichen (32 Bytes) für die Fernet-Verschlüsselung
# des Bitvavo-API-Secrets. Ohne diesen Schlüssel bleibt das Secret im Klartext.
ENCRYPTION_KEY = None
if not HAVE_CRYPTO:
    print("WARNUNG: 'cryptography' nicht installiert – Secret-Verschlüsselung deaktiviert.")
elif os.environ.get("ENCRYPTION_KEY"):
    try:
        _key_bytes = bytes.fromhex(os.environ["ENCRYPTION_KEY"].strip())
        if len(_key_bytes) != 32:
            raise ValueError("ENCRYPTION_KEY muss aus 64 Hex-Zeichen (32 Bytes) bestehen")
        ENCRYPTION_KEY = base64.urlsafe_b64encode(_key_bytes)
    except Exception as e:
        print(f"WARNUNG: Ungültiger ENCRYPTION_KEY – Secrets bleiben im Klartext: {e}")


def encrypt_secret(secret):
    """Verschlüsselt ein Secret mit Fernet; Ergebnis erhält 'enc:'-Präfix."""
    if not ENCRYPTION_KEY:
        return secret
    if not HAVE_CRYPTO:
        raise Exception("cryptography-Bibliothek fehlt")
    if not secret:
        return secret
    return "enc:" + Fernet(ENCRYPTION_KEY).encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(stored):
    """Entschlüsselt ein mit encrypt_secret gespeichertes Secret."""
    if not stored or not stored.startswith("enc:"):
        return stored
    if not ENCRYPTION_KEY or not HAVE_CRYPTO:
        raise Exception("ENCRYPTION_KEY fehlt oder cryptography nicht installiert")
    return Fernet(ENCRYPTION_KEY).decrypt(stored[4:].encode("ascii")).decode("utf-8")


def migrate_credentials_encryption():
    """
    Verschlüsselt vorhandene Klartext-Secrets beim Start in-place.
    Achtung: Vor dem ersten Setzen von ENCRYPTION_KEY ein DB-Backup anlegen.
    """
    if not ENCRYPTION_KEY:
        logging.info("ENCRYPTION_KEY nicht gesetzt – Secrets bleiben unverschlüsselt gespeichert.")
        return
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, api_secret FROM credentials")
        rows = c.fetchall()
        migrated = 0
        for (rid, secret) in rows:
            if secret and not secret.startswith("enc:"):
                try:
                    c.execute("UPDATE credentials SET api_secret=? WHERE id=?",
                              (encrypt_secret(secret), rid))
                    migrated += 1
                except Exception as e:
                    logging.error(f"Migration Secret (id={rid}) fehlgeschlagen: {e}")
        conn.commit()
    if migrated:
        logging.info(f"{migrated} Klartext-Secret(s) beim Start verschlüsselt.")

print("DB-Pfad:", os.path.abspath(DB_NAME))
print("Log-Pfad:", os.path.abspath(LOG_FILE))


def current_tz_name():
    """Zeitzonen-Anzeige: bevorzugt TZ-Env (Docker), sonst lokale Zeitzone."""
    env_tz = os.environ.get("TZ")
    if env_tz:
        return env_tz
    try:
        return datetime.datetime.now().astimezone().tzname() or "UTC"
    except Exception:
        return "lokal"


def file_size_or_none(path):
    try:
        return os.path.getsize(path)
    except OSError:
        return None


# Beispielhafte Liste an Assets, die man im Dropdown anbieten kann
ALLOWED_ASSETS = ["BTC", "ETH", "ADA", "XRP", "DOT", "SOL"]

# Bekannte Coin-Namen als Fallback, falls der Bitvavo-/assets-Endpoint keinen
# Namen liefert (sonst würde z. B. XRP statt "Ripple" nur das Kürzel zeigen).
COIN_NAMES_FALLBACK = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "XRP": "Ripple", "SOL": "Solana",
    "ADA": "Cardano", "DOGE": "Dogecoin", "LINK": "Chainlink", "LTC": "Litecoin",
    "DOT": "Polkadot", "AVAX": "Avalanche", "TRX": "Tron", "XLM": "Stellar",
    "BCH": "Bitcoin Cash", "SHIB": "Shiba Inu", "UNI": "Uniswap", "ATOM": "Cosmos",
    "ALGO": "Algorand", "VET": "VeChain Thor", "ICP": "Internet Computer",
    "FIL": "Filecoin", "ETC": "Ethereum Classic", "XDG": "Dogecoin",
    "AAVE": "Aave", "GRT": "The Graph", "SAND": "The Sandbox",
    "MANA": "Decentraland", "CRV": "Curve DAO Token", "COMP": "Compound",
    "SNX": "Synthetix", "MKR": "Maker", "YFI": "Yearn Finance",
    "1INCH": "1inch", "ENJ": "Enjin", "ZRX": "0x", "BAT": "Basic Attention Token",
    "NEO": "NEO", "EOS": "EOS", "IOTA": "IOTA", "MIOTA": "IOTA",
    "QTUM": "Qtum", "ZEC": "Zcash", "DASH": "Dash", "XMR": "Monero",
    "BNB": "Binance Coin", "MATIC": "Polygon", "POL": "POL (ex-MATIC)",
    "ARB": "Arbitrum", "OP": "Optimism", "SUI": "Sui", "APT": "Aptos",
    "NEAR": "Near Protocol", "INJ": "Injective Protocol", "TIA": "Celestia",
    "SEI": "Sei", "PEPE": "Pepe", "FLOKI": "FLOKI", "BONK": "Bonk",
    "WIF": "dogwifhat", "JUP": "Jupiter", "PYTH": "Pyth Network",
    "ONDO": "Ondo", "HBAR": "Hedera", "KAS": "Kaspa", "RUNE": "THORChain",
    "FTM": "Fantom", "S": "Sonic (prev. FTM)", "USDC": "USD Coin",
    "EURC": "EURC", "EUROP": "EUROP",
}


def _coin_display_name(symbol, api_name=None):
    """Voller Anzeigename: API-Name, sonst Fallback-Map, sonst das Kürzel."""
    name = (api_name or "").strip()
    if name and name != symbol:
        return name
    return COIN_NAMES_FALLBACK.get(symbol, symbol)

########################################
# 3) DB-Funktionen mit Context Manager
########################################
def init_db():
    with sqlite3.connect(DB_NAME) as conn:
        c = conn.cursor()

        # Tabelle credentials (API-Keys)
        c.execute("""
        CREATE TABLE IF NOT EXISTS credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            api_key TEXT,
            api_secret TEXT
        )
        """)

        # Tabelle schedules (Planung)
        c.execute("""
        CREATE TABLE IF NOT EXISTS schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            weekday TEXT,
            time_of_day TEXT
        )
        """)

        # Spalten-Migration schedules: optionale Handelsregeln
        for (col, decl) in [
            ("rule_max_price", "REAL"),
            ("rule_ath_dip_pct", "REAL"),
            ("rule_sma", "TEXT DEFAULT 'off'"),
            ("rule_rsi_min", "REAL"),
            ("rule_rsi_max", "REAL"),
            ("rule_max_invest", "REAL"),
        ]:
            cols = [r[1] for r in c.execute("PRAGMA table_info(schedules)").fetchall()]
            if col not in cols:
                c.execute(f"ALTER TABLE schedules ADD COLUMN {col} {decl}")

        # schedule_lines (Detailzeilen je Schedule)
        c.execute("""
        CREATE TABLE IF NOT EXISTS schedule_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER,
            asset TEXT,
            amount_eur REAL
        )
        """)

        # Trades (abgeschlossene Käufe)
        c.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            asset TEXT,
            amount_eur REAL,
            filled_asset REAL,
            avg_price REAL,
            order_id TEXT
        )
        """)

        # balances (Kontostands-Snapshots)
        c.execute("""
        CREATE TABLE IF NOT EXISTS balances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            currency TEXT,
            amount REAL
        )
        """)

        # historical_rates (historische Kurse)
        c.execute("""
        CREATE TABLE IF NOT EXISTS historical_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE,
            asset TEXT,
            price_eur REAL
        )
        """)

    # Spalten-Migration für bereits bestehende Datenbanken
    for (col, decl) in [("side", "TEXT"), ("trade_id", "TEXT"), ("fee", "REAL"), ("fee_currency", "TEXT")]:
        cols = [r[1] for r in c.execute("PRAGMA table_info(trades)").fetchall()]
        if col not in cols:
            c.execute(f"ALTER TABLE trades ADD COLUMN {col} {decl}")

    # Eindeutigkeits-Index für den Trade-Import (Deduplizierung anhand der Trade-ID)
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_trades_asset_trade_id "
              "ON trades(asset, trade_id) WHERE trade_id IS NOT NULL")

    # E-Mail-Einstellungen
    c.execute("""
    CREATE TABLE IF NOT EXISTS email_settings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            smtp_server TEXT,
            smtp_port INTEGER,
            smtp_user TEXT,
            smtp_pass TEXT,
            from_email TEXT,
            to_email TEXT,
            send_on_success INTEGER,
            send_on_error INTEGER,
            use_tls INTEGER
        )
        """)

    # Transfers (Ein-/Auszahlungen von Coins) – vervollständigen die FIFO-Steuerbasis
    c.execute("""
        CREATE TABLE IF NOT EXISTS transfers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp DATETIME,
            asset TEXT,
            amount REAL,
            kind TEXT,
            tx_id TEXT,
            fee REAL,
            fee_currency TEXT,
            price_eur REAL
        )
        """)
    # Deduplizierung über die Bitvavo-Transaction-ID (txId)
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_transfers_tx "
              "ON transfers(asset, kind, tx_id) WHERE tx_id IS NOT NULL")

    # App-weite Einstellungen (z. B. Schwellen der Verkaufsempfehlungen)
    c.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value REAL
        )
        """)


def get_connection():
    conn = sqlite3.connect(DB_NAME)
    # Fremdschlüssel aktivieren: verhindert z. B. schedule_lines ohne Zeitplan
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def dedupe_trades():
    """
    Entfernt doppelte Trade-Zeilen (Altbestand aus Imports vor dem Trade-ID-Dedup:
    dieselbe Order/Fill wurde durch mehrere Importläufe mehrfach eingefügt, weil
    trade_id damals immer NULL war). Behält pro Gruppe die älteste Zeile (MIN(id)).
    Wird beim Start und vor jedem Importlauf ausgeführt.
    """
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("""
            DELETE FROM trades WHERE id NOT IN (
                SELECT MIN(id) FROM trades
                GROUP BY asset, COALESCE(trade_id, ''), order_id, timestamp, side,
                         ROUND(filled_asset, 10), ROUND(avg_price, 10),
                         ROUND(COALESCE(amount_eur, 0), 10),
                         ROUND(COALESCE(fee, 0), 10),
                         COALESCE(fee_currency, '')
            )
        """)
        removed = c.rowcount
        conn.commit()
    if removed:
        logging.info(f"dedupe_trades: {removed} doppelte Trade-Zeilen entfernt.")
    return removed


def dedupe_transfers():
    """Entfernt doppelte Transfer-Zeilen (gleiche Tx-ID/Asset/Art/Zeitpunkt)."""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("""
            DELETE FROM transfers WHERE id NOT IN (
                SELECT MIN(id) FROM transfers
                GROUP BY asset, kind, tx_id,
                         ROUND(COALESCE(amount, 0), 10), timestamp
            )
        """)
        removed = c.rowcount
        conn.commit()
    if removed:
        logging.info(f"dedupe_transfers: {removed} doppelte Transfer-Zeilen entfernt.")
    return removed


########################################
# 3b) Strategie-/Empfehlungs-Einstellungen (konfigurierbar)
########################################
# Defaults; Überschreibbar über app_settings (Einstellungen -> Verkaufsempfehlungen)
STRATEGY_DEFAULTS = {
    "enabled_s1": 1.0,        # steuerfrei im Plus
    "enabled_s2": 1.0,        # ATH-Nähe
    "enabled_s3": 1.0,        # Rebalancing
    "enabled_s4": 1.0,        # Gewinnziel (25 % bei +50 %)
    "enabled_s5": 1.0,        # bald steuerfrei
    "min_value_eur": 10.0,    # Mindest-Positionswert für Empfehlungen
    "rebalance_share": 0.40,  # > 40 % Portfolioanteil
    "ath_near_pct": -5.0,     # innerhalb 5 % unter dem 90-Tage-Hoch
    "s4_gain_pct": 50.0,      # Gewinnziel: +50 %
    "s4_sell_pct": 25.0,      # dann 25 % des Bestands verkaufen
}


def get_strategy_settings():
    """Liefert die aktuellen Empfehlungs-Einstellungen (DB-Werte überschreiben Defaults)."""
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT key, value FROM app_settings")
        rows = c.fetchall()
    s = dict(STRATEGY_DEFAULTS)
    for (k, v) in rows:
        if k in s and v is not None:
            s[k] = float(v)
    return s


def set_strategy_setting(key, value):
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("INSERT INTO app_settings (key, value) VALUES (?, ?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, float(value)))
        conn.commit()


def _bool_setting(on):
    return 1.0 if on else 0.0


def get_onboarding():
    """
    Geführter Erststart: 4 Schritte, deren Status aus dem echten App-Zustand
    abgeleitet wird (Credentials, Import, Portfolio-Besuch, Zeitplan).
    Liefert eine Liste von Schritten mit Ziel-URL und Beschreibung.
    """
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT api_key, api_secret FROM credentials ORDER BY id DESC LIMIT 1")
        cred = c.fetchone()
        c.execute("SELECT COUNT(*) FROM trades")
        trades = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM schedules")
        scheds = c.fetchone()[0]
        c.execute("SELECT value FROM app_settings WHERE key='onboarding_portfolio_seen'")
        seen_row = c.fetchone()
        c.execute("SELECT value FROM app_settings WHERE key='onboarding_dismissed'")
        dismissed_row = c.fetchone()
    seen = bool(seen_row and seen_row[0])
    if dismissed_row and dismissed_row[0]:
        # Nutzer hat den Assistenten dauerhaft ausgeblendet -> nicht mehr anzeigen
        return []
    return [
        {
            "id": "credentials",
            "title": "API-Key hinterlegen",
            "desc": "Trage deinen Bitvavo-API-Key ein – Key UND Secret brauchst du für Import, Kontostand und automatische Käufe.",
            "done": bool(cred and cred[0] and cred[1]),
            "url": "/settings",
            "btn": "Zu den Einstellungen",
        },
        {
            "id": "import",
            "title": "Historie importieren",
            "desc": "Hole alle Käufe, Verkäufe und Transfers aus deinem Bitvavo-Konto – daraus berechnet die App deine Steuern (FIFO).",
            "done": trades > 0,
            "url": "/trades",
            "btn": "Zu den Trades",
        },
        {
            "id": "portfolio",
            "title": "Portfolio prüfen",
            "desc": "Schau dir Bestände, Steuerbasis und Verkaufsempfehlungen an – oft lohnt sich ein Blick vor dem ersten Verkauf.",
            "done": trades > 0 and seen,
            "url": "/portfolio",
            "btn": "Zum Portfolio",
        },
        {
            "id": "schedule",
            "title": "Zeitplan anlegen",
            "desc": "Richte deinen Sparplan ein – der Bot kauft dann automatisch in festen Abständen, völlig ohne Zutun.",
            "done": scheds > 0,
            "url": "/add_schedule",
            "btn": "Zeitplan-Assistent",
        },
    ]


def mark_onboarding_portfolio_seen():
    """Setzt das Flag, sobald der Nutzer die Portfolio-Seite öffnet (Schritt 3 des Erststarts)."""
    set_strategy_setting("onboarding_portfolio_seen", 1.0)


########################################
# 3c) Öffentliche Assets (für Zeitplan-Vorschläge) + historische Tageskurse
########################################
TOP_ASSETS_CACHE = {}
ALL_ASSETS_CACHE = {}
HIST_PRICE_CACHE = {}
HIST_CACHE_TTL = 3600  # historische Tageskurse 1 h cachen
CACHE_TTL = 600        # 10 Minuten Cache (Preis-/Indikator-Daten)


def get_all_assets():
    """
    ALLE handelbaren Coins (EUR-Märkte) mit vollständigem Namen, Live-Preis und
    24h-Änderung über die öffentliche API (/ticker/24h + /assets für die Namen).
    Gibt [{symbol, name, price, change, volume_eur}] oder [] bei Fehler zurück.
    """
    cached = ALL_ASSETS_CACHE.get("list")
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    try:
        res = get_public_client().ticker24h({})
        if isinstance(res, dict) and "errorCode" in res:
            return []
        # Vollständige Coin-Namen über den öffentlichen /assets-Endpoint
        names = {}
        try:
            assets_res = get_public_client().assets({})
            if isinstance(assets_res, list):
                for a in assets_res:
                    if isinstance(a, dict) and a.get("symbol"):
                        names[a["symbol"]] = a.get("name") or a["symbol"]
        except Exception:
            names = {}
        entries = []
        for t in res or []:
            if not isinstance(t, dict):
                continue
            market = t.get("market", "")
            if not market.endswith("-EUR"):
                continue
            symbol = market[:-4]
            try:
                price = float(t.get("last") or 0.0)
            except (TypeError, ValueError):
                price = 0.0
            try:
                open_p = float(t.get("open") or 0.0)
            except (TypeError, ValueError):
                open_p = 0.0
            try:
                vol_q = float(t.get("volumeQuote") or 0.0)
            except (TypeError, ValueError):
                vol_q = 0.0
            change = ((price - open_p) / open_p * 100.0) if open_p else 0.0
            entries.append({
                "symbol": symbol,
                "name": _coin_display_name(symbol, names.get(symbol)),
                "price": price,
                "change": change,
                "volume_eur": vol_q,
            })
        entries.sort(key=lambda e: e["volume_eur"], reverse=True)
        ALL_ASSETS_CACHE["list"] = (time.time(), entries)
        return entries
    except Exception as e:
        logging.warning(f"get_all_assets fehlgeschlagen: {e}")
        return []


def get_top_assets(n=10):
    """
    Top-N handelbare Coins (EUR-Märkte) nach 24h-Volumen über die öffentliche API
    (/ticker/24h). Gibt [{symbol, volume_eur, price}] oder [] bei Fehler zurück.
    """
    cached = TOP_ASSETS_CACHE.get("list")
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1][:n]
    try:
        res = get_public_client().ticker24h({})
        if isinstance(res, dict) and "errorCode" in res:
            return []
        entries = []
        for t in res or []:
            if not isinstance(t, dict):
                continue
            market = t.get("market", "")
            if not market.endswith("-EUR"):
                continue
            try:
                vol_q = float(t.get("volumeQuote") or 0.0)
            except (TypeError, ValueError):
                vol_q = 0.0
            try:
                price = float(t.get("last") or 0.0)
            except (TypeError, ValueError):
                price = 0.0
            if vol_q <= 0:
                continue
            entries.append({"symbol": market[:-4], "volume_eur": vol_q, "price": price})
        entries.sort(key=lambda e: e["volume_eur"], reverse=True)
        TOP_ASSETS_CACHE["list"] = (time.time(), entries)
        return entries[:n]
    except Exception as e:
        logging.warning(f"get_top_assets fehlgeschlagen: {e}")
        return []


def get_historical_day_price(asset, day):
    """
    EUR-Tageskurs (Schlusskurs) eines Coins an einem Datum über öffentliche
    1d-Candles. Die SDK erwartet für start/end datetime-Objekte (wandelt sie
    intern über _epoch_millis um); als Fallback wird über die letzten 1000 Tage
    gescannt. Rückgabe float oder None (kein Markt / kein Kurs verfügbar).
    """
    key = (asset.upper(), day.strftime("%Y-%m-%d"))
    cached = HIST_PRICE_CACHE.get(key)
    if cached and time.time() - cached[0] < HIST_CACHE_TTL:
        return cached[1]
    price = None
    try:
        day0 = datetime.datetime(day.year, day.month, day.day)
        res = get_public_client().candles(
            f"{asset.upper()}-EUR", "1d",
            start=day0 - timedelta(days=1), end=day0 + timedelta(days=1))
        if isinstance(res, list) and res:
            price = float(res[-1][4])  # letzte Kerze im Fenster = Tagesschluss
            if not price:
                price = None
        # Fallback: letzte 1000 Tage scannen (z. B. wenn das Fenster leer war)
        if not price:
            res2 = get_public_client().candles(f"{asset.upper()}-EUR", "1d", limit=1000)
            if isinstance(res2, list):
                hits = [c for c in res2
                        if datetime.datetime.fromtimestamp(c[0] / 1000.0).date() == day]
                if hits:
                    price = float(hits[-1][4])
    except Exception as e:
        logging.warning(f"get_historical_day_price({asset}, {day}) fehlgeschlagen: {e}")
    HIST_PRICE_CACHE[key] = (time.time(), price)
    return price


########################################
# 3d) Handelsstatus des API-Keys (gecacht, für die Verkaufs-UI)
########################################
TRADING_STATUS_CACHE = {}


def get_trading_status():
    """
    Prüft, ob echte Market-Orders möglich sind (Key + Secret + trading-Berechtigung).
    Rückgabe: {"ok": bool, "reason": str}
    """
    cached = TRADING_STATUS_CACHE.get("status")
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    status = {"ok": False, "reason": ""}
    if SIMULATION_MODE:
        status["reason"] = "Simulationsmodus aktiv – keine echten Orders."
    else:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT api_key, api_secret FROM credentials ORDER BY id DESC LIMIT 1")
            cred = c.fetchone()
        if not cred or not cred[0] or not cred[1]:
            status["reason"] = "Kein vollständiger API-Key hinterlegt (Key + Secret nötig)."
        else:
            try:
                bv = get_bitvavo_client()
                acc = bitvavo_request_with_retry(bv.account)
                if isinstance(acc, dict) and "errorCode" in acc:
                    status["reason"] = f"Bitvavo-Fehler {acc['errorCode']}: {acc.get('error', '')}"
                elif acc.get("trading"):
                    status["ok"] = True
                else:
                    status["reason"] = "API-Key hat KEINE Handelsberechtigung (Read-only-Key)."
            except Exception as e:
                status["reason"] = f"Prüfung fehlgeschlagen: {str(e)}"
    TRADING_STATUS_CACHE["status"] = (time.time(), status)
    return status


########################################
# 4) Einfache Authentifizierung
########################################
@app.before_request
def require_login():
    """
    Blockt alle Seiten bis auf /login und /do_login, falls nicht eingeloggt.
    """
    allowed_paths = ["/login", "/do_login", "/static", "/health"]
    if not session.get("logged_in") and not request.path.startswith(tuple(allowed_paths)):
        return redirect(url_for("login"))


@app.route("/login", methods=["GET"])
def login():
    return render_template("login.html", login_pepper=LOGIN_PEPPER)


@app.route("/do_login", methods=["POST"])
def do_login():
    pw = request.form.get("password", "")
    remember = request.form.get("remember") == "on"
    if pw == MASTER_PASSWORD:
        session["logged_in"] = True
        # Bei "Angemeldet bleiben" wird das Session-Cookie zum 30-Tage-Cookie
        session.permanent = remember
        logging.info(f"Login erfolgreich (remember={remember}).")
        return redirect(url_for("index"))
    else:
        logging.warning("Falsches Passwort beim Login.")
        flash("Falsches Passwort!", "error")
        return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.clear()
    flash("Du wurdest abgemeldet.", "info")
    return redirect(url_for("login"))


########################################
# 5) E-Mail-Einstellungen
########################################
def load_email_settings():
    """
    Lädt die E-Mail-Einstellungen aus der DB (letzter Eintrag).
    Gibt ein Dict oder None zurück, wenn nichts gespeichert.
    """
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT smtp_server, smtp_port, smtp_user, smtp_pass, from_email, to_email,
                   send_on_success, send_on_error, use_tls
            FROM email_settings
            ORDER BY id DESC
            LIMIT 1
        """)
        row = c.fetchone()

    if row:
        return {
            "smtp_server":     row[0],
            "smtp_port":       row[1],
            "smtp_user":       row[2],
            "smtp_pass":       row[3],
            "from_email":      row[4],
            "to_email":        row[5],
            "send_on_success": bool(row[6]),
            "send_on_error":   bool(row[7]),
            "use_tls":         bool(row[8]),
        }
    else:
        return None


def send_email(subject, body):
    """
    Sendet eine E-Mail mit den in der DB gespeicherten SMTP-Einstellungen.
    Nutzt ggf. STARTTLS (Port 587), wenn 'use_tls' konfiguriert ist.
    """
    settings = load_email_settings()
    if not settings:
        logging.warning("send_email aufgerufen, aber keine E-Mail-Einstellungen konfiguriert.")
        return

    try:
        msg = MIMEMultipart()
        msg["From"] = settings["from_email"]
        msg["To"]   = settings["to_email"]
        msg["Subject"] = subject

        msg.attach(MIMEText(body, "plain"))

        # SMTP verbinden
        server = smtplib.SMTP(settings["smtp_server"], settings["smtp_port"], timeout=10)

        # Wenn TLS gewünscht (z.B. Port 587) -> STARTTLS
        if settings["use_tls"]:
            server.starttls()

        # Falls SMTP-Login nötig
        if settings["smtp_user"] and settings["smtp_pass"]:
            server.login(settings["smtp_user"], settings["smtp_pass"])

        server.send_message(msg)
        server.quit()

        logging.info(f"E-Mail verschickt: Betreff='{subject}' an {settings['to_email']}")

    except Exception as e:
        logging.error(f"Fehler beim E-Mail-Versand: {str(e)}")


########################################
# 6) Route: Einstellungen (API + Mail) + Test-E-Mail
########################################
@app.route("/dismiss_onboarding", methods=["POST"])
def dismiss_onboarding():
    """
    Blendet den Onboarding-Assistenten dauerhaft aus (Flag in app_settings).
    Der Assistent kommt danach nicht mehr wieder – auch nicht nach Neustart.
    """
    set_strategy_setting("onboarding_dismissed", 1.0)
    logging.info("Onboarding-Assistent dauerhaft ausgeblendet (onboarding_dismissed=1).")
    return jsonify({"ok": True})


@app.route("/settings", methods=["GET", "POST"])
def settings():
    """
    Gemeinsame Seite für:
      1) API-Key-Einstellungen
      2) E-Mail-Einstellungen
      3) Test-E-Mail-Versand
    """
    if request.method == "POST":
        action = request.form.get("action")
        with get_connection() as conn:
            c = conn.cursor()

            # 1) API-Key speichern
            if action == "save_api":
                new_key = request.form.get("api_key", "").strip()
                new_secret = request.form.get("api_secret", "").strip()
                try:
                    stored_secret = encrypt_secret(new_secret)
                except Exception as e:
                    flash(f"Secret konnte nicht verschlüsselt werden: {e}", "error")
                    logging.error(f"save_api: Verschlüsselung fehlgeschlagen: {e}")
                    return redirect(url_for("settings"))
                c.execute("DELETE FROM credentials")  # Nur 1 Datensatz halten
                c.execute("INSERT INTO credentials (api_key, api_secret) VALUES (?, ?)",
                          (new_key, stored_secret))
                conn.commit()
                flash("API-Credentials wurden gespeichert.")
                logging.info("API-Credentials gespeichert/aktualisiert.")

            # 2) API-Key löschen
            elif action == "delete_api":
                c.execute("DELETE FROM credentials")
                conn.commit()
                flash("API-Credentials wurden gelöscht.")
                logging.info("API-Credentials gelöscht.")

            # 3) E-Mail-Einstellungen speichern
            elif action == "save_email":
                smtp_server = request.form.get("smtp_server", "").strip()
                smtp_port   = request.form.get("smtp_port", "587").strip()
                smtp_user   = request.form.get("smtp_user", "").strip()
                smtp_pass   = request.form.get("smtp_pass", "").strip()
                from_email  = request.form.get("from_email", "").strip()
                to_email    = request.form.get("to_email", "").strip()

                send_on_success = 1 if request.form.get("send_on_success") == "on" else 0
                send_on_error   = 1 if request.form.get("send_on_error") == "on" else 0
                use_tls         = 1 if request.form.get("use_tls") == "on" else 0

                # Alten Eintrag löschen, nur 1 Satz wird vorgehalten
                c.execute("DELETE FROM email_settings")
                c.execute("""
                    INSERT INTO email_settings (
                        smtp_server, smtp_port, smtp_user, smtp_pass,
                        from_email, to_email,
                        send_on_success, send_on_error, use_tls
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    smtp_server, smtp_port, smtp_user, smtp_pass,
                    from_email, to_email,
                    send_on_success, send_on_error, use_tls
                ))
                conn.commit()

                flash("E-Mail-Einstellungen wurden aktualisiert.")
                logging.info("E-Mail-Einstellungen wurden aktualisiert.")

            # 4) Test-E-Mail versenden
            elif action == "test_email":
                # Wir schicken eine Test-E-Mail
                subject = "Test-E-Mail von Bitvavo-Autobot"
                body = (
                    "Hallo,\n\n"
                    "dies ist eine Test-E-Mail vom Bitmaster Tool.\n"
                    "Wenn du diese Mail siehst, funktioniert dein SMTP-Setup!\n"
                )
                send_email(subject, body)
                flash("Test-E-Mail wurde verschickt (siehe Logs für Details).")

            # 5) Verkaufsempfehlungs-Einstellungen speichern
            elif action == "save_strategies":
                cur_s = get_strategy_settings()

                def _num(key, lo, hi):
                    try:
                        v = float(request.form.get(key, ""))
                    except (TypeError, ValueError):
                        v = cur_s[key]
                    return min(max(v, lo), hi)

                updates = {
                    "enabled_s1": _bool_setting(request.form.get("enabled_s1") == "on"),
                    "enabled_s2": _bool_setting(request.form.get("enabled_s2") == "on"),
                    "enabled_s3": _bool_setting(request.form.get("enabled_s3") == "on"),
                    "enabled_s4": _bool_setting(request.form.get("enabled_s4") == "on"),
                    "enabled_s5": _bool_setting(request.form.get("enabled_s5") == "on"),
                    "min_value_eur": _num("min_value_eur", 0.0, 1e9),
                    "rebalance_share": _num("rebalance_share", 1.0, 99.0) / 100.0,
                    "ath_near_pct": _num("ath_near_pct", -99.0, 0.0),
                    "s4_gain_pct": _num("s4_gain_pct", 1.0, 1000.0),
                    "s4_sell_pct": _num("s4_sell_pct", 1.0, 100.0),
                }
                for k, v in updates.items():
                    set_strategy_setting(k, v)
                flash("Verkaufsempfehlungs-Einstellungen gespeichert.", "success")
                logging.info("Strategie-Einstellungen aktualisiert.")

            # 6) Onboarding-Assistent anzeigen / ausblenden
            elif action == "save_onboarding":
                shown = request.form.get("onboarding_active") == "on"
                set_strategy_setting("onboarding_dismissed", _bool_setting(not shown))
                if shown:
                    flash("Onboarding-Assistent ist wieder aktiv – er erscheint beim nächsten "
                          "Dashboard-Besuch, bis alle Schritte erledigt sind.", "success")
                else:
                    flash("Onboarding-Assistent ausgeblendet – er kommt nicht mehr wieder.", "info")
                logging.info(f"Onboarding-Anzeige geändert: {'aktiv' if shown else 'ausgeblendet'}")

        return redirect(url_for("settings"))

    # GET: Aktuelle Werte laden
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT api_key, api_secret FROM credentials ORDER BY id DESC LIMIT 1")
        api_row = c.fetchone()

    secret_error = ""
    api_permissions = None
    api_permission_error = ""
    api_secret_missing = False
    if api_row:
        saved_api_key, saved_api_secret_raw = api_row
        saved_api_secret = saved_api_secret_raw
        if saved_api_secret and saved_api_secret.startswith("enc:"):
            try:
                saved_api_secret = decrypt_secret(saved_api_secret)
            except Exception as e:
                saved_api_secret = ""
                secret_error = str(e)
        mask_key = saved_api_key[:5] + "..." if saved_api_key else ""
        mask_secret = saved_api_secret[:5] + "..." if saved_api_secret else ""

        api_secret_missing = bool(saved_api_key) and not saved_api_secret_raw

        # Berechtigungen des hinterlegten Keys prüfen (/account-Endpunkt)
        if saved_api_key and saved_api_secret and not SIMULATION_MODE and not secret_error:
            try:
                bv = get_bitvavo_client()
                acc = bitvavo_request_with_retry(bv.account)
                if isinstance(acc, dict) and "errorCode" in acc:
                    api_permission_error = f"Prüfung fehlgeschlagen – Fehler {acc['errorCode']}: {acc.get('error', '')}"
                else:
                    api_permissions = {
                        "trading": bool(acc.get("trading")),
                        "withdrawal": bool(acc.get("withdrawal")),
                    }
            except Exception as e:
                api_permission_error = f"Prüfung fehlgeschlagen: {str(e)}"
        elif SIMULATION_MODE and saved_api_key:
            api_permission_error = "Simulationsmodus aktiv – Berechtigungen werden nicht geprüft."
    else:
        mask_key = ""
        mask_secret = ""

    mail_settings = load_email_settings()
    strategy = get_strategy_settings()

    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT value FROM app_settings WHERE key='onboarding_dismissed'")
        onb_row = c.fetchone()
    onboarding_dismissed = bool(onb_row and onb_row[0])

    return render_template(
        "settings.html",
        strategy=strategy,
        mask_key=mask_key,
        mask_secret=mask_secret,
        mail_settings=mail_settings,
        encryption_enabled=ENCRYPTION_KEY is not None,
        secret_error=secret_error,
        api_permissions=api_permissions,
        api_permission_error=api_permission_error,
        api_secret_missing=api_secret_missing,
        onboarding_dismissed=onboarding_dismissed
    )


########################################
# 7) Bitvavo-Client (optional) + Mock-Order
########################################
def get_bitvavo_client():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT api_key, api_secret FROM credentials ORDER BY id DESC LIMIT 1")
        row = c.fetchone()

    if not row:
        raise Exception("Keine API-Credentials hinterlegt. Bitte in den Einstellungen hinzufügen.")

    api_key, api_secret = row
    try:
        api_secret = decrypt_secret(api_secret)
    except Exception as e:
        raise Exception(f"API-Secret konnte nicht entschlüsselt werden: {str(e)}")
    return Bitvavo({
        'APIKEY': api_key,
        'APISECRET': api_secret,
        'RESTURL': 'https://api.bitvavo.com/v2',
        'WSURL': 'wss://ws.bitvavo.com/v2/',
        'ACCESSWINDOW': 30000
    })


def place_mock_order(asset, amount_eur):
    """
    Simuliert einen Kauf und gibt eine Fake-Response zurück.
    """
    logging.info(f"SIMULATION: Würde jetzt {amount_eur} EUR in {asset} investieren.")
    fake_price = 25000.0
    fake_filled_amount = float(amount_eur) / fake_price

    return {
        "orderId": "SIM-ORDER-12345",
        "fills": [
            {
                "amount": str(fake_filled_amount),
                "price": str(fake_price)
            }
        ]
    }


########################################
# 8) RETRY-Logik für Bitvavo-Aufrufe
########################################
def bitvavo_request_with_retry(func, *args, max_retries=3, **kwargs):
    attempt = 0
    while attempt < max_retries:
        try:
            return func(*args, **kwargs)
        except Exception as e:
            logging.warning(f"Bitvavo-Aufruf fehlgeschlagen (Versuch {attempt+1}/{max_retries}): {str(e)}")
            attempt += 1
            time.sleep(2)
    raise Exception(f"Bitvavo-Aufruf fehlgeschlagen nach {max_retries} Versuchen")


########################################
# 8b) Öffentlicher Bitvavo-Client, Kurse & Indikatoren (ohne API-Keys)
#     -> tickerPrice/candles sind öffentliche Endpunkte und brauchen
#        KEINE Credentials. Wichtig: mit gesetztem, aber ungültigem Key
#        lehnt Bitvavo signierte Public-Calls ab (Fehler 309).
########################################
_PUBLIC_BV = None
PRICE_CACHE = {}
INDICATOR_CACHE = {}
TAX_FREE_DAYS = 365      # Haltefrist für steuerfreie Verkäufe (Deutschland)
MATURITY_NOTICE_DAYS = 10  # Warnung, wenn Haltefrist bald erreicht wird
FREIGRENZE_EUR = 600.0   # Freigrenze für Gewinne aus privaten Veräußerungsgeschäften (Deutschland)


def get_public_client():
    global _PUBLIC_BV
    if _PUBLIC_BV is None:
        _PUBLIC_BV = Bitvavo({'RESTURL': 'https://api.bitvavo.com/v2'})
    return _PUBLIC_BV


def get_current_price(asset):
    """Aktueller Kurs (EUR) über die öffentliche Bitvavo-API, mit Cache."""
    cached = PRICE_CACHE.get(asset)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    try:
        res = get_public_client().tickerPrice({"market": f"{asset.upper()}-EUR"})
        if isinstance(res, dict) and "errorCode" in res:
            raise Exception(f"{res.get('errorCode')} {res.get('error', '')}")
        price = float(res.get("price", 0.0))
        PRICE_CACHE[asset] = (time.time(), price)
        return price
    except Exception as e:
        logging.warning(f"get_current_price({asset}) fehlgeschlagen: {e}")
        return None


def fetch_candles(asset, limit=90):
    """Tägliche Candles (öffentlich): [[ts, open, high, low, close, volume], ...]"""
    res = get_public_client().candles(f"{asset.upper()}-EUR", "1d", limit=limit)
    if isinstance(res, dict) and "errorCode" in res:
        raise Exception(f"{res.get('errorCode')} {res.get('error', '')}")
    return res or []


def _sma(values, n):
    if len(values) < n:
        return None
    return sum(values[-n:]) / n


def _rsi14(closes):
    """RSI mit Wilder-Glättung über 14 Perioden."""
    if len(closes) < 15:
        return None
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:14]) / 14
    avg_loss = sum(losses[:14]) / 14
    for i in range(14, len(deltas)):
        avg_gain = (avg_gain * 13 + gains[i]) / 14
        avg_loss = (avg_loss * 13 + losses[i]) / 14
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def compute_indicators(asset):
    """SMA9, SMA50, RSI14, ATH (90 Tage) + aktueller Kurs, gecacht."""
    cached = INDICATOR_CACHE.get(asset)
    if cached and time.time() - cached[0] < CACHE_TTL:
        return cached[1]
    candles = fetch_candles(asset, 90)
    if not candles:
        return None
    closes = [float(c[4]) for c in candles]
    highs = [float(c[2]) for c in candles]
    price = closes[-1]
    ath = max(highs)
    data = {
        "price": price,
        "sma9": _sma(closes, 9),
        "sma50": _sma(closes, 50),
        "rsi14": _rsi14(closes),
        "ath": ath,
        "ath_distance_pct": (price / ath - 1.0) * 100.0 if ath else None,
    }
    INDICATOR_CACHE[asset] = (time.time(), data)
    return data


def _to_float_or_none(value):
    try:
        v = float(value)
        return v
    except (TypeError, ValueError):
        return None


def check_trade_rules(asset, amount_eur, current_price, rules):
    """
    Prüft die Handelsregeln eines Zeitplans vor dem Kauf.
    Gibt den Skipping-Grund als String zurück – oder None, wenn gekauft werden darf.
    """
    if not rules:
        return None

    if rules.get("max_price") and current_price and current_price > rules["max_price"]:
        return f"Max-Preis überschritten (Kurs {current_price:.2f} EUR > {rules['max_price']:.2f} EUR)"

    if rules.get("max_invest"):
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("SELECT COALESCE(SUM(amount_eur), 0) FROM trades "
                      "WHERE asset=? AND (side IS NULL OR side='buy')", (asset.upper(),))
            invested = c.fetchone()[0] or 0.0
        if invested + amount_eur > rules["max_invest"]:
            return (f"Max-Investment erreicht ({invested:.2f} EUR investiert + {amount_eur:.2f} EUR "
                    f"> {rules['max_invest']:.2f} EUR)")

    needs_indicators = (rules.get("ath_dip_pct") or
                        (rules.get("sma") and rules["sma"] != "off") or
                        rules.get("rsi_min") is not None or rules.get("rsi_max") is not None)
    if not needs_indicators:
        return None

    try:
        ind = compute_indicators(asset)
    except Exception as e:
        logging.warning(f"Indikatoren für {asset} nicht verfügbar: {e}")
        ind = None
    if ind is None:
        return "Indikatoren nicht verfügbar – Regel kann nicht bewertet werden"

    if rules.get("ath_dip_pct") and ind.get("ath"):
        dist = ind["ath_distance_pct"]
        if dist > -abs(rules["ath_dip_pct"]):
            return f"Kurs nur {dist:.1f}% unter ATH (Mindestabstand {abs(rules['ath_dip_pct'])}%)"

    if rules.get("sma") and rules["sma"] != "off":
        sma = ind.get("sma9" if rules["sma"] == "below_sma9" else "sma50")
        if sma is not None and current_price >= sma:
            return f"Kurs {current_price:.2f} ≥ {rules['sma']} ({sma:.2f} EUR)"

    if (rules.get("rsi_min") is not None or rules.get("rsi_max") is not None) and ind.get("rsi14") is not None:
        rsi = ind["rsi14"]
        rmin = rules.get("rsi_min")
        rmax = rules.get("rsi_max")
        if (rmin is not None and rsi < rmin) or (rmax is not None and rsi > rmax):
            return f"RSI {rsi:.1f} außerhalb des Bandes ({rmin or '-'} – {rmax or '-'})"

    return None


########################################
# 9) Scheduler-Logik
########################################
def load_schedules_into_scheduler():
    schedule.clear()
    # Täglicher Job um 00:00 Uhr -> update_prices_for_assets
    schedule.every().day.at("00:00").do(update_prices_for_assets)

    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, weekday, time_of_day FROM schedules ORDER BY id")
        schedules_rows = c.fetchall()

    weekday_mapping = {
        "Monday": schedule.every().monday,
        "Tuesday": schedule.every().tuesday,
        "Wednesday": schedule.every().wednesday,
        "Thursday": schedule.every().thursday,
        "Friday": schedule.every().friday,
        "Saturday": schedule.every().saturday,
        "Sunday": schedule.every().sunday
    }

    for (sched_id, wd, tod) in schedules_rows:
        if wd not in weekday_mapping:
            logging.warning(f"Ungültiger Wochentag in DB: {wd}")
            continue

        def job_func(schedule_id=sched_id):
            execute_investment(schedule_id)

        weekday_mapping[wd].at(tod).do(job_func).tag(f"schedule_{sched_id}")


def execute_investment(schedule_id):
    """
    Führt für schedule_id alle definierten Käufe durch.
    """
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT asset, amount_eur FROM schedule_lines WHERE schedule_id = ?", (schedule_id,))
        lines = c.fetchall()

    if not lines:
        logging.info(f"Schedule {schedule_id} hat keine lines definiert.")
        return

    # Optionale Handelsregeln des Zeitplans laden
    rules = None
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT rule_max_price, rule_ath_dip_pct, rule_sma, rule_rsi_min, "
                  "rule_rsi_max, rule_max_invest FROM schedules WHERE id=?", (schedule_id,))
        r = c.fetchone()
        if r:
            rules = {
                "max_price": r[0], "ath_dip_pct": r[1], "sma": r[2] or "off",
                "rsi_min": r[3], "rsi_max": r[4], "max_invest": r[5],
            }

    email_config = load_email_settings()

    # Bitvavo-Client nur laden, wenn wir nicht simulieren
    if not SIMULATION_MODE:
        try:
            bv = get_bitvavo_client()
        except Exception as e:
            logging.error(f"execute_investment: Kein Bitvavo-Client verfügbar: {str(e)}")
            # E-Mail bei Fehler?
            if email_config and email_config["send_on_error"]:
                subject = f"Fehler bei Schedule {schedule_id}"
                body = f"Konnte keinen Bitvavo-Client erstellen: {str(e)}"
                send_email(subject, body)
            return

    for (asset, amount_eur) in lines:
        try:
            market_symbol = f"{asset.upper()}-EUR"

            # Aktuellen Kurs abrufen (öffentliche API, kein Key nötig)
            if SIMULATION_MODE:
                current_price = 25000.0
            else:
                current_price = get_current_price(asset) or 0.0

            if not current_price:
                logging.warning(f"Schedule {schedule_id}: kein Kurs für {asset} verfügbar – Kauf übersprungen.")
                continue

            # ---- Handelsregeln prüfen ----
            skip_reason = check_trade_rules(asset, amount_eur, current_price, rules)
            if skip_reason:
                logging.info(f"Schedule {schedule_id}: Kauf von {asset} übersprungen – {skip_reason}")
                continue

            estimated_coins = float(amount_eur) / current_price if current_price else 0.0
            logging.info(
                f"Starte Kauf: {amount_eur} EUR => {asset} (Schedule {schedule_id}), "
                f"Kurs ~ {current_price:.2f} EUR, erwartet ~ {estimated_coins:.6f} {asset}"
            )

            # Order platzieren
            if SIMULATION_MODE:
                response = place_mock_order(asset, amount_eur)
            else:
                order_body = {"amountQuote": str(amount_eur)}
                response = bitvavo_request_with_retry(
                    bv.placeOrder, market_symbol, "buy", "market", order_body
                )

            # Erfolg?
            if "orderId" in response:
                filled_asset = 0.0
                total_cost = 0.0
                if "fills" in response:
                    for f in response["fills"]:
                        amt = float(f["amount"])
                        prc = float(f["price"])
                        filled_asset += amt
                        total_cost += amt * prc
                avg_price = total_cost / filled_asset if filled_asset else 0.0

                # In Datenbank speichern
                with get_connection() as conn2:
                    c2 = conn2.cursor()
                    c2.execute("""
                        INSERT INTO trades (
                            timestamp, asset, amount_eur,
                            filled_asset, avg_price, order_id
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    """, (
                        datetime.datetime.now(),
                        asset.upper(),
                        amount_eur,
                        filled_asset,
                        avg_price,
                        response["orderId"]
                    ))
                    conn2.commit()

                logging.info(
                    f"Kauf erfolgreich (Schedule {schedule_id}): "
                    f"{filled_asset:.6f} {asset} @ ~{avg_price:.4f} EUR. "
                    f"OrderId={response['orderId']}"
                )

                # E-Mail bei Erfolg
                if email_config and email_config["send_on_success"]:
                    subject = f"Erfolgreicher Kauf: {asset}"
                    body = (
                        f"Schedule-ID: {schedule_id}\n"
                        f"Asset: {asset}\n"
                        f"EUR: {amount_eur}\n"
                        f"Erhaltene Menge: {filled_asset:.6f}\n"
                        f"Durchschnittspreis: {avg_price:.4f}\n"
                        f"OrderId: {response['orderId']}\n"
                        f"Zeitpunkt: {datetime.datetime.now()}\n"
                    )
                    send_email(subject, body)

            else:
                logging.error(f"Order fehlgeschlagen: {response}")
                if email_config and email_config["send_on_error"]:
                    subject = f"Fehler beim Kauf: {asset}"
                    body = f"Die Order ist fehlgeschlagen: {str(response)}"
                    send_email(subject, body)

        except Exception as e:
            logging.error(f"Fehler beim Kauf von {asset}: {str(e)}")

            # E-Mail bei Exception
            if email_config and email_config["send_on_error"]:
                subject = f"Exception beim Kauf: {asset}"
                body = (
                    f"Schedule-ID: {schedule_id}\n"
                    f"Asset: {asset}\n"
                    f"EUR: {amount_eur}\n"
                    f"Fehlermeldung: {str(e)}\n"
                )
                send_email(subject, body)


def run_scheduler():
    """Scheduler-Hauptschleife: arbeitet fällige Jobs ab (läuft in eigenem Thread)."""
    logging.info("Scheduler-Thread gestartet.")
    while True:
        try:
            schedule.run_pending()
        except Exception as e:
            logging.error(f"Scheduler: Fehler beim Ausführen der Jobs: {e}")
        time.sleep(1)


SCHEDULER_THREAD = threading.Thread(target=run_scheduler, daemon=True, name="scheduler")
SCHEDULER_THREAD.start()


def scheduler_watchdog():
    """
    Watchdog: prüft alle 60 s, ob der Scheduler-Thread lebt, und startet ihn
    bei Absturz neu (mit deutlicher deutscher Fehlermeldung im Log).
    """
    while True:
        time.sleep(60)
        global SCHEDULER_THREAD
        if SCHEDULER_THREAD.is_alive():
            continue
        logging.error(
            "WATCHDOG: Scheduler-Thread ist abgestürzt! Starte ihn automatisch neu. "
            "Geplante Käufe wurden während des Ausfalls NICHT ausgeführt – "
            "bitte das Log auf die Ursache prüfen."
        )
        try:
            load_schedules_into_scheduler()
        except Exception as e:
            logging.error(f"WATCHDOG: Zeitpläne konnten nicht neu geladen werden: {e}")
        SCHEDULER_THREAD = threading.Thread(target=run_scheduler, daemon=True, name="scheduler")
        SCHEDULER_THREAD.start()
        logging.info("WATCHDOG: Scheduler-Thread erfolgreich neu gestartet.")


threading.Thread(target=scheduler_watchdog, daemon=True, name="scheduler-watchdog").start()


########################################
# 10) Historische Preise aktualisieren
########################################
def update_prices_for_assets():
    """
    Sammelt alle Assets aus schedule_lines und trades,
    ruft den aktuellen Preis ab und speichert ihn in historical_rates.
    """
    logging.info("Starte update_prices_for_assets() ...")
    if SIMULATION_MODE:
        logging.info("SIMULATION_MODE aktiv: Keine echten Preisupdates.")
        return

    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT DISTINCT asset FROM schedule_lines")
        assets_lines = [r[0] for r in c.fetchall()]

        c.execute("SELECT DISTINCT asset FROM trades")
        assets_trades = [r[0] for r in c.fetchall()]

        all_assets = set(assets_lines + assets_trades)
        date_str = datetime.datetime.now().strftime('%Y-%m-%d')

    with get_connection() as conn:
        c = conn.cursor()
        for asset in all_assets:
            if not asset:
                continue
            try:
                price_eur = get_current_price(asset) or 0.0

                c.execute("""
                    INSERT INTO historical_rates (date, asset, price_eur)
                    VALUES (?, ?, ?)
                """, (date_str, asset.upper(), price_eur))

                logging.info(f"Preis gespeichert: {asset} = {price_eur} EUR am {date_str}")
            except Exception as e2:
                logging.warning(f"Preis für {asset} konnte nicht geholt werden: {str(e2)}")

        conn.commit()


########################################
# 11) Routen: Startseite & Co.
########################################
@app.route("/")
def index():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT id, weekday, time_of_day FROM schedules ORDER BY id")
        scheds = c.fetchall()

        schedules_list = []
        for (sid, wd, tod) in scheds:
            c.execute("SELECT asset, amount_eur FROM schedule_lines WHERE schedule_id=?", (sid,))
            lines_ = c.fetchall()
            c.execute("SELECT rule_max_price, rule_ath_dip_pct, rule_sma, rule_rsi_min, "
                      "rule_rsi_max, rule_max_invest FROM schedules WHERE id=?", (sid,))
            r = c.fetchone()
            rules = {
                "max_price": r[0], "ath_dip_pct": r[1], "sma": r[2] or "off",
                "rsi_min": r[3], "rsi_max": r[4], "max_invest": r[5],
            }
            schedules_list.append({"id": sid, "weekday": wd, "time": tod,
                                   "lines": lines_, "rules": rules})

        c.execute("SELECT api_key, api_secret FROM credentials ORDER BY id DESC LIMIT 1")
        cred = c.fetchone()
        has_credentials = bool(cred and cred[0])
        api_secret_missing = bool(cred and cred[0] and not cred[1])

        c.execute("SELECT COUNT(*) FROM trades")
        trade_count = c.fetchone()[0]

        # Assets für die Indikator-Tabelle sammeln
        assets = set()
        c.execute("SELECT DISTINCT asset FROM schedule_lines")
        assets |= {r[0] for r in c.fetchall()}
        c.execute("SELECT DISTINCT asset FROM trades")
        assets |= {r[0] for r in c.fetchall()}
        c.execute("SELECT DISTINCT currency FROM balances WHERE currency != 'EUR'")
        assets |= {r[0] for r in c.fetchall()}

    indicators = []
    for asset in sorted(assets):
        if not asset:
            continue
        try:
            data = compute_indicators(asset)
            if data:
                data["asset"] = asset
                indicators.append(data)
        except Exception:
            pass  # Preis nicht verfügbar -> Zeile weglassen

    onboarding = get_onboarding()
    onboarding_current = next((i + 1 for i, s in enumerate(onboarding) if not s["done"]), None)
    return render_template(
        "index.html",
        schedules_list=schedules_list,
        has_credentials=has_credentials,
        api_secret_missing=api_secret_missing,
        simulation_mode=SIMULATION_MODE,
        indicators=indicators,
        has_trades=trade_count > 0,
        onboarding=onboarding,
        onboarding_current=onboarding_current
    )


@app.route("/api/coin_prices")
def api_coin_prices():
    """
    Positionen (FIFO-Lots + Live-Kurse + 24h-Änderung) und verfügbares
    EUR-Guthaben als JSON – für das Dashboard.
    """
    tick_map = {}
    for t in get_all_assets():
        tick_map[t["symbol"]] = t

    lots, _ = build_lots()
    by_asset = {}
    for lot in lots:
        by_asset.setdefault(lot["asset"], []).append(lot)

    rows = []
    for asset, ls in by_asset.items():
        t = tick_map.get(asset)
        price = (t["price"] if t and t["price"] else None) or get_current_price(asset) or 0.0
        qty = sum(l["qty"] for l in ls)
        basis = sum(l["qty"] * l["price"] for l in ls)
        value = qty * price
        unrealized = value - basis
        rows.append({
            "symbol": asset,
            "name": _coin_display_name(asset, (t["name"] if t else "")) or asset,
            "qty": qty,
            "price": price,
            "value": value,
            "change": (t["change"] if t else 0.0) or 0.0,
            "unrealized": unrealized,
            "unrealized_pct": (unrealized / basis * 100.0) if basis else None,
        })
    rows.sort(key=lambda r: r["value"], reverse=True)

    eur_balance = 0.0
    if not SIMULATION_MODE:
        try:
            bv = get_bitvavo_client()
            res = bitvavo_request_with_retry(bv.balance, {})
            if isinstance(res, list):
                for b in res:
                    if b.get("symbol") == "EUR":
                        eur_balance = float(b.get("available") or 0.0)
        except Exception as e:
            logging.warning(f"api_coin_prices: EUR-Guthaben nicht verfügbar: {e}")

    return jsonify({"positions": rows, "eur_balance": eur_balance})


DAY_PRICE_SERIES_CACHE = {}


def _period_bounds(period):
    """Start-Datetime für einen Chart-Zeitraum (gemeinsam für BTC- und Portfolio-Modus)."""
    now = datetime.datetime.now()
    day = timedelta(days=1)
    if period == "1d":
        return now - day
    if period == "7d":
        return now - 7 * day
    if period == "1y":
        return now - 365 * day
    if period == "all":
        start = now - 8 * 365 * day
        try:
            with get_connection() as conn:
                c = conn.cursor()
                c.execute("SELECT MIN(timestamp) FROM trades")
                first_ts = c.fetchone()[0]
            if first_ts:
                d = parse_db_time(first_ts)
                if d:
                    start = min(start, d - 3 * day)
        except Exception:
            pass
        return start
    return now - 30 * day


def _day_prices_for_asset(asset, start_date, end_date):
    """
    Tages-Schlusskurse {date: close} eines Coins für einen Zeitraum über die
    öffentliche 1d-Candles-API (gecacht). Bei sehr langen Zeiträumen wird in
    Fenstern von max. 1000 Tagen abgefragt.
    """
    key = (asset, start_date.strftime("%Y-%m-%d"), end_date.strftime("%Y-%m-%d"))
    cached = DAY_PRICE_SERIES_CACHE.get(key)
    if cached and time.time() - cached[0] < HIST_CACHE_TTL:
        return cached[1]
    out = {}
    cur = datetime.datetime(start_date.year, start_date.month, start_date.day)
    end_dt = datetime.datetime(end_date.year, end_date.month, end_date.day)
    while cur <= end_dt:
        win_end = min(cur + timedelta(days=999), end_dt)
        try:
            res = get_public_client().candles(
                f"{asset}-EUR", "1d", start=cur, end=win_end, limit=1000)
        except Exception:
            res = None
        if isinstance(res, list):
            for c in res:
                try:
                    d = datetime.datetime.fromtimestamp(int(c[0]) / 1000.0).date()
                    out[d] = float(c[4])
                except (TypeError, ValueError, IndexError):
                    continue
        cur = win_end + timedelta(days=1)
    DAY_PRICE_SERIES_CACHE[key] = (time.time(), out)
    return out


def portfolio_value_series(start, end):
    """
    Gesamtwert des Portfolios (alle Positionen) pro Tag im Zeitraum [start, end].
    Replayt die FIFO-Lots aus Trades + Transfers Tag für Tag und bewertet die
    Bestände mit den historischen Tages-Schlusskursen (Bitvavo 1d-Candles).
    Rückgabe: [{label, value}, ...] chronologisch.
    """
    events = _fifo_rows()
    if not events:
        return []
    assets = sorted({e["asset"] for e in events})
    start_date = start.date()
    end_date = end.date()
    prices = {}
    for a in assets:
        prices[a] = _day_prices_for_asset(a, start_date, end_date)

    by_date = {}
    for e in events:
        by_date.setdefault(e["date"].date(), []).append(e)

    lots = {}  # asset -> [ [date, qty], ... ] FIFO-Liste

    def _apply(ev):
        ls = lots.setdefault(ev["asset"], [])
        if ev["side"] == "sell":
            remaining = ev["qty"]
            while remaining > 1e-12 and ls:
                lot = ls[0]
                take = min(remaining, lot[1])
                lot[1] -= take
                remaining -= take
                if lot[1] <= 1e-12:
                    ls.pop(0)
        elif ev["side"] == "withdraw":
            remaining = ev["qty"]
            while remaining > 1e-12 and ls:
                lot = ls[0]
                take = min(remaining, lot[1])
                lot[1] -= take
                remaining -= take
                if lot[1] <= 1e-12:
                    ls.pop(0)
        else:
            ls.append([ev["date"], ev["qty"]])

    # Bestände VOR dem Zeitraum aufbauen, damit der Chart mit dem echten
    # Bestand startet (nicht bei 0).
    for e in events:
        if e["date"].date() < start_date:
            _apply(e)

    out = []
    last_prices = {}  # letzter bekannter Kurs je Asset (Tageskerzen hinken 1 Tag nach)
    d = start_date
    while d <= end_date:
        for ev in by_date.get(d, []):
            _apply(ev)
        total = 0.0
        for a, ls in lots.items():
            qty = sum(l[1] for l in ls)
            if qty <= 1e-12:
                continue
            p = prices.get(a, {}).get(d)
            if p:
                last_prices[a] = p
            elif d == end_date:
                # Heute: Tageskerze noch nicht verfügbar -> Live-Kurs verwenden
                live = get_current_price(a)
                if live:
                    last_prices[a] = live
            p = last_prices.get(a)
            if p:
                total += qty * p
        out.append({"label": d.strftime("%d.%m.%y"), "value": total})
        d += timedelta(days=1)
    return out


@app.route("/api/portfolio_data")
def api_portfolio_data():
    """
    Kursverlauf für die Dashboard-Chart-Zeiträume (1T/7T/30T/1J/ALLE).
    mode=btc (Standard): BTC-EUR-Kerzen. mode=portfolio: Gesamtwert aller
    Positionen pro Tag (FIFO-Replay + historische Tageskurse).
    Rückgabe: [{label, value}, ...] chronologisch sortiert.
    """
    period = request.args.get("period", "30d")
    mode = request.args.get("mode", "btc")
    now = datetime.datetime.now()

    if mode == "portfolio":
        start = _period_bounds(period)
        series = portfolio_value_series(start, now)
        if series:
            series[-1]["value"] = round(series[-1]["value"], 2)
        return jsonify(series)

    start = _period_bounds(period)
    day = timedelta(days=1)
    if period == "1d":
        interval = "1h"
    elif period == "7d":
        interval = "4h"
    else:
        interval = "1d"

    try:
        res = get_public_client().candles("BTC-EUR", interval, start=start, end=now)
    except Exception as e:
        logging.warning(f"api_portfolio_data: Candles fehlgeschlagen: {e}")
        return jsonify([])
    if isinstance(res, dict) and "errorCode" in res:
        return jsonify([])

    fmt = "%d.%m. %H:%M" if interval in ("1h", "4h") else "%d.%m.%y"
    out = []
    for c in res or []:
        try:
            ts = int(c[0])
            close = float(c[4])
            label = datetime.datetime.fromtimestamp(ts / 1000.0).strftime(fmt)
            out.append({"ts": ts, "label": label, "value": close})
        except (TypeError, ValueError, IndexError):
            continue
    out.sort(key=lambda x: x["ts"])
    for x in out:
        x.pop("ts", None)
    return jsonify(out)


@app.route("/schedule_preview", methods=["POST"])
def schedule_preview():
    """
    Vorschau: simuliert die nächsten 3 Käufe eines noch nicht gespeicherten
    Zeitplans (Termin, Positionen, Regeln) mit aktuellem Kurs und Regel-Check.
    """
    data = request.get_json(silent=True) or {}
    weekday = data.get("weekday", "Monday")
    time_str = data.get("time", "09:00")
    lines = data.get("lines", []) or []
    rules = data.get("rules", {}) or {}

    if not lines:
        return jsonify({"error": "Keine Positionen"})

    week_map = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    if weekday not in week_map:
        return jsonify({"error": "Unbekannter Wochentag"})
    target = week_map.index(weekday)

    # Nächste 3 passende Termine (heute nur, wenn die Uhrzeit noch bevorsteht)
    now = datetime.datetime.now()
    dates = []
    d = now.date()
    for _ in range(90):
        if d.weekday() == target:
            if d > now.date():
                dates.append(d)
            else:
                try:
                    t = datetime.datetime.strptime(time_str, "%H:%M").time()
                    if now.time() < t:
                        dates.append(d)
                except ValueError:
                    pass
            if len(dates) >= 3:
                break
        d += timedelta(days=1)

    de_days = ["Montag", "Dienstag", "Mittwoch", "Donnerstag", "Freitag", "Samstag", "Sonntag"]
    rules_dict = {
        "max_price": _to_float_or_none(str(rules.get("max_price") or "")),
        "ath_dip_pct": _to_float_or_none(str(rules.get("ath_dip_pct") or "")),
        "sma": (rules.get("sma") or "off").strip() or "off",
        "rsi_min": _to_float_or_none(str(rules.get("rsi_min") or "")),
        "rsi_max": _to_float_or_none(str(rules.get("rsi_max") or "")),
        "max_invest": _to_float_or_none(str(rules.get("max_invest") or "")),
    }

    items = []
    for date in dates:
        for line in lines:
            asset = str(line.get("asset", "")).strip().upper()
            if asset.endswith("-EUR"):
                asset = asset[:-4]
            amount = float(line.get("amount_eur") or 0.0)
            if not asset or amount <= 0:
                continue
            price = get_current_price(asset) or 0.0
            rule_reason = check_trade_rules(asset, amount, price, rules_dict)
            items.append({
                "day_name": de_days[date.weekday()],
                "date": date.strftime("%d.%m.%Y"),
                "asset": asset,
                "amount_eur": amount,
                "price": price,
                "est_qty": (amount / price) if price > 0 else 0.0,
                "rule_ok": rule_reason is None,
                "rule_reason": rule_reason or "",
            })
    return jsonify(items)


@app.route("/add_schedule", methods=["GET", "POST"])
def add_schedule():
    if request.method == "POST":
        wd = request.form.get("weekday")
        tod = request.form.get("time_of_day")

        assets = request.form.getlist("asset")
        amounts = request.form.getlist("amount_eur")

        rule_max_price = _to_float_or_none(request.form.get("rule_max_price"))
        rule_ath_dip_pct = _to_float_or_none(request.form.get("rule_ath_dip_pct"))
        rule_sma = (request.form.get("rule_sma") or "off").strip() or "off"
        rule_rsi_min = _to_float_or_none(request.form.get("rule_rsi_min"))
        rule_rsi_max = _to_float_or_none(request.form.get("rule_rsi_max"))
        rule_max_invest = _to_float_or_none(request.form.get("rule_max_invest"))

        with get_connection() as conn:
            c = conn.cursor()
            c.execute("""
                INSERT INTO schedules (
                    weekday, time_of_day, rule_max_price, rule_ath_dip_pct, rule_sma,
                    rule_rsi_min, rule_rsi_max, rule_max_invest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (wd, tod, rule_max_price, rule_ath_dip_pct, rule_sma,
                  rule_rsi_min, rule_rsi_max, rule_max_invest))
            schedule_id = c.lastrowid

            for (ast, amt_str) in zip(assets, amounts):
                ast = ast.strip().upper()
                amt_val = 0.0
                try:
                    amt_val = float(amt_str)
                except (ValueError, TypeError):
                    pass
                if ast and amt_val > 0:
                    c.execute("""
                      INSERT INTO schedule_lines (schedule_id, asset, amount_eur)
                      VALUES (?, ?, ?)
                    """, (schedule_id, ast, amt_val))

            conn.commit()

        load_schedules_into_scheduler()
        logging.info(f"Neuer Zeitplan {schedule_id} angelegt: {wd} {tod}")
        flash("Neuer Zeitplan angelegt.")
        return redirect(url_for("index"))

    now_plus_2 = datetime.datetime.now() + timedelta(minutes=2)
    default_day = now_plus_2.strftime("%A")     # z.B. "Monday"
    default_time = now_plus_2.strftime("%H:%M") # z.B. "23:59"

    # Alle handelbaren Coins (EUR-Märkte) mit Namen + Live-Preis für die Auswahl
    top_assets = get_all_assets() or get_top_assets(10)
    allowed = [t["symbol"] for t in top_assets] or list(ALLOWED_ASSETS)

    return render_template(
        "add_schedule.html",
        allowed_assets=allowed,
        top_assets=top_assets,
        default_day=default_day,
        default_time=default_time
    )


@app.route("/edit_schedule/<int:schedule_id>", methods=["GET", "POST"])
def edit_schedule(schedule_id):
    if request.method == "POST":
        wd = request.form.get("weekday")
        tod = request.form.get("time_of_day")

        assets = request.form.getlist("asset")
        amounts = request.form.getlist("amount_eur")

        rule_max_price = _to_float_or_none(request.form.get("rule_max_price"))
        rule_ath_dip_pct = _to_float_or_none(request.form.get("rule_ath_dip_pct"))
        rule_sma = (request.form.get("rule_sma") or "off").strip() or "off"
        rule_rsi_min = _to_float_or_none(request.form.get("rule_rsi_min"))
        rule_rsi_max = _to_float_or_none(request.form.get("rule_rsi_max"))
        rule_max_invest = _to_float_or_none(request.form.get("rule_max_invest"))

        with get_connection() as conn:
            c = conn.cursor()
            c.execute("""
                UPDATE schedules SET
                    weekday=?, time_of_day=?, rule_max_price=?, rule_ath_dip_pct=?,
                    rule_sma=?, rule_rsi_min=?, rule_rsi_max=?, rule_max_invest=?
                WHERE id=?
            """, (wd, tod, rule_max_price, rule_ath_dip_pct, rule_sma,
                  rule_rsi_min, rule_rsi_max, rule_max_invest, schedule_id))
            c.execute("DELETE FROM schedule_lines WHERE schedule_id=?", (schedule_id,))

            for (ast, amt_str) in zip(assets, amounts):
                ast = ast.strip().upper()
                amt_val = 0.0
                try:
                    amt_val = float(amt_str)
                except (ValueError, TypeError):
                    pass
                if ast and amt_val > 0:
                    c.execute("""
                      INSERT INTO schedule_lines (schedule_id, asset, amount_eur)
                      VALUES (?, ?, ?)
                    """, (schedule_id, ast, amt_val))
            conn.commit()

        load_schedules_into_scheduler()
        logging.info(f"Zeitplan {schedule_id} aktualisiert: {wd} {tod}")
        flash(f"Zeitplan {schedule_id} wurde aktualisiert.")
        return redirect(url_for("index"))

    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT weekday, time_of_day, rule_max_price, rule_ath_dip_pct, rule_sma, "
                  "rule_rsi_min, rule_rsi_max, rule_max_invest FROM schedules WHERE id=?", (schedule_id,))
        row = c.fetchone()
        if not row:
            flash(f"Zeitplan {schedule_id} existiert nicht.")
            return redirect(url_for("index"))
        wd, tod = row[0], row[1]
        rules = {
            "max_price": row[2], "ath_dip_pct": row[3], "sma": row[4] or "off",
            "rsi_min": row[5], "rsi_max": row[6], "max_invest": row[7],
        }

        c.execute("SELECT asset, amount_eur FROM schedule_lines WHERE schedule_id=?", (schedule_id,))
        lines = c.fetchall()

    while len(lines) < 3:
        lines.append(("", 0.0))

    # Alle handelbaren Coins (EUR-Märkte) mit Namen + Live-Preis für die Auswahl
    top_assets = get_all_assets() or get_top_assets(10)
    allowed = [t["symbol"] for t in top_assets] or list(ALLOWED_ASSETS)

    return render_template(
        "edit_schedule.html",
        schedule_id=schedule_id,
        wd=wd,
        tod=tod,
        lines=lines,
        allowed_assets=allowed,
        top_assets=top_assets,
        rules=rules
    )


@app.route("/delete_schedule/<int:schedule_id>")
def delete_schedule(schedule_id):
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM schedule_lines WHERE schedule_id = ?", (schedule_id,))
        c.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
        conn.commit()

    load_schedules_into_scheduler()
    flash(f"Zeitplan {schedule_id} gelöscht.")
    logging.info(f"Zeitplan {schedule_id} gelöscht.")
    return redirect(url_for("index"))


@app.route("/balance")
def manual_balance():
    if SIMULATION_MODE:
        flash("SIMULATION_MODE aktiv: Kein echter Kontostand.", "info")
        return redirect(url_for("index"))

    try:
        bv = get_bitvavo_client()
    except Exception as e:
        flash(f"Keine Credentials oder Fehler: {str(e)}", "error")
        logging.error(f"manual_balance: Kein Client. {str(e)}")
        return redirect(url_for("index"))

    # Letzter Snapshot
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("""
          SELECT timestamp, currency, amount
          FROM balances
          ORDER BY id DESC LIMIT 50
        """)
        old_rows = c.fetchall()

        old_balance = {}
        last_ts = None
        if old_rows:
            last_ts = old_rows[0][0]
            old_balance_ts_rows = [r for r in old_rows if r[0] == last_ts]
            for (_, currency, amount) in old_balance_ts_rows:
                old_balance[currency] = amount

    # Aktueller Kontostand
    try:
        res = bitvavo_request_with_retry(bv.balance, {})
        if isinstance(res, dict) and "errorCode" in res:
            flash(f"Fehler: {res['errorCode']} - {res['error']}", "error")
            logging.error(f"Bitvavo balance error: {res}")
            return redirect(url_for("index"))

        now_str = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        filtered = [b for b in res if float(b['available']) > 0]

        with get_connection() as conn:
            c = conn.cursor()
            for b in filtered:
                currency = b["symbol"]
                amount   = float(b["available"])
                c.execute("""
                  INSERT INTO balances (timestamp, currency, amount)
                  VALUES (?, ?, ?)
                """, (now_str, currency, amount))
            conn.commit()

        current_balance = {b["symbol"]: float(b["available"]) for b in filtered}

        flash(f"Kontostand abgerufen und gespeichert ({now_str}).")
        logging.info(f"Kontostand abgerufen: {len(filtered)} Einträge gespeichert.")

        return render_template(
            "balance.html",
            now_str=now_str,
            current_balance=current_balance,
            last_ts=last_ts,
            old_balance=old_balance
        )

    except Exception as e:
        flash(f"Fehler beim Kontostand: {e}", "error")
        logging.error(f"Fehler in manual_balance(): {str(e)}")
        return redirect(url_for("index"))


@app.route("/trades")
def trades_list():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("""
            SELECT timestamp, asset, amount_eur, filled_asset, avg_price, order_id, side
            FROM trades
            ORDER BY id DESC
        """)
        rows = c.fetchall()
        c.execute("""
            SELECT timestamp, asset, amount, kind, tx_id, price_eur
            FROM transfers
            ORDER BY id DESC
            LIMIT 200
        """)
        transfers = c.fetchall()

    return render_template("trades.html", rows=rows, transfers=transfers)


@app.route("/import_trades")
def import_trades():
    """
    Importiert die eigene Bitvavo-Trade-Historie (alle EUR-Märkte) in die DB.
    Dedupliziert anhand der Trade-ID; Paginierung über tradeIdFrom.
    """
    # days=all|full|0  -> komplette Historie seit Kontoerstellung (kein Startzeitpunkt)
    raw_days = request.args.get("days", "365").strip().lower()
    full_history = raw_days in ("all", "full", "0", "")
    try:
        days = max(1, min(3650, int(raw_days))) if not full_history else 365
    except Exception:
        days = 365

    if SIMULATION_MODE:
        flash(f"SIMULATION_MODE aktiv: Kein Import möglich.", "info")
        logging.info("import_trades: im Simulationsmodus übersprungen.")
        return redirect(url_for("trades_list"))

    try:
        bv = get_bitvavo_client()
    except Exception as e:
        flash(f"Import nicht möglich: {str(e)}", "error")
        logging.error(f"import_trades: Kein Client. {str(e)}")
        return redirect(url_for("trades_list"))

    # Häufigster Grund für Fehlschläge: Key ohne Secret -> Bitvavo-Fehler 309
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT api_key, api_secret FROM credentials ORDER BY id DESC LIMIT 1")
        _cred = c.fetchone()
    if _cred and _cred[0] and not _cred[1]:
        flash("Import nicht möglich: Es fehlt das API-Secret! Bitvavo zeigt das Secret nur EINMAL "
              "bei der Erstellung an – lege in der Bitvavo-App einen neuen API-Key an und "
              "trage Key UND Secret in den Einstellungen ein.", "error")
        logging.error("import_trades: API-Secret ist leer (Fehler 309 beim Signieren).")
        return redirect(url_for("trades_list"))

    try:
        market_list = bitvavo_request_with_retry(bv.markets, {})
    except Exception as e:
        flash(f"Marktliste konnte nicht geladen werden: {str(e)}", "error")
        logging.error(f"import_trades: markets fehlgeschlagen: {e}")
        return redirect(url_for("trades_list"))

    if isinstance(market_list, dict) and "errorCode" in market_list:
        flash(f"Bitvavo-Fehler: {market_list['errorCode']} {market_list.get('error', '')}", "error")
        logging.error(f"import_trades: markets-Fehler: {market_list}")
        return redirect(url_for("trades_list"))

    eur_markets = sorted(
        m["market"] for m in market_list
        if isinstance(m, dict) and m.get("market", "").endswith("-EUR")
    )

    period_desc = "komplette Historie (seit Kontoerstellung)" if full_history \
        else f"letzte {days} Tage"
    start_ms = None if full_history else int(
        (datetime.datetime.now() - timedelta(days=days)).timestamp() * 1000)
    imported = 0
    backfilled = 0
    checked = 0
    errors = []

    logging.info(f"import_trades: starte Import der {period_desc} für {len(eur_markets)} EUR-Märkte ...")

    def _wait_for_rate_limit(bv):
        """Wartet, bis das Rate-Limit der SDK wieder Spielraum hat."""
        remaining = getattr(bv, "rateLimitRemaining", 1000)
        reset = getattr(bv, "rateLimitReset", None)
        if remaining and remaining < 30 and reset:
            wait = (reset / 1000.0) - time.time()
            if 0 < wait < 300:
                logging.info(f"import_trades: Rate-Limit fast erschöpft – warte {wait:.0f}s")
                time.sleep(wait)

    # Altbestand bereinigen, bevor die Dedupe-Logik greift (Vorher: Duplikate je Importlauf)
    removed_dupes = dedupe_trades()
    if removed_dupes:
        flash(f"Beim Import-Start wurden {removed_dupes} doppelte Trade-Zeilen entfernt "
              f"(Altbestand ohne Trade-ID aus früheren Imports).", "info")

    fatal = None
    rate_limited = False
    with get_connection() as conn:
        cur = conn.cursor()
        for market in eur_markets:
            asset = market.replace("-EUR", "")
            try:
                trade_id_from = None
                while True:
                    opts = {"limit": 1000}
                    if not full_history and start_ms is not None:
                        opts["start"] = start_ms
                    if trade_id_from:
                        opts["tradeIdFrom"] = trade_id_from
                    _wait_for_rate_limit(bv)
                    page = bitvavo_request_with_retry(bv.trades, market, opts)

                    if isinstance(page, dict) and "errorCode" in page:
                        err = f"{market}: {page['errorCode']} {page.get('error', '')}"
                        errors.append(err)
                        if page["errorCode"] == 105:
                            rate_limited = True
                            fatal = err
                            break
                        if page["errorCode"] in (309, 401, 403):
                            # Auth-/Berechtigungsfehler betrifft alle Märkte -> sofort abbrechen
                            fatal = err
                        break
                    if not page:
                        break

                    for t in page:
                        checked += 1
                        ts_ms = t.get("timestamp", 0)
                        ts_str = (datetime.datetime.fromtimestamp(ts_ms / 1000)
                                  .strftime("%Y-%m-%d %H:%M:%S")) if ts_ms else None
                        # Bitvavo liefert die Fill-ID im Feld "id" (NICHT "tradeId").
                        # Sie ist der Schlüssel für Deduplikation UND Pagination.
                        tid = t.get("id") or t.get("tradeId")
                        side = (t.get("side") or "buy").lower()
                        amount_eur = float(t.get("amountQuote", 0.0) or 0.0)
                        filled = float(t.get("amount", 0.0) or 0.0)
                        price = float(t.get("price", 0.0) or 0.0)
                        fee = float(t.get("fee", 0.0) or 0.0)
                        fee_cur = t.get("feeCurrency")

                        # 1) Backfill: bereits vorhandene Zeile (frühere Imports ohne id)
                        #    anhand order_id+Zeitpunkt+Menge erkennen und trade_id nachtragen.
                        if tid:
                            cur.execute("""
                                UPDATE trades SET trade_id=?, side=?, fee=?, fee_currency=?
                                WHERE asset=? AND order_id=? AND timestamp=? AND side=?
                                  AND trade_id IS NULL
                                  AND ROUND(filled_asset, 10) = ROUND(?, 10)
                            """, (tid, side, fee, fee_cur, asset, t.get("orderId"),
                                  ts_str, side, filled))
                            if cur.rowcount:
                                backfilled += 1

                        # 2) Neu einfügen – durch trade_id dedupliziert (INSERT OR IGNORE
                        #    hatte vorher keine Wirkung, weil trade_id immer NULL war).
                        if not tid or not cur.rowcount:
                            cur.execute("""
                                INSERT OR IGNORE INTO trades (
                                    timestamp, asset, amount_eur, filled_asset, avg_price,
                                    order_id, side, trade_id, fee, fee_currency
                                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (
                                ts_str,
                                asset,
                                amount_eur,
                                filled,
                                price,
                                t.get("orderId"),
                                side,
                                tid,
                                fee,
                                fee_cur,
                            ))
                            if cur.rowcount:
                                imported += 1
                    conn.commit()

                    if len(page) < 1000:
                        break
                    # Nächste Seite = ältere Trades: Cursor auf die älteste Fill-ID der Seite
                    trade_id_from = page[-1].get("id") or page[-1].get("tradeId")
                    time.sleep(0.2)
            except Exception as e:
                errors.append(f"{market}: {str(e)}")
            if fatal:
                break
            time.sleep(0.35)  # Rate-Limit von Bitvavo schonen (1000 Gewichtspunkte/Minute)

    if rate_limited:
        msg = (f"Import pausiert: Bitvavo-Rate-Limit erreicht ({fatal}). "
               f"Warte einige Minuten und klicke erneut auf Importieren – bereits "
               f"gespeicherte Trades werden nicht dupliziert.")
        logging.error(f"import_trades: Rate-Limit – {fatal}")
    elif fatal:
        msg = (f"Import abgebrochen: Authentifizierungsfehler ({fatal}). "
               f"Wahrscheinlich fehlt oder falsch ist das API-Secret – neu anlegen und "
               f"Key UND Secret in den Einstellungen eintragen.")
        logging.error(f"import_trades: abgebrochen – {fatal}")
    else:
        msg = (f"Import abgeschlossen: {imported} neue Trades importiert, "
               f"{backfilled} bestehende nachträglich mit Trade-ID ergänzt, "
               f"{checked} Einträge geprüft, {len(eur_markets)} Märkte durchsucht ({period_desc}).")
        if errors:
            msg += f" Fehler bei {len(errors)} Märkten: " + " | ".join(errors[:3])
            if len(errors) > 3:
                msg += " …"
            for e in errors:
                logging.warning(f"import_trades: {e}")
        logging.info(msg)
    flash(msg)
    return redirect(url_for("trades_list"))


@app.route("/import_transfers")
def import_transfers():
    """
    Importiert Ein-/Auszahlungen (depositHistory/withdrawalHistory) aller Coins.
    Einzahlungen werden als Kauf-Lots zum jeweiligen Tageskurs verbucht (FIFO-Basis),
    Auszahlungen reduzieren nur den Bestand. Dedupliziert über die Bitvavo-Tx-ID.
    Zeitraum: days=365 usw. oder days=all (komplette Historie seit Kontoerstellung).
    """
    raw_days = request.args.get("days", "365").strip().lower()
    full_history = raw_days in ("all", "full", "0", "")
    try:
        days = max(1, min(3650, int(raw_days))) if not full_history else 365
    except Exception:
        days = 365

    if SIMULATION_MODE:
        flash("SIMULATION_MODE aktiv: Kein Import möglich.", "info")
        return redirect(url_for("trades_list"))

    try:
        bv = get_bitvavo_client()
    except Exception as e:
        flash(f"Import nicht möglich: {str(e)}", "error")
        return redirect(url_for("trades_list"))

    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT api_key, api_secret FROM credentials ORDER BY id DESC LIMIT 1")
        _cred = c.fetchone()
    if _cred and _cred[0] and not _cred[1]:
        flash("Import nicht möglich: Es fehlt das API-Secret (Fehler 309) – bitte Key UND "
              "Secret in den Einstellungen eintragen.", "error")
        return redirect(url_for("trades_list"))

    period_desc = "komplette Historie (seit Kontoerstellung)" if full_history \
        else f"letzte {days} Tage"
    start_ms = None if full_history else int(
        (datetime.datetime.now() - timedelta(days=days)).timestamp() * 1000)

    TRANSFER_OK_STATUSES = {"completed", "finished", "done", "success", "confirmed", "processing"}

    def _wait_for_rate_limit():
        remaining = getattr(bv, "rateLimitRemaining", 1000)
        reset = getattr(bv, "rateLimitReset", None)
        if remaining and remaining < 30 and reset:
            wait = (reset / 1000.0) - time.time()
            if 0 < wait < 300:
                logging.info(f"import_transfers: Rate-Limit fast erschöpft – warte {wait:.0f}s")
                time.sleep(wait)

    def _fetch_hist(func, kind):
        """Holt eine Historie-Seite um die andere (rückwärts) und speichert Zeilen."""
        nonlocal_ok = {"imported": 0, "skipped": 0, "eur": 0, "no_price": 0}
        cursor = start_ms
        page_no = 0
        while True:
            opts = {"limit": 1000}
            if cursor is not None:
                opts["start"] = cursor
            _wait_for_rate_limit()
            page = bitvavo_request_with_retry(func, opts)
            if isinstance(page, dict) and "errorCode" in page:
                if page["errorCode"] == 105:
                    raise Exception(f"Rate-Limit (Fehler 105): {page.get('error', '')}")
                if page["errorCode"] in (309, 401, 403):
                    raise Exception(f"Auth-Fehler {page['errorCode']}: {page.get('error', '')}")
                raise Exception(f"Bitvavo-Fehler {page['errorCode']}: {page.get('error', '')}")
            if not page:
                break
            page_no += 1
            oldest = None
            for t in page:
                if not isinstance(t, dict):
                    continue
                status = str(t.get("status") or "").lower()
                if status and status not in TRANSFER_OK_STATUSES:
                    nonlocal_ok["skipped"] += 1
                    continue
                asset = (t.get("symbol") or t.get("coin") or t.get("asset") or "").upper()
                if asset == "EUR":
                    # SEPA-Ein-/Auszahlungen sind Fiat – für Krypto-Steuern irrelevant
                    nonlocal_ok["eur"] += 1
                    continue
                if not asset:
                    nonlocal_ok["skipped"] += 1
                    continue
                try:
                    amount = float(t.get("amount") or 0.0)
                except (TypeError, ValueError):
                    amount = 0.0
                if amount <= 0:
                    nonlocal_ok["skipped"] += 1
                    continue
                ts_ms = t.get("created") or t.get("timestamp") or 0
                if not ts_ms:
                    # Ohne Zeitstempel lässt sich kein Lot zuordnen
                    nonlocal_ok["skipped"] += 1
                    continue
                ts_str = datetime.datetime.fromtimestamp(int(ts_ms) / 1000)\
                    .strftime("%Y-%m-%d %H:%M:%S")
                tx_id = t.get("txId") or t.get("txID") or t.get("transactionId") \
                    or t.get("hash") or t.get("id") or t.get("depositId") or t.get("withdrawalId")
                fee = float(t.get("fee") or 0.0) if t.get("fee") is not None else 0.0
                fee_cur = t.get("feeCurrency") or (asset if fee else None)
                if oldest is None or (ts_ms and int(ts_ms) < oldest):
                    oldest = int(ts_ms) if ts_ms else 0

                price = None
                if kind == "deposit" and ts_str:
                    d = parse_db_time(ts_str)
                    if d:
                        price = get_historical_day_price(asset, d.date())
                        if price is None:
                            nonlocal_ok["no_price"] += 1

                with get_connection() as conn:
                    cur = conn.cursor()
                    # Bestehende Zeile ohne Preis nachziehen (z. B. spätere Imports)
                    cur.execute("UPDATE transfers SET price_eur=? WHERE asset=? AND kind=? "
                                "AND tx_id=? AND price_eur IS NULL", (price, asset, kind, tx_id))
                    if not cur.rowcount:
                        cur.execute("""
                            INSERT OR IGNORE INTO transfers (
                                timestamp, asset, amount, kind, tx_id, fee, fee_currency, price_eur
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (ts_str, asset, amount, kind, tx_id, fee, fee_cur, price))
                        if cur.rowcount:
                            nonlocal_ok["imported"] += 1
                    conn.commit()
            if len(page) < 1000 or not oldest:
                break
            cursor = oldest + 1
            time.sleep(0.25)
        return nonlocal_ok

    summary = {}
    errors = []
    try:
        dedupe_transfers()
        summary["deposit"] = _fetch_hist(bv.depositHistory, "deposit")
        summary["withdrawal"] = _fetch_hist(bv.withdrawalHistory, "withdrawal")
    except Exception as e:
        logging.error(f"import_transfers: abgebrochen – {e}")
        errors.append(str(e))

    d = summary.get("deposit", {})
    w = summary.get("withdrawal", {})
    msg = (f"Transfers-Import ({period_desc}) abgeschlossen: "
           f"{d.get('imported', 0)} Einzahlungen, {w.get('imported', 0)} Auszahlungen neu "
           f"gespeichert. Übersprungen (unfertig/ohne Datum): "
           f"{d.get('skipped', 0) + w.get('skipped', 0)} | SEPA-EUR (ignoriert): "
           f"{d.get('eur', 0) + w.get('eur', 0)} | ohne Tageskurs: {d.get('no_price', 0)} "
           f"(Basis dort 0 €).")
    if errors:
        msg += " | Fehler: " + " | ".join(errors[:3])
        for e in errors:
            logging.warning(f"import_transfers: {e}")
    logging.info(msg)
    flash(msg)
    return redirect(url_for("trades_list"))


########################################
# 12) Portfolio / Steuer-Dashboard (FIFO)
########################################
def parse_db_time(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def _fifo_rows():
    """
    Baut die chronologische Event-Liste für das FIFO-Replay aus Trades UND Transfers:
      - Trades:    buy / sell (Käufe/Verkäufe)
      - Transfers: Einzahlung wird als Kauf-Lot zum Tageskurs verbucht,
                   Auszahlung reduziert den Bestand OHNE Steuerereignis (kein Verkauf).
    """
    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT timestamp, asset, amount_eur, filled_asset, avg_price, side, fee, fee_currency "
                  "FROM trades ORDER BY timestamp ASC, id ASC")
        trade_rows = c.fetchall()
        c.execute("SELECT timestamp, asset, amount, kind, price_eur "
                  "FROM transfers ORDER BY timestamp ASC, id ASC")
        transfer_rows = c.fetchall()

    out = []
    for (ts, asset, amount_eur, filled_asset, avg_price, side, fee, fee_currency) in trade_rows:
        if not asset:
            continue
        d = parse_db_time(ts)
        if d is None:
            continue
        qty = float(filled_asset or 0.0)
        if qty <= 0 or avg_price is None:
            continue
        out.append({
            "date": d,
            "asset": asset.upper(),
            "side": (side or "buy").lower(),
            "qty": qty,
            "price": float(avg_price),
            "fee_eur": float(fee or 0.0) if fee_currency == "EUR" else 0.0,
        })

    for (ts, asset, amount, kind, price_eur) in transfer_rows:
        if not asset:
            continue
        d = parse_db_time(ts)
        if d is None:
            continue
        qty = float(amount or 0.0)
        if qty <= 0:
            continue
        kind = (kind or "deposit").lower()
        if kind == "deposit":
            # Einzahlung = fiktiver Kauf zum Tageskurs (Basis für spätere Gewinnberechnung)
            out.append({
                "date": d,
                "asset": asset.upper(),
                "side": "buy",
                "qty": qty,
                "price": float(price_eur or 0.0),
                "fee_eur": 0.0,
                "source": "deposit",
            })
        elif kind == "withdrawal":
            # Auszahlung: Bestand reduzieren, aber KEIN realisierter Gewinn/Verlust
            out.append({
                "date": d,
                "asset": asset.upper(),
                "side": "withdraw",
                "qty": qty,
                "price": 0.0,
                "fee_eur": 0.0,
                "source": "withdrawal",
            })
    # stabil sortieren (Trades/Transfers gleichen Datums behalten ihre Reihenfolge)
    out.sort(key=lambda e: e["date"])
    return out


def fifo_replay(rows=None):
    """
    FIFO-Replay der kompletten Trade-Historie – getrennt je Asset
    (ein Verkauf konsumiert das älteste Lot DESSELBEN Assets, nicht ein globales).

    Rückgabe:
      lots_by_asset: {asset: [lot,...]}   Restbestände (lot: date, qty, price inkl. EUR-Gebühr)
      sales:         [piece,...]          jede FIFO-Teilposition eines Verkaufs
      year_holdings: {jahr: {asset: [lot,...]}}  Bestände am 31.12. des jeweiligen Jahres
    """
    events = rows if rows is not None else _fifo_rows()
    lots_by_asset = {}
    sales = []
    year_holdings = {}
    current_year = None

    for ev in events:
        year = ev["date"].year
        if current_year is not None and year != current_year:
            # Jahreswechsel -> Bestände am 31.12. des abgeschlossenen Jahres sichern
            year_holdings[current_year] = {
                a: [dict(l) for l in ls] for a, ls in lots_by_asset.items()}
        current_year = year

        lots = lots_by_asset.setdefault(ev["asset"], [])
        if ev["side"] == "sell":
            remaining = ev["qty"]
            while remaining > 1e-12 and lots:
                lot = lots[0]
                take = min(remaining, lot["qty"])
                fee_share = ev["fee_eur"] * (take / ev["qty"]) if ev["qty"] else 0.0
                held_days = (ev["date"] - lot["date"]).days
                pl = (ev["price"] - lot["price"]) * take - fee_share
                sales.append({
                    "date": ev["date"],
                    "asset": ev["asset"],
                    "qty": take,
                    "sell_price": ev["price"],
                    "gross": ev["price"] * take,
                    "basis": lot["price"] * take,
                    "fee": fee_share,
                    "pl": pl,
                    "held_days": held_days,
                    "taxfree": held_days >= TAX_FREE_DAYS,
                    "buy_date": lot["date"],
                })
                lot["qty"] -= take
                remaining -= take
                if lot["qty"] <= 1e-12:
                    lots.pop(0)
        elif ev["side"] == "withdraw":
            # Auszahlung: FIFO-Lots reduzieren, KEIN Steuerereignis
            remaining = ev["qty"]
            while remaining > 1e-12 and lots:
                lot = lots[0]
                take = min(remaining, lot["qty"])
                lot["qty"] -= take
                remaining -= take
                if lot["qty"] <= 1e-12:
                    lots.pop(0)
        else:
            lots.append({
                "asset": ev["asset"],
                "date": ev["date"],
                "qty": ev["qty"],
                "price": ev["price"] + (ev["fee_eur"] / ev["qty"] if ev["qty"] else 0.0),
            })

    if current_year is not None:
        year_holdings[current_year] = {
            a: [dict(l) for l in ls] for a, ls in lots_by_asset.items()}
    return lots_by_asset, sales, year_holdings


def build_lots():
    """
    Bestände (FIFO-Lots) und realisierte Gewinne je Asset – für die Portfolio-Seite.
    Rückgabe: (lots, realized)
      lots:     [ {asset, date, qty, price(incl. Gebühr)}, ... ]
      realized: { asset: [steuerfreier Gewinn, steuerpflichtiger Gewinn] }
    """
    lots_by_asset, sales, _ = fifo_replay()
    lots = []
    for asset, ls in lots_by_asset.items():
        for lot in ls:
            if lot["qty"] > 1e-12:
                lots.append(dict(lot))
    realized = {}
    for s in sales:
        realized.setdefault(s["asset"], [0.0, 0.0])[0 if s["taxfree"] else 1] += s["pl"]
    return lots, realized


@app.route("/portfolio")
def portfolio():
    """
    Portfolio & Steuer-Dashboard: aktuelle Bestände werden nach FIFO-Lots
    bewertet und in steuerfreie (> 1 Jahr gehalten) und steuerpflichtige
    Bestände aufgeteilt (Annahme: alles würde heute verkauft).
    """
    mark_onboarding_portfolio_seen()
    lots, realized = build_lots()
    assets = sorted({lot["asset"] for lot in lots})

    rows = []
    unavailable = []
    totals = {"value": 0.0, "basis": 0.0, "taxfree_value": 0.0, "taxable_value": 0.0,
              "taxfree_profit": 0.0, "taxable_profit": 0.0}
    realized_total = {"taxfree": 0.0, "taxable": 0.0}

    for asset in assets:
        price = get_current_price(asset)
        if not price:
            unavailable.append(asset)
            continue

        qty = tf_qty = tx_qty = 0.0
        basis = tf_basis = tx_basis = 0.0
        days_left = None
        for lot in lots:
            if lot["asset"] != asset:
                continue
            qty += lot["qty"]
            basis += lot["qty"] * lot["price"]
            held = (datetime.datetime.now() - lot["date"]).days
            if held >= TAX_FREE_DAYS:
                tf_qty += lot["qty"]
                tf_basis += lot["qty"] * lot["price"]
            else:
                tx_qty += lot["qty"]
                tx_basis += lot["qty"] * lot["price"]
                left = TAX_FREE_DAYS - held
                days_left = left if days_left is None else min(days_left, left)

        value = qty * price
        tf_value = tf_qty * price
        tx_value = tx_qty * price
        tf_profit = tf_value - tf_basis
        tx_profit = tx_value - tx_basis
        profit = value - basis

        if tf_qty > 0 and tx_qty == 0:
            status = "steuerfrei"
        elif tx_qty > 0 and tf_qty == 0:
            status = "steuerpflichtig"
        else:
            status = "gemischt"
        if days_left is not None and days_left <= MATURITY_NOTICE_DAYS and status != "steuerfrei":
            status = "bald"

        totals["value"] += value
        totals["basis"] += basis
        totals["taxfree_value"] += tf_value
        totals["taxable_value"] += tx_value
        totals["taxfree_profit"] += tf_profit
        totals["taxable_profit"] += tx_profit

        if asset in realized:
            realized_total["taxfree"] += realized[asset][0]
            realized_total["taxable"] += realized[asset][1]

        rows.append({
            "asset": asset, "qty": qty, "basis": basis, "price": price,
            "value": value, "profit": profit,
            "profit_pct": (profit / basis * 100.0) if basis else 0.0,
            "taxfree_value": tf_value, "taxable_value": tx_value,
            "taxfree_profit": tf_profit, "taxable_profit": tx_profit,
            "status": status, "days_left": days_left,
        })

    rows.sort(key=lambda r: r["value"], reverse=True)

    freigrenze = FREIGRENZE_EUR
    freigrenze_left = max(0.0, freigrenze - max(0.0, totals["taxable_profit"] + realized_total["taxable"]))

    # Indikatoren je Asset (für die ATH-Nähe-Analyse der Empfehlungen)
    indicators = []
    ind_map = {}
    for asset in sorted(set(r["asset"] for r in rows)):
        try:
            data = compute_indicators(asset)
            if data:
                data["asset"] = asset
                indicators.append(data)
                ind_map[asset] = data
        except Exception:
            pass

    # ---------- Verkaufsempfehlungen (Strategien 1-5, Schwellen konfigurierbar) ----------
    strat = get_strategy_settings()
    min_value = strat["min_value_eur"]
    rebalance_share = strat["rebalance_share"]
    ath_near_pct = strat["ath_near_pct"]
    recommendations = []
    for r in rows:
        r["recs"] = []
        if r["value"] < min_value:
            continue
        ind = ind_map.get(r["asset"])
        ath_dist = ind.get("ath_distance_pct") if ind else None
        near_ath = ath_dist is not None and ath_dist >= ath_near_pct
        share = (r["value"] / totals["value"]) if totals["value"] else 0.0
        r["share_pct"] = share * 100.0

        def _rec(level, icon, text, tip, sell_pct=None):
            entry = {
                "asset": r["asset"], "level": level, "icon": icon,
                "text": text, "tip": tip,
                "qty": r["qty"], "price": r["price"], "value": r["value"],
            }
            if sell_pct is not None:
                entry["sell_pct"] = sell_pct
            recommendations.append(entry)

        # Strategie 1: steuerfreier Anteil im Plus
        if strat["enabled_s1"] and r["taxfree_profit"] > 0 and r["profit"] > 0:
            r["recs"].append({
                "color": "success", "icon": "bi-arrow-up-circle",
                "label": "steuerfrei im Plus",
                "tip": (f"{r['asset']}: {r['taxfree_profit']:+,.2f} € Gewinn aus Anteilen, "
                         f"die länger als 1 Jahr gehalten werden – ein Verkauf jetzt wäre steuerfrei."),
            })
            _rec(
                "success", "bi-check-circle-fill",
                f"{r['asset']} ist steuerfrei und aktuell im Plus "
                f"({r['profit_pct']:+.1f} %) – der steuerfreie Gewinn von "
                f"{r['taxfree_profit']:+,.2f} € wäre sofort realisierbar.",
                ("Strategie 1: Steuerfreie Verkäufe. Anteile mit mehr als 1 Jahr Haltefrist "
                 "sind in Deutschland steuerfrei – ein Verkauf jetzt realisiert den Gewinn "
                 "ohne Steuerlast."))

        # Strategie 2: nahe am 90-Tage-Hoch und steuerfrei verkaufbar
        if strat["enabled_s2"] and near_ath and r["taxfree_profit"] > 0:
            r["recs"].append({
                "color": "warning", "icon": "bi-graph-up-arrow",
                "label": "ATH-Nähe",
                "tip": (f"{r['asset']} notiert nur {ath_dist:+.1f} % unter seinem 90-Tage-Hoch – "
                         f"idealer Punkt für einen steuerfreien (Teil-)Verkauf."),
            })
            _rec(
                "danger", "bi-fire",
                f"{r['asset']} steht {ath_dist:+.1f} % unter seinem 90-Tage-Hoch – "
                f"jetzt steuerfrei verkaufen?",
                ("Strategie 2: Verkauf am Kurshoch. Der Coin notiert nahe am lokalen "
                 "Höchststand der letzten 90 Tage; ein Verkauf hier maximiert den "
                 "steuerfreien Erlös."))

        # Strategie 3: Rebalancing (Anteil > konfigurierter Schwelle)
        if strat["enabled_s3"] and share > rebalance_share:
            r["recs"].append({
                "color": "danger", "icon": "bi-pie-chart-fill",
                "label": f"{share * 100.0:.0f} % Anteil",
                "tip": (f"{r['asset']} macht {share * 100.0:.1f} % des Portfolios aus – "
                         f"über der {rebalance_share * 100.0:.0f}-%-Schwelle. Teilverkauf zum "
                         f"Rebalancing erwägen."),
            })
            _rec(
                "warning", "bi-shuffle",
                f"Rebalancing: {r['asset']} hat {share * 100.0:.1f} % Anteil am Portfolio "
                f"(> {rebalance_share * 100.0:.0f} %) – ein Teilverkauf senkt das Klumpenrisiko.",
                ("Strategie 3: Rebalancing. Einzelpositionen über der konfigurierten Schwelle "
                 "konzentrieren das Risiko; übliches Ziel ist ≤ 40 % pro Coin."))

        # Strategie 4: Gewinnziel erreicht (z. B. +50 % und steuerfrei -> 25 % verkaufen)
        if (strat["enabled_s4"] and r["taxfree_profit"] > 0
                and r["profit_pct"] >= strat["s4_gain_pct"]):
            r["recs"].append({
                "color": "primary", "icon": "bi-bullseye",
                "label": f"Gewinnziel +{strat['s4_gain_pct']:.0f} % erreicht",
                "tip": (f"{r['asset']} liegt bei {r['profit_pct']:+.1f} % Gewinn (≥ "
                         f"+{strat['s4_gain_pct']:.0f} %) und ist steuerfrei – laut Strategie 4 "
                         f"jetzt {strat['s4_sell_pct']:.0f} % des Bestands verkaufen."),
            })
            _rec(
                "success", "bi-bullseye",
                f"Gewinnziel erreicht: {r['asset']} liegt bei {r['profit_pct']:+.1f} % "
                f"(≥ +{strat['s4_gain_pct']:.0f} %) und ist steuerfrei – "
                f"{strat['s4_sell_pct']:.0f} % des Bestands verkaufen.",
                ("Strategie 4: Gewinnziel. Bei konfiguriertem Gewinn (z. B. +50 %) und "
                 "steuerfreier Haltefrist wird ein Teilverkauf (Standard 25 %) empfohlen, "
                 "um Gewinne gezielt zu sichern."),
                sell_pct=strat["s4_sell_pct"])

        # Strategie 5: bald ablaufende Haltefrist (Status 'bald' -> in X Tagen steuerfrei)
        if strat["enabled_s5"] and r["status"] == "bald" and r["days_left"] is not None:
            _rec(
                "info", "bi-hourglass-split",
                f"{r['asset']} wird in {r['days_left']} Tagen steuerfrei – "
                f"einen Verkauf ggf. bis dahin aufschieben.",
                ("Strategie 5: Steueroptimierung. Kurz vor Ablauf der 1-Jahres-Frist "
                 "lohnt das Warten: Der Gewinn wäre danach komplett steuerfrei."))

    rec_order = {"danger": 0, "warning": 1, "success": 2, "info": 3}
    recommendations.sort(key=lambda x: (rec_order.get(x["level"], 9), x["asset"]))

    # Steuerjahre für den CSV-Export ermitteln (Verkaufsjahre + Kaufjahre der Bestände)
    _, sales_all, year_holdings_all = fifo_replay()
    export_years = sorted(
        {s["date"].year for s in sales_all} | set(year_holdings_all.keys()), reverse=True)

    # Steuerjahr-Übersicht: realisierte Gewinne (steuerfrei/steuerpflichtig) je Jahr
    year_map = {}
    year_order = []
    for s in sales_all:
        yy = s["date"].year
        if yy not in year_map:
            year_map[yy] = {"year": yy, "taxfree": 0.0, "taxable": 0.0, "count": 0}
            year_order.append(yy)
        year_map[yy]["count"] += 1
        if s["taxfree"]:
            year_map[yy]["taxfree"] += s["pl"]
        else:
            year_map[yy]["taxable"] += s["pl"]
    year_summaries = []
    for yy in year_order:
        ys = year_map[yy]
        ys["freigrenze"] = ys["taxable"] <= FREIGRENZE_EUR
        ys["share_taxfree"] = 0.0
        total = ys["taxfree"] + ys["taxable"]
        if total > 0:
            ys["share_taxfree"] = max(0.0, min(100.0, ys["taxfree"] / total * 100.0))
        year_summaries.append(ys)

    # Verkaufs-UI: Darf dieser Key echte Orders platzieren?
    trading_status = get_trading_status()

    return render_template(
        "portfolio.html",
        rows=rows,
        totals=totals,
        realized_total=realized_total,
        unavailable=unavailable,
        indicators=indicators,
        freigrenze=freigrenze,
        freigrenze_left=freigrenze_left,
        tax_free_days=TAX_FREE_DAYS,
        maturity_notice_days=MATURITY_NOTICE_DAYS,
        recommendations=recommendations,
        export_years=export_years,
        year_summaries=year_summaries,
        rec_min_value=min_value,
        can_trade=trading_status["ok"],
        trade_block_reason=trading_status["reason"],
    )


@app.route("/execute_sell", methods=["POST"])
def execute_sell():
    """
    Führt einen Teilverkauf als echte Bitvavo-Market-Order aus (mit Bestätigung in der UI).
    Nur möglich mit hinterlegtem Key + Secret UND Handelsberechtigung.
    """
    asset = request.form.get("asset", "").strip().upper()
    try:
        percent = float(request.form.get("percent", ""))
    except (TypeError, ValueError):
        percent = 0.0

    if not asset:
        flash("Kein Coin angegeben.", "error")
        return redirect(url_for("portfolio"))
    if not (0.0 < percent <= 100.0):
        flash("Bitte einen Verkaufsanteil zwischen 0 und 100 % eingeben.", "error")
        return redirect(url_for("portfolio"))

    if SIMULATION_MODE:
        flash("SIMULATION_MODE aktiv: Es werden keine echten Verkäufe ausgeführt.", "error")
        logging.info(f"execute_sell: {asset} {percent}% – im Simulationsmodus übersprungen.")
        return redirect(url_for("portfolio"))

    with get_connection() as conn:
        c = conn.cursor()
        c.execute("SELECT api_key, api_secret FROM credentials ORDER BY id DESC LIMIT 1")
        cred = c.fetchone()
    if not cred or not cred[0] or not cred[1]:
        flash("Verkauf nicht möglich: Kein vollständiger API-Key hinterlegt.", "error")
        return redirect(url_for("portfolio"))

    try:
        bv = get_bitvavo_client()
        acc = bitvavo_request_with_retry(bv.account)  # frisch, nicht gecacht
        if isinstance(acc, dict) and "errorCode" in acc:
            flash(f"Verkauf nicht möglich – Bitvavo-Fehler {acc['errorCode']}: "
                  f"{acc.get('error', '')}", "error")
            return redirect(url_for("portfolio"))
        if not acc.get("trading"):
            flash("Verkauf nicht möglich: Der API-Key hat KEINE Handelsberechtigung "
                  "(Read-only-Key).", "error")
            logging.warning(f"execute_sell: {asset} – Key ohne Handelsberechtigung.")
            return redirect(url_for("portfolio"))
    except Exception as e:
        flash(f"Verkauf nicht möglich: {str(e)}", "error")
        return redirect(url_for("portfolio"))

    # Aktuellen Bestand des Coins ermitteln (inkl. Transfer-Lots)
    lots, _ = build_lots()
    total_qty = sum(l["qty"] for l in lots if l["asset"] == asset)
    if total_qty <= 0:
        flash(f"Verkauf nicht möglich: Kein Bestand von {asset} in der FIFO-Basis.", "error")
        return redirect(url_for("portfolio"))

    qty_sell = total_qty * percent / 100.0
    market = f"{asset}-EUR"
    amount_str = f"{qty_sell:.8f}".rstrip("0").rstrip(".")
    logging.info(f"execute_sell: Verkaufe {amount_str} {asset} ({percent:g}% von {total_qty:g}) "
                 f"per Market-Order ...")

    try:
        response = bitvavo_request_with_retry(
            bv.placeOrder, market, "sell", "market", {"amount": amount_str})
    except Exception as e:
        flash(f"Order fehlgeschlagen: {str(e)}", "error")
        logging.error(f"execute_sell: Order-Fehler – {e}")
        return redirect(url_for("portfolio"))

    if "orderId" not in response:
        flash(f"Bitvavo hat die Order abgelehnt: {response}", "error")
        logging.error(f"execute_sell: abgelehnt – {response}")
        return redirect(url_for("portfolio"))

    # Fills in die Trade-Historie schreiben (Side sell) – FIFO wertet sie sofort aus
    imported_fills = 0
    with get_connection() as conn:
        c2 = conn.cursor()
        for f in response.get("fills") or []:
            try:
                amt = float(f.get("amount") or 0.0)
                prc = float(f.get("price") or 0.0)
            except (TypeError, ValueError):
                continue
            if amt <= 0:
                continue
            tid = f.get("id") or f.get("tradeId")
            c2.execute("""
                INSERT OR IGNORE INTO trades (
                    timestamp, asset, amount_eur, filled_asset, avg_price,
                    order_id, side, trade_id, fee, fee_currency
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                asset, amt * prc, amt, prc,
                response.get("orderId"), "sell", tid,
                float(f.get("fee") or 0.0) if f.get("fee") is not None else 0.0,
                f.get("feeCurrency"),
            ))
            if c2.rowcount:
                imported_fills += 1
        conn.commit()

    sold_qty = sum(float(f.get("amount") or 0.0) for f in response.get("fills") or [])
    flash(f"Verkauf ausgeführt: {sold_qty:.8g} {asset} (~{percent:g}% des Bestands) per "
          f"Market-Order, Order {response.get('orderId')}.", "success")
    logging.info(f"execute_sell: Erfolg – {sold_qty:g} {asset}, Order {response.get('orderId')}, "
                 f"{imported_fills} Fills gespeichert.")
    return redirect(url_for("portfolio"))


@app.route("/portfolio/export")
def portfolio_export():
    """
    CSV-Export der FIFO-Berechnung je Steuerjahr (realisierte Gewinne,
    Jahresend-Bestände, Freigrenzen-Prüfung) als Jahressteuer-Unterlage.
    Optional: ?year=JJJJ für ein einzelnes Steuerjahr.
    """
    try:
        years_arg = request.args.get("year", "").strip()
        only_year = int(years_arg) if years_arg else None
    except Exception:
        only_year = None

    _, sales, year_holdings = fifo_replay()
    all_years = sorted({s["date"].year for s in sales} | set(year_holdings.keys()), reverse=True)
    years = [y for y in all_years if only_year is None or y == only_year]

    def _qty(v):
        return f"{v:.10f}".rstrip("0").rstrip(".") if v else "0"

    buf = io.StringIO()
    buf.write("\ufeff")  # UTF-8-BOM, damit Excel die Umlaute korrekt öffnet
    w = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    w.writerow(["Bitvavo-Autobot – FIFO-Jahressteuer-Export", "erstellt",
                datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "", "", "", "", "", "", "", "", ""])
    w.writerow(["Hinweis", "", "Keine Steuerberatung – nur Käufe/Verkäufe aus der importierten "
                               "Bitvavo-Trade-Historie (EUR-Märkte), FIFO-Haltefrist 365 Tage.",
                "", "", "", "", "", "", "", "", ""])
    w.writerow([])

    for year in years:
        year_sales = [s for s in sales if s["date"].year == year]
        tf = sum(s["pl"] for s in year_sales if s["taxfree"])
        tx = sum(s["pl"] for s in year_sales if not s["taxfree"])

        w.writerow(["Steuerjahr", year, "", "", "", "", "", "", "", "", "", ""])
        w.writerow(["Zusammenfassung", year, "", "", "", "", f"{tf:+.2f}", "", "", "", "",
                    "Steuerfreier realisierter Gewinn (Verkäufe nach > 1 Jahr Haltefrist)"])
        w.writerow(["Zusammenfassung", year, "", "", "", "", f"{tx:+.2f}", "", "", "", "",
                    "Steuerpflichtiger realisierter Gewinn (Verkäufe innerhalb von 1 Jahr)"])
        if tx > 0:
            rest = FREIGRENZE_EUR - tx
            if rest >= 0:
                frei = f"Freigrenze {FREIGRENZE_EUR:.0f} € NICHT überschritten – Rest {rest:,.2f} €"
            else:
                frei = (f"Freigrenze {FREIGRENZE_EUR:.0f} € um {-rest:,.2f} € überschritten – "
                        f"gesamter Betrag steuerpflichtig (Freigrenze, kein Freibetrag)")
        else:
            frei = f"Kein steuerpflichtiger Gewinn – Freigrenze {FREIGRENZE_EUR:.0f} € nicht relevant"
        w.writerow(["Zusammenfassung", year, "", "", "", "", "", "", "", "", "", frei])

        if year_sales:
            w.writerow(["Verkauf (realisiert)", year, "", "", "", "", "", "", "", "", "", ""])
            for s in year_sales:
                w.writerow(["Verkauf", year, s["date"].strftime("%Y-%m-%d"), s["asset"],
                            _qty(s["qty"]), f"{s['sell_price']:.8f}", f"{s['gross']:.2f}",
                            f"{s['basis']:.2f}", f"{s['pl']:.2f}", s["held_days"],
                            "ja" if s["taxfree"] else "nein",
                            f"FIFO-Kauf vom {s['buy_date'].strftime('%Y-%m-%d')}"])

        holdings = year_holdings.get(year, {})
        if holdings:
            w.writerow(["Bestand am Jahresende", year, "", "", "", "", "", "", "", "", "", ""])
            for asset in sorted(holdings):
                for lot in holdings[asset]:
                    w.writerow(["Bestand", year, lot["date"].strftime("%Y-%m-%d"), asset,
                                _qty(lot["qty"]), f"{lot['price']:.8f}", "",
                                f"{lot['price'] * lot['qty']:.2f}", "", "", "",
                                f"Einstand {lot['price'] * lot['qty']:.2f} € – noch gehalten "
                                f"am 31.12.{year}"])
        w.writerow([])

    if not years:
        w.writerow(["Keine Trades im gewählten Zeitraum."])

    filename = f"bitvavo-fifo-{only_year}.csv" if only_year else "bitvavo-fifo-steuerjahre.csv"
    return Response(
        buf.getvalue(),
        mimetype="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.route("/health")
def health():
    """Leichtgewichtiger Health-Check ohne Login – für Docker & verifizieren.sh."""
    counts = {}
    try:
        with get_connection() as conn:
            c = conn.cursor()
            counts["trades"] = c.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
            counts["schedules"] = c.execute("SELECT COUNT(*) FROM schedules").fetchone()[0]
    except Exception as e:
        counts = {"error": str(e)}
    return jsonify({
        "status": "ok",
        "app": "Bitvavo-Autobot",
        "version": APP_VERSION,
        "sdk": SDK_VERSION,
        "timezone": current_tz_name(),
        "server_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "uptime_s": int(time.time() - START_TIME),
        "db_path": os.path.abspath(DB_NAME),
        "db_exists": os.path.exists(DB_NAME),
        "db_size_bytes": file_size_or_none(DB_NAME),
        "log_path": os.path.abspath(LOG_FILE),
        "log_exists": os.path.exists(LOG_FILE),
        "simulation_mode": SIMULATION_MODE,
        "debug_mode": DEBUG_MODE,
        "encryption_enabled": ENCRYPTION_KEY is not None,
        "selfcheck_errors": STARTUP_ERRORS,
        "scheduler_alive": SCHEDULER_THREAD.is_alive() if SCHEDULER_THREAD else False,
        "counts": counts,
    })


@app.route("/system")
def system():
    """System-/Healthcheck-Seite: Versionen, DB-Pfad, Zeitzone, API-Key-Status auf einen Blick."""
    with get_connection() as conn:
        c = conn.cursor()
        counts = {
            "trades": c.execute("SELECT COUNT(*) FROM trades").fetchone()[0],
            "transfers": c.execute("SELECT COUNT(*) FROM transfers").fetchone()[0],
            "schedules": c.execute("SELECT COUNT(*) FROM schedules").fetchone()[0],
            "schedule_lines": c.execute("SELECT COUNT(*) FROM schedule_lines").fetchone()[0],
            "balances": c.execute("SELECT COUNT(*) FROM balances").fetchone()[0],
        }
        c.execute("SELECT api_key, api_secret FROM credentials ORDER BY id DESC LIMIT 1")
        cred = c.fetchone()

    # Log-Tail für die System-Seite
    log_lines = []
    try:
        with open(LOG_FILE, encoding="utf-8", errors="replace") as fh:
            log_lines = fh.readlines()[-200:]
    except OSError:
        log_lines = []

    key_status = None
    if cred and cred[0]:
        saved_key, saved_secret_raw = cred
        secret_plain = ""
        decrypt_error = ""
        if saved_secret_raw:
            if saved_secret_raw.startswith("enc:"):
                try:
                    secret_plain = decrypt_secret(saved_secret_raw)
                except Exception as e:
                    decrypt_error = str(e)
            else:
                secret_plain = saved_secret_raw  # Altbestand im Klartext
        key_status = {
            "key_masked": saved_key[:5] + "..." if saved_key else "",
            "secret_present": bool(saved_secret_raw),
            "secret_encrypted": bool(saved_secret_raw) and saved_secret_raw.startswith("enc:"),
            "secret_ok": bool(secret_plain) and not decrypt_error,
            "decrypt_error": decrypt_error,
        }
        if secret_plain and not decrypt_error and not SIMULATION_MODE:
            try:
                bv = get_bitvavo_client()
                acc = bitvavo_request_with_retry(bv.account)
                if isinstance(acc, dict) and "errorCode" in acc:
                    key_status["permission_error"] = (
                        f"Fehler {acc['errorCode']}: {acc.get('error', '')}")
                else:
                    key_status["permissions"] = {
                        "trading": bool(acc.get("trading")),
                        "withdrawal": bool(acc.get("withdrawal")),
                    }
            except Exception as e:
                key_status["permission_error"] = str(e)
        elif SIMULATION_MODE and saved_key:
            key_status["permission_error"] = "Simulationsmodus aktiv – Berechtigungen nicht geprüft."

    try:
        import sys
        python_version = sys.version.split()[0]
    except Exception:
        python_version = "?"

    return render_template(
        "system.html",
        log_lines=log_lines,
        app_version=APP_VERSION,
        sdk_version=SDK_VERSION,
        python_version=python_version,
        timezone=current_tz_name(),
        server_time=datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        uptime_s=int(time.time() - START_TIME),
        db_path=os.path.abspath(DB_NAME),
        db_size=file_size_or_none(DB_NAME),
        log_path=os.path.abspath(LOG_FILE),
        log_size=file_size_or_none(LOG_FILE),
        simulation_mode=SIMULATION_MODE,
        debug_mode=DEBUG_MODE,
        encryption_enabled=ENCRYPTION_KEY is not None,
        selfcheck_errors=STARTUP_ERRORS,
        scheduler_alive=SCHEDULER_THREAD.is_alive() if SCHEDULER_THREAD else False,
        key_status=key_status,
        counts=counts,
    )


@app.route("/system/log")
def system_log():
    """Liefert das komplette Log als Textdatei (login-geschützt) für die System-Seite."""
    try:
        with open(LOG_FILE, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError as e:
        return Response(f"Log nicht lesbar: {e}", mimetype="text/plain")
    return Response(
        content,
        mimetype="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=bitmaster.log"},
    )


########################################
# MAIN
########################################
# Pflicht-Tabellen der App – der Selbstcheck prüft, ob alle existieren.
REQUIRED_TABLES = (
    "credentials", "schedules", "schedule_lines", "trades", "balances",
    "historical_rates", "email_settings", "transfers", "app_settings",
)


def startup_selfcheck():
    """
    Selbstcheck beim Start: prüft DB/Schema, .env-Pflichtwerte und Konfiguration
    und loggt deutliche deutsche Meldungen, statt still weiterzulaufen.
    Rückgabe: Liste der kritischen Fehler (App startet trotzdem, damit der
    Nutzer die Fehler auf der System-Seite sehen kann).
    """
    errors = []

    # --- 1) Datenbank & Schema ---
    if not os.path.exists(DB_NAME):
        logging.warning(
            f"SELBSTCHECK: Datenbank '{os.path.abspath(DB_NAME)}' existiert nicht – "
            "sie wird jetzt neu angelegt (erster Start?).")
    try:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute("PRAGMA integrity_check")
            integrity = c.fetchone()[0]
            if integrity != "ok":
                errors.append(f"Datenbank beschädigt (integrity_check: {integrity}). "
                              "Bitte aus Backup wiederherstellen!")
                logging.error(f"SELBSTCHECK: {errors[-1]}")
            c.execute("PRAGMA foreign_key_check")
            fk_violations = c.fetchall()
            if fk_violations:
                errors.append(f"{len(fk_violations)} Fremdschlüssel-Verletzungen in der Datenbank "
                              f"(z. B. Zeilen ohne Bezug): {fk_violations[:5]}")
                logging.error(f"SELBSTCHECK: {errors[-1]}")
            existing = {r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            missing = [t for t in REQUIRED_TABLES if t not in existing]
            if missing:
                errors.append(f"Fehlende Tabellen in der Datenbank: {', '.join(missing)}")
                logging.error(f"SELBSTCHECK: {errors[-1]} (werden von init_db() angelegt)")
    except sqlite3.DatabaseError as e:
        errors.append(f"Datenbank nicht lesbar: {e}")
        logging.error(f"SELBSTCHECK: {errors[-1]}")

    # --- 2) .env-Pflichtwerte ---
    if not os.environ.get("MASTER_PASSWORD"):
        logging.warning(
            "SELBSTCHECK: MASTER_PASSWORD nicht gesetzt – es gilt das unsichere "
            "Standard-Passwort 'bitmaster'. Bitte in der .env setzen!")
    if not os.environ.get("FLASK_SECRET_KEY"):
        logging.warning(
            "SELBSTCHECK: FLASK_SECRET_KEY nicht gesetzt – Sessions nutzen einen "
            "festen Ersatzschlüssel. Bitte in der .env setzen!")
    if not os.environ.get("ENCRYPTION_KEY"):
        logging.warning(
            "SELBSTCHECK: ENCRYPTION_KEY nicht gesetzt – gespeicherte API-Secrets "
            "liegen unverschlüsselt in der Datenbank!")
    if not (os.environ.get("BITVAVO_API_KEY") or os.environ.get("BITVAVO_API_SECRET")):
        logging.info(
            "SELBSTCHECK: Kein API-Key in der .env – Key wird über die "
            "Einstellungen-Seite in der Datenbank verwaltet (normal).")

    # --- 3) ENCRYPTION_KEY-Format ---
    if os.environ.get("ENCRYPTION_KEY") and ENCRYPTION_KEY is None:
        errors.append("ENCRYPTION_KEY ist gesetzt, aber ungültig (64 Hex-Zeichen erwartet) "
                      "– Secrets bleiben im Klartext!")
        logging.error(f"SELBSTCHECK: {errors[-1]}")

    # --- 4) Datenverzeichnis schreibbar? ---
    try:
        probe = os.path.join(os.path.dirname(os.path.abspath(DB_NAME)) or ".", ".write_probe")
        with open(probe, "w") as fh:
            fh.write("ok")
        os.remove(probe)
    except OSError as e:
        errors.append(f"Datenverzeichnis nicht schreibbar: {e}")
        logging.error(f"SELBSTCHECK: {errors[-1]}")

    if not errors:
        logging.info("SELBSTCHECK: alle Prüfungen bestanden (DB, Schema, Konfiguration).")
    else:
        logging.error(f"SELBSTCHECK: {len(errors)} Problem(e) gefunden (siehe oben).")
    return errors


if __name__ == "__main__":
    STARTUP_ERRORS = startup_selfcheck()
    init_db()
    migrate_credentials_encryption()
    dedupe_trades()
    dedupe_transfers()
    load_schedules_into_scheduler()
    logging.info(f"Starte Flask-Server (SIMULATION_MODE={SIMULATION_MODE}, DEBUG={DEBUG_MODE}) ...")
    # Debugmodus NICHT im Docker-Container verwenden (FLASK_DEBUG=0)
    port = 5000
    try:
        port = int(os.environ.get("PORT") or "5000")
        if port <= 0:  # PORT=0 (zufälliger Port) -> Standard 5000 verwenden
            port = 5000
    except ValueError:
        pass
    app.run(host="0.0.0.0", port=port, debug=DEBUG_MODE, use_reloader=False)