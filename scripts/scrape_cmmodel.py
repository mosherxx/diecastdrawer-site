#!/usr/bin/env python3
"""
Scrape CM Model 1:64 releases from Horizon Diecast (Shopify store).

Uses Shopify's public products.json endpoint rather than parsing HTML — it
returns clean structured data, is far more stable than scraping markup, and
paginates reliably.

Output: data/cmmodel_releases.json
Schema matches the other feeds: modelName, imageURL, productURL, scale, series, make
"""

import json
import os
import re
import sys
import time

import requests

BASE = "https://horizondiecast.com"
COLLECTION = "all-cm-models"
OUT_PATH = os.path.join("data", "cmmodel_releases.json")

HEADERS = {
    "User-Agent": "DiecastDrawerBot/1.0 (+https://github.com/mosherxx/diecastdrawer-site)"
}

# Vehicle makes we can detect in product titles, for the `make` field.
KNOWN_MAKES = [
    "Pagani", "McLaren", "Toyota", "Subaru", "Ford", "BMW", "Nissan",
    "Mitsubishi", "Dodge", "MV Agusta",
]


def clean_title(raw: str) -> str:
    """Turn a shop listing title into a clean model name."""
    name = raw
    # Drop the shop's status prefixes: "[Preorder] ", "[Pre-order] "
    name = re.sub(r"^\s*\[[^\]]*\]\s*", "", name)
    # Drop leading brand + scale, e.g. "CM Model 1:64 "
    name = re.sub(r"^\s*CM\s*Model\s*1[:/]64\s*", "", name, flags=re.I)
    # Normalise whitespace and stray quotes
    name = name.replace('"', "").strip()
    name = re.sub(r"\s+", " ", name)
    return name


def detect_make(title: str) -> str:
    lowered = title.lower()
    for make in KNOWN_MAKES:
        if make.lower() in lowered:
            return make
    return ""


def extract_code(title: str) -> str:
    """Pull the CM product code, e.g. CM64-ZondaF-04, from the title."""
    m = re.search(r"\bCM64[-\w]*\b", title, flags=re.I)
    return m.group(0) if m else ""


def fetch_page(page: int):
    url = f"{BASE}/collections/{COLLECTION}/products.json?limit=250&page={page}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json().get("products", [])


def main():
    releases = []
    seen = set()

    for page in range(1, 12):  # generous upper bound; we break when empty
        try:
            products = fetch_page(page)
        except Exception as exc:  # noqa: BLE001
            print(f"⚠️  page {page} failed: {exc}", file=sys.stderr)
            break

        if not products:
            break

        print(f"📄 page {page}: {len(products)} products")

        for p in products:
            raw_title = p.get("title", "").strip()
            if not raw_title:
                continue

            model_name = clean_title(raw_title)
            if not model_name or model_name.lower() in seen:
                continue
            seen.add(model_name.lower())

            images = p.get("images") or []
            image_url = images[0].get("src") if images else None
            # Shopify serves protocol-relative or CDN URLs; normalise to https
            if image_url and image_url.startswith("//"):
                image_url = "https:" + image_url

            handle = p.get("handle", "")
            product_url = f"{BASE}/products/{handle}" if handle else None

            releases.append(
                {
                    "modelName": model_name,
                    "imageURL": image_url,
                    "productURL": product_url,
                    "scale": "1/64",
                    "series": extract_code(raw_title),
                    "make": detect_make(raw_title),
                }
            )

        time.sleep(1)  # be polite to a small shop's server

    if not releases:
        print("❌ No releases scraped — leaving existing feed untouched.", file=sys.stderr)
        sys.exit(1)

    # Newest first (Shopify returns newest-first already, but be explicit).
    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(releases, fh, indent=2, ensure_ascii=False)

    print(f"✅ Wrote {len(releases)} CM Model releases → {OUT_PATH}")


if __name__ == "__main__":
    main()
