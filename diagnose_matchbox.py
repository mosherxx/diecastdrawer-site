"""Diagnostic: show Matchbox table headers + first few rows per column."""
from __future__ import annotations
import requests
from bs4 import BeautifulSoup

API = "https://matchbox.fandom.com/api.php"
HEADERS = {"User-Agent": "DiecastDrawer/1.0 (contact: hello@diecastdrawer.app)", "Accept": "application/json"}

def clean(s): 
    import re
    return re.sub(r"\s+", " ", s or "").strip()

resp = requests.get(API, params={
    "action": "parse", "page": "List_of_2026_Matchbox",
    "format": "json", "prop": "text", "formatversion": "2",
}, headers=HEADERS, timeout=30)
html = resp.json()["parse"]["text"]
soup = BeautifulSoup(html, "html.parser")

for ti, table in enumerate(soup.find_all("table")):
    headers = [clean(th.get_text()) for th in table.find_all("th")]
    if not headers or not any("model" in h.lower() or "name" in h.lower() for h in headers):
        continue
    print(f"\n=== TABLE {ti} HEADERS: {headers}")
    rows = table.find_all("tr")
    for r in rows[1:4]:  # first 3 data rows
        cells = [clean(td.get_text())[:40] for td in r.find_all("td")]
        print("  ROW:", cells)
    break  # just the first matching table
