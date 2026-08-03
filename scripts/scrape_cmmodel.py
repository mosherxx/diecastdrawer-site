#!/usr/bin/env python3
"""
Scrape CM Model 1:64 releases from Horizon Diecast (Shopify store).

Uses Shopify's public products.json endpoint rather than parsing HTML — it
returns clean structured data, is stable, and paginates reliably.

NOTE: this covers Horizon's CM Model listings (newer / in-stock / upcoming),
not the full back-catalogue. The HobbySearch (1999.co.jp) full-catalogue source
was dropped because it 403-blocks GitHub Actions' datacenter IPs.

Output: data/cmmodel_releases.json
Schema: modelName, imageURL, productURL, scale, series, make
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

KNOWN_MAKES = ["Pagani", "McLaren", "Mclaren", "Toyota", "Subaru", "Ford", "BMW",
               "Nissan", "Mitsubishi", "Dodge", "MV Agusta", "Lamborghini",
               "Ferrari"]


def clean_title(raw):
    name = re.sub(r"^\s*\[[^\]]*\]\s*", "", raw)                 # [Preorder]
    name = re.sub(r"^\s*CM\s*Model\s*1[:/]64\s*", "", name, flags=re.I)
    name = name.replace('"', "").strip()
    return re.sub(r"\s+", " ", name)


def detect_make(title):
    lowered = title.lower()
    for make in KNOWN_MAKES:
        if make.lower() in lowered:
            return "McLaren" if make.lower() == "mclaren" else make
    return ""


def extract_code(title):
    m = re.search(r"\bCM64[-\w]*\b", title, flags=re.I)
    return m.group(0) if m else ""


def fetch_page(page):
    url = f"{BASE}/collections/{COLLECTION}/products.json?limit=250&page={page}"
    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.json().get("products", [])


def main():
    releases, seen = [], set()

    for page in range(1, 12):
        try:
            products = fetch_page(page)
        except Exception as exc:
            print(f"WARN page {page} failed: {exc}", file=sys.stderr)
            break
        if not products:
            break
        print(f"page {page}: {len(products)} products")

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
            if image_url and image_url.startswith("//"):
                image_url = "https:" + image_url

            handle = p.get("handle", "")
            product_url = f"{BASE}/products/{handle}" if handle else None

            releases.append({
                "modelName": model_name,
                "imageURL": image_url,
                "productURL": product_url,
                "scale": "1/64",
                "series": extract_code(raw_title),
                "make": detect_make(raw_title),
            })

        time.sleep(1)

    if not releases:
        print("ERROR no releases scraped - leaving existing feed untouched.", file=sys.stderr)
        sys.exit(1)

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(releases, fh, indent=2, ensure_ascii=False)
    print(f"OK wrote {len(releases)} CM Model releases -> {OUT_PATH}")


if __name__ == "__main__":
    main()
