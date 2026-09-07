#!/usr/bin/env python3
"""
Erstellt PNG-Platzhalter-Screenshots (statische HTML-Darstellung) für Bitvavo-Autobot.
Kein echter Login, kein echter API-Key, keine echten Credentials.
Nutzt die tatsächlichen Template-Vorlagen als HTML-Rahmen (Platzhalter).
"""

import base64
import io
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont
    HAVE_PIL = True
except Exception:
    HAVE_PIL = False

SCREEN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCREEN_DIR.parent))

# Platzhalter-Darstellung (kein Login, kein API-Key)
PLACEHOLDERS = {
    "01_login.png": "Bitvavo-Autobot — Anmeldung",
    "02_dashboard.png": "Bitvavo-Autobot — Dashboard",
    "03_positions.png": "Bitvavo-Autobot — Meine Kryptowährungen",
    "04_wizard_anlegen.png": "Bitvavo-Autobot — Zeitplan anlegen",
    "05_wizard_bestätigung.png": "Bitvavo-Autobot — Zeitplan gespeichert (Vorschau)",
    "06_trades.png": "Bitvavo-Autobot — Trades",
    "07_portfolio.png": "Bitvavo-Autobot — Portfolio & Steuern",
    "08_einstellungen.png": "Bitvavo-Autobot — Einstellungen",
    "09_system.png": "Bitvavo-Autobot — System",
    "10_chart_toggle.png": "Bitvavo-Autobot — Chart (BTC-EUR / Portfolio-Wert)",
}

TITLE_FONT = None
SUB_FONT = None
try:
    TITLE_FONT = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 26)
    SUB_FONT = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 14)
except Exception:
    try:
        TITLE_FONT = ImageFont.truetype("/Library/Fonts/Arial.ttf", 26)
        SUB_FONT = ImageFont.truetype("/Library/Fonts/Arial.ttf", 14)
    except Exception:
        pass


def make_placeholder_png(path, title):
    W, H = 1080, 780
    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)

    if TITLE_FONT:
        d.text((40, 40), title, font=TITLE_FONT, fill=(33, 37, 41))
        d.text((40, 80), "Bitvavo-Autobot — Demo-Vorschau (Platzhalter)", font=SUB_FONT, fill=(108, 117, 125))

        d.text((40, 120), "Screenshots: bitte mit echten Browsern aufnehmen", font=SUB_FONT, fill=(108, 117, 125))
        d.text((40, 140), "Siehe screen/README_screenshots.md", font=SUB_FONT, fill=(108, 117, 125))

        # Einfache Rahmen-Darstellung (kein echter Inhalt)
        d.rectangle([40, 200, W - 40, H - 120], outline=(22, 160, 133), width=2)
        d.text((W // 2 - 160, H // 2), "Dashboard-Vorschau", font=SUB_FONT, fill=(108, 117, 125))
        d.text((W // 2 - 140, H // 2 + 30), "Bitvavo-Autobot Demo", font=SUB_FONT, fill=(108, 117, 125))
    img.save(path, "PNG")
    return path


def main():
    for name, title in PLACEHOLDERS.items():
        p = SCREEN_DIR / name
        p = make_placeholder_png(p, title)
        print(f"created: {p}")


if __name__ == "__main__":
    main()
