#!/usr/bin/env python3
"""
Scrape BBR 1:64 releases.

Two sources, merged:
  1. The BBR fandom wiki "Full Collection" page, read through the MediaWiki
     API (NOT by scraping the rendered page — the API returns clean data and
     avoids the ad-heavy HTML, which also blocks plain fetchers).
  2. Horizon Diecast's BBR 1:64 collection via Shopify's products.json, which
     is more current for brand-new releases than the volunteer-maintained wiki.

Output: data/bbr_releases.json
Schema matches the other feeds: modelName, imageURL, productURL, scale, series, make
"""

import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

WIKI_API = "https://bbr-diecast-models.fandom.com/api.php"
WIKI_PAGE = "Full_Collection"

SHOP_BASE = "https://horizondiecast.com"
SHOP_COLLECTION = "bbr-1-64"

OUT_PATH = os.path.join("data", "bbr_releases.json")

HEADERS = {
    "User-Agent": "DiecastDrawerBot/1.0 (+https://github.com/mosherxx/diecastdrawer-site)"
}

KNOWN_MAKES = [
    "Ferrari", "Lamborghini", "Porsche", "Pagani", "McLaren", "Bugatti",
    "Aston Martin", "Mercedes", "BMW", "Audi", "Alfa Romeo", "Maserati",
    "Koenigsegg", "Toyota", "Nissan", "Honda", "Ford",
]


def detect_make(title: str) -> str:
    lowered = title.lower()
    for make in KNOWN_MAKES:
        if make.lower() in lowered:
            return make
    return ""


def clean_name(raw: str) -> str:
    name = re.sub(r"^\s*\[[^\]]*\]\s*", "", raw)          # [Preorder]
    name = re.sub(r"^\s*BBR\s*1[:/]64\s*", "", name, flags=re.I)
    name = re.sub(r"^\s*BBR\s+", "", name, flags=re.I)
    name = name.replace('"', "").strip()
    return re.sub(r"\s+", " ", name)


# ---------------------------------------------------------------- wiki source

def scrape_wiki():
    """Read the Full Collection page through the MediaWiki parse API."""
    params = {
        "action": "parse",
        "page": WIKI_PAGE,
        "prop": "text",
        "format": "json",
        "formatversion": "2",
    }
    try:
        resp = requests.get(WIKI_API, params=params, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        html = resp.json()["parse"]["text"]
    except Exception as exc:  # noqa: BLE001
        print(f"⚠️  wiki source failed: {exc}", file=sys.stderr)
        return []

    soup = BeautifulSoup(html, "html.parser")
    found = []

    # The collection is laid out in tables; take the rows and pull the first
    # meaningful text cell as the model name, plus any thumbnail in the row.
    for row in soup.select("table tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) < 2:
            continue

        texts = [c.get_text(" ", strip=True) for c in cells]
        name = next((t for t in texts if len(t) > 4 and not t.isdigit()), "")
        if not name:
            continue

        img = row.find("img")
        image_url = None
        if img:
            image_url = img.get("data-src") or img.get("src")
            if image_url and image_url.startswith("//"):
                image_url = "https:" + image_url
            # Fandom appends scaling params after /revision/ — strip for full size
            if image_url:
                image_url = image_url.split("/revision/")[0]

        # A BBR reference code often appears in one of the cells (e.g. BBRC123)
        code = ""
        for t in texts:
            m = re.search(r"\bBBR[A-Z]*\d+[A-Z]*\b", t, flags=re.I)
            if m:
                code = m.group(0)
                break

        found.append(
            {
                "modelName": clean_name(name),
                "imageURL": image_url,
                "productURL": f"https://bbr-diecast-models.fandom.com/wiki/{WIKI_PAGE}",
                "scale": "1/64",
                "series": code,
                "make": detect_make(name),
            }
        )

    print(f"📚 wiki: {len(found)} rows")
    return found


# ---------------------------------------------------------------- shop source

def scrape_shop():
    """Newer releases from Horizon Diecast's BBR 1:64 collection."""
    found = []
    for page in range(1, 8):
        url = f"{SHOP_BASE}/collections/{SHOP_COLLECTION}/products.json?limit=250&page={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.raise_for_status()
            products = resp.json().get("products", [])
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  shop page {page} failed: {exc}", file=sys.stderr)
            break

        if not products:
            break

        for p in products:
            raw_title = p.get("title", "").strip()
            if not raw_title:
                continue

            images = p.get("images") or []
            image_url = images[0].get("src") if images else None
            if image_url and image_url.startswith("//"):
                image_url = "https:" + image_url

            handle = p.get("handle", "")
            code_match = re.search(r"\bBBR[A-Z]*\d+[A-Z]*\b", raw_title, flags=re.I)

            found.append(
                {
                    "modelName": clean_name(raw_title),
                    "imageURL": image_url,
                    "productURL": f"{SHOP_BASE}/products/{handle}" if handle else None,
                    "scale": "1/64",
                    "series": code_match.group(0) if code_match else "",
                    "make": detect_make(raw_title),
                }
            )

        time.sleep(1)

    print(f"🛒 shop: {len(found)} products")
    return found


def main():
    combined = scrape_shop() + scrape_wiki()   # shop first = newest wins on dedupe

    releases = []
    seen = set()
    for item in combined:
        name = (item.get("modelName") or "").strip()
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        releases.append(item)

    if not releases:
        print("❌ No BBR releases scraped — leaving existing feed untouched.", file=sys.stderr)
        sys.exit(1)

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(releases, fh, indent=2, ensure_ascii=False)

    print(f"✅ Wrote {len(releases)} BBR releases → {OUT_PATH}")


if __name__ == "__main__":
    main()
