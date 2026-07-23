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


CODE_RE = re.compile(r"\bBBR[A-Z]*\d+[A-Z]*\b", re.I)


def clean_name(raw: str) -> str:
    """Strip shop/brand boilerplate, leaving just the model name.

    Handles the real-world prefixes seen on Horizon listings, e.g.
    "[Preorder] BBR Model 1:64 Maserati MC20 #1 ..." -> "Maserati MC20 #1 ..."
    """
    name = raw
    name = re.sub(r"^\s*\[[^\]]*\]\s*", "", name)              # [Preorder]
    # Strip leading boilerplate repeatedly, because it appears in varying
    # orders: "BBR Model 1:64 X", "BBR 1:64 X", "Model 1:64 X", "1/64 X".
    prefixes = [
        r"^\s*BBR\s*Models?\b\s*",
        r"^\s*BBR\b\s*",
        r"^\s*Models?\b\s*",
        r"^\s*1\s*[:/]\s*64\b\s*",
    ]
    changed = True
    while changed:
        changed = False
        for pat in prefixes:
            stripped = re.sub(pat, "", name, flags=re.I)
            if stripped != name:
                name = stripped
                changed = True
    # Trailing product code adds nothing to the name (it lives in `series`)
    name = CODE_RE.sub("", name)
    name = name.replace('"', "").strip(" -–—|,")
    return re.sub(r"\s+", " ", name).strip()


def is_valid_name(name: str) -> bool:
    """Reject table headers, bare product codes, and other non-models."""
    if not name or len(name) < 4:
        return False
    lowered = name.lower().strip()
    # Header cells from the wiki tables
    if lowered in {"model #", "model", "name", "image", "photo", "code",
                   "reference", "ref", "product", "product #", "notes"}:
        return False
    # A bare product code is not a model name
    if CODE_RE.fullmatch(name.strip()):
        return False
    # Needs at least one letter (not just digits/punctuation)
    if not any(ch.isalpha() for ch in name):
        return False
    return True


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

    for row in soup.select("table tr"):
        # Skip header rows outright — they produced junk like "Model #".
        if row.find("th") is not None:
            continue

        cells = row.find_all("td")
        if not cells:
            continue

        # Prefer the row's wiki LINK: its text is the real model name
        # ("Maserati MC20 Bianco Audace") and its href is the model's own page.
        model_link = None
        for a in row.find_all("a", href=True):
            href = a["href"]
            text = a.get_text(" ", strip=True)
            if not href.startswith("/wiki/"):
                continue
            if ":" in href.split("/wiki/")[-1]:      # File:, Category: pages
                continue
            if not is_valid_name(clean_name(text)):
                continue
            model_link = a
            break

        if model_link is not None:
            name = clean_name(model_link.get_text(" ", strip=True))
            product_url = "https://bbr-diecast-models.fandom.com" + model_link["href"]
        else:
            # No usable link — fall back to the longest text cell, but this
            # is where bare codes/headers used to slip through, so validate.
            texts = [c.get_text(" ", strip=True) for c in cells]
            candidates = [clean_name(t) for t in texts]
            candidates = [c for c in candidates if is_valid_name(c)]
            if not candidates:
                continue                       # row carries no model name → skip
            name = max(candidates, key=len)
            product_url = f"https://bbr-diecast-models.fandom.com/wiki/{WIKI_PAGE}"

        if not is_valid_name(name):
            continue

        # Product code, from anywhere in the row.
        row_text = row.get_text(" ", strip=True)
        code_match = CODE_RE.search(row_text)
        code = code_match.group(0) if code_match else ""

        img = row.find("img")
        image_url = None
        if img:
            image_url = img.get("data-src") or img.get("src")
            if image_url and image_url.startswith("//"):
                image_url = "https:" + image_url
            if image_url:
                image_url = image_url.split("/revision/")[0]

        found.append(
            {
                "modelName": name,
                "imageURL": image_url,
                "productURL": product_url,
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
            code_match = CODE_RE.search(raw_title)
            cleaned = clean_name(raw_title)
            if not is_valid_name(cleaned):
                continue

            found.append(
                {
                    "modelName": cleaned,
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

    # Dedupe by model name. When the same car appears in both sources, keep the
    # RICHER record (one with an image and a product code) rather than whichever
    # happened to come first — the wiki has better names, the shop better images.
    def richness(entry) -> int:
        score = 0
        if entry.get("imageURL"):
            score += 2
        if entry.get("series"):
            score += 1
        if entry.get("make"):
            score += 1
        return score

    best = {}
    for item in combined:
        name = (item.get("modelName") or "").strip()
        if not is_valid_name(name):
            continue
        key = name.lower()
        if key not in best or richness(item) > richness(best[key]):
            best[key] = item

    releases = list(best.values())
    releases.sort(key=lambda e: e["modelName"].lower())

    if not releases:
        print("❌ No BBR releases scraped — leaving existing feed untouched.", file=sys.stderr)
        sys.exit(1)

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(releases, fh, indent=2, ensure_ascii=False)

    print(f"✅ Wrote {len(releases)} BBR releases → {OUT_PATH}")


if __name__ == "__main__":
    main()
