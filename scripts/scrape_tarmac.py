#!/usr/bin/env python3
"""
Scrape Tarmac Works 1/64 releases from the official store.

The site is Shopify, so we use the public products.json endpoint on the
"tarmac-works" collection rather than rendering pages with Playwright. That is
faster, far more stable, and needs no browser in CI.

Collection: https://www.tarmacworks.com/collections/tarmac-works
(~312 products total, ~286 of them 1/64)

Output: data/tarmac_releases.json
Schema: modelName, imageURL, productURL, scale, series, make
"""

import json
import os
import re
import sys
import time

import requests

BASE = "https://www.tarmacworks.com"
COLLECTION = "tarmac-works"
OUT_PATH = os.path.join("data", "tarmac_releases.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DiecastDrawerBot/1.0; "
                  "+https://github.com/mosherxx/diecastdrawer-site)",
    "Accept": "application/json",
}

# Tarmac's own product lines, used as the `series` label.
SERIES_TAGS = ["GLOBAL64", "HOBBY64+", "HOBBY64", "TRUCK64", "ROAD64",
               "COLLAB64", "PARTS64", "HOBBY43", "HOBBY18"]

KNOWN_MAKES = [
    "Alfa Romeo", "Audi", "Bentley", "BMW", "Datsun", "Dodge", "Ferrari",
    "FIAT", "Ford", "Honda", "Hyundai", "Koenigsegg", "Lancia", "Land Rover",
    "Lexus", "Liberty Walk", "Mazda", "McLaren", "Mercedes-AMG",
    "Mercedes-Benz", "Mitsubishi", "Nissan", "Opel", "Pagani", "PANDEM",
    "Porsche", "Renault", "RWB", "Saab", "Subaru", "Toyota", "Veilside",
    "Vertex", "Volkswagen", "Volvo",
]


def detect_make(title):
    lowered = title.lower()
    for make in KNOWN_MAKES:
        if make.lower() in lowered:
            return make
    return ""


def detect_series(title, tags):
    haystack = (title + " " + " ".join(tags)).upper()
    for tag in SERIES_TAGS:          # longest-first ordering matters (HOBBY64+)
        if tag in haystack:
            return tag
    return ""


def is_164(title, tags, variants):
    """Keep only 1/64 scale products."""
    blob = (title + " " + " ".join(tags)).lower()
    if "1/64" in blob or "1:64" in blob:
        return True
    # Explicitly exclude other scales when they're named.
    if any(s in blob for s in ("1/18", "1:18", "1/43", "1:43")):
        return False
    return False


def clean_title(raw):
    """'1/64 Pagani Utopia Green - Tarmac Works GLOBAL64' -> trimmed name."""
    name = raw
    name = re.sub(r"^\s*1\s*[:/]\s*64\s*", "", name)
    name = re.sub(r"\s*-\s*Tarmac Works.*$", "", name, flags=re.I)
    name = name.replace("&amp;", "&").strip(" -\u2013\u2014|,")
    return re.sub(r"\s+", " ", name).strip()


def extract_code(product):
    """Tarmac codes (T64G-063-WH) appear in image filenames."""
    for img in product.get("images") or []:
        src = img.get("src") or ""
        m = re.search(r"/(T\d{2}[A-Z]?-[A-Z0-9\-]+?)_", src)
        if m:
            return m.group(1)
    return ""


def fetch_page(page):
    url = f"{BASE}/collections/{COLLECTION}/products.json?limit=250&page={page}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json().get("products", [])


def main():
    releases, seen = [], set()

    for page in range(1, 10):
        try:
            products = fetch_page(page)
        except Exception as exc:
            print(f"WARN page {page} failed: {exc}", file=sys.stderr)
            break
        if not products:
            break

        kept = 0
        for p in products:
            raw_title = (p.get("title") or "").strip()
            if not raw_title:
                continue
            tags = p.get("tags") or []
            if not is_164(raw_title, tags, p.get("variants") or []):
                continue

            name = clean_title(raw_title)
            if not name or name.lower() in seen:
                continue
            seen.add(name.lower())

            images = p.get("images") or []
            image_url = images[0].get("src") if images else None
            if image_url and image_url.startswith("//"):
                image_url = "https:" + image_url

            handle = p.get("handle", "")
            releases.append({
                "modelName": name,
                "imageURL": image_url,
                "productURL": f"{BASE}/collections/{COLLECTION}/products/{handle}" if handle else None,
                "scale": "1/64",
                "series": detect_series(raw_title, tags) or extract_code(p),
                "make": detect_make(raw_title),
            })
            kept += 1

        print(f"page {page}: {len(products)} products, {kept} kept (1/64)")
        time.sleep(1)

    if not releases:
        print("ERROR no Tarmac releases scraped - leaving feed untouched.", file=sys.stderr)
        sys.exit(1)

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(releases, fh, indent=2, ensure_ascii=False)
    print(f"OK wrote {len(releases)} Tarmac Works 1/64 releases -> {OUT_PATH}")


if __name__ == "__main__":
    main()
