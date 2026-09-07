#!/usr/bin/env python3
"""
Screenshot-Aufnehmer für Bitvavo-Autobot (lokal).
Nimmt Screenshots der wichtigsten Seiten auf und legt sie im Ordner screen/ ab.
Achtung: Das Skript ruft die laufende Instanz (http://127.0.0.1:5000) auf.
Dadurch werden ggf. private Daten (API-Key, Trades) in die Screenshots geladen.
Verwenden Sie eigene Daten/Keys und entfernen Sie sensible Angaben vor einer
zweiten Nutzung.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

SCREEN_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCREEN_DIR.parent))

try:
    import selenium.webdriver as webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.support.ui import WebDriverWait
    from selenium.webdriver.support import expected_conditions as EC
    HAVE_SELENIUM = True
except Exception:
    HAVE_SELENIUM = False

BASE_URL = os.environ.get("BASE_URL", "http://127.0.0.1:5000")
SCREENSHOT_LIST = [
    ("01_login.png", f"{BASE_URL}/login"),
    ("02_dashboard.png", f"{BASE_URL}/"),
    ("03_positions.png", f"{BASE_URL}/"),  # positionen sind auf dashboard präsent
    ("04_wizard_anlegen.png", f"{BASE_URL}/add_schedule"),
    ("05_wizard_bestätigung.png", f"{BASE_URL}/add_schedule"),  # vorschau wird hier angezeigt
    ("06_trades.png", f"{BASE_URL}/trades"),
    ("07_portfolio.png", f"{BASE_URL}/portfolio"),
    ("08_einstellungen.png", f"{BASE_URL}/settings"),
    ("09_system.png", f"{BASE_URL}/system"),
    ("10_chart_toggle.png", f"{BASE_URL}/"),  # chart auf dashboard
]


def take_screenshot(driver, url, path):
    driver.get(url)
    WebDriverWait(driver, 10).until(
        EC.presence_of_element_located((By.TAG_NAME, "body"))
    )
    dst = SCREEN_DIR / path
    driver.save_screenshot(str(dst))
    print(f"screenshot: {dst} ({dst.stat().st_size} bytes)")


def main():
    if not HAVE_SELENIUM:
        print("Fehler: selenium nicht installiert")
        print("pip install selenium")
        sys.exit(1)

    options = webdriver.FirefoxOptions()
    options.add_argument("--headless")
    options.add_argument("--width=1200")
    options.add_argument("--height=900")
    options.binary_location = os.environ.get("FIREFOX_BINARY", "/usr/bin/firefox")

    driver = webdriver.Firefox(options=options)
    try:
        for name, url in SCREENSHOT_LIST:
            take_screenshot(driver, url, name)
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
