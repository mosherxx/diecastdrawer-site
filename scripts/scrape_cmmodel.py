#!/usr/bin/env python3
"""
Scrape the full CM Model 1/64 catalogue from HobbySearch (1999.co.jp).

REPLACES the Horizon Diecast CM Model source: HobbySearch carries the full
back-catalogue (released, sold-out, pre-order) with clean English names,
product IDs, prices, and status.

Future-proofing: product images are DOWNLOADED and committed into the repo at
images/cmmodel/<id>.jpg, and the feed points at the raw.githubusercontent URL
rather than hotlinking HobbySearch.

Output:
  data/cmmodel_releases.json
  images/cmmodel/<id>.jpg   (committed by the Actions workflow)
"""

import json
import os
import re
import sys
import time

import requests
from bs4 import BeautifulSoup

BASE = "https://www.1999.co.jp"
LIST_URL = "https://www.1999.co.jp/eng/list/3432/7/{page}"  # 3432 = CM Model
MAX_PAGES = 8

RAW_BASE = "https://raw.githubusercontent.com/mosherxx/diecastdrawer-site/main"
IMG_DIR = os.path.join("images", "cmmodel")
OUT_PATH = os.path.join("data", "cmmodel_releases.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; DiecastDrawerBot/1.0; "
                  "+https://github.com/mosherxx/diecastdrawer-site)"
}

KNOWN_MAKES = ["Pagani", "McLaren", "Mclaren", "Toyota", "Subaru", "Ford", "BMW",
               "Nissan", "Mitsubishi", "Dodge", "Audi", "Lancer", "Lamborghini",
               "Ferrari", "Pandem", "LB-WORKS", "DarwinPRO"]

STATUS_MAP = [("sold out", "Sold Out"), ("pre-order", "Pre-order"),
              ("restock", "Restock"), ("in stock", "In Stock")]


def detect_make(title):
    lowered = title.lower()
    for make in KNOWN_MAKES:
        if make.lower() in lowered:
            return "McLaren" if make.lower() == "mclaren" else make
    return ""


def detect_status(text):
    lowered = text.lower()
    for needle, label in STATUS_MAP:
        if needle in lowered:
            return label
    return ""


def clean_name(raw):
    name = re.sub(r"\(Diecast Car\)", "", raw, flags=re.I)
    name = re.sub(r"^\s*\*?Bargain Item\*?\s*", "", name, flags=re.I)
    name = re.sub(r"\s*-\s*(Pre-order|Sold Out|Restock|In Stock|New|Only.*|Small Packet).*$",
                  "", name, flags=re.I)
    name = name.replace("&amp;", "&").replace('"', "").strip(" -\u2013\u2014|,")
    return re.sub(r"\s+", " ", name).strip()


def download_image(url, product_id):
    if not url:
        return None
    if url.startswith("//"):
        url = "https:" + url
    os.makedirs(IMG_DIR, exist_ok=True)
    path = os.path.join(IMG_DIR, f"{product_id}.jpg")
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return f"{RAW_BASE}/{IMG_DIR}/{product_id}.jpg"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        if not r.content or len(r.content) < 500:
            return None
        with open(path, "wb") as fh:
            fh.write(r.content)
        return f"{RAW_BASE}/{IMG_DIR}/{product_id}.jpg"
    except Exception as exc:
        print(f"WARN image {product_id} failed: {exc}", file=sys.stderr)
        return None


def scrape_page(page):
    resp = requests.get(LIST_URL.format(page=page), headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    found = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = re.match(r"^https?://www\.1999\.co\.jp/eng/(\d{6,})", href) \
            or re.match(r"^/eng/(\d{6,})$", href)
        if not m:
            continue
        pid = m.group(1)
        text = a.get_text(" ", strip=True)
        if "(Diecast Car)" not in text:
            continue
        name = clean_name(text)
        if not name or len(name) < 4:
            continue
        img = a.find("img")
        img_src = (img.get("data-src") or img.get("src")) if img else None
        found.append({"pid": pid, "name": name, "status": detect_status(text),
                      "img_src": img_src, "product_url": f"{BASE}/eng/{pid}"})
    return found


def main():
    seen, raw_items = set(), []
    for page in range(1, MAX_PAGES + 1):
        try:
            page_items = scrape_page(page)
        except Exception as exc:
            print(f"WARN page {page} failed: {exc}", file=sys.stderr)
            break
        if not page_items:
            break
        new = 0
        for it in page_items:
            if it["pid"] in seen:
                continue
            seen.add(it["pid"])
            raw_items.append(it)
            new += 1
        print(f"page {page}: {new} new items")
        if new == 0:
            break
        time.sleep(1.5)

    if not raw_items:
        print("ERROR no CM Model items scraped - leaving feed untouched.", file=sys.stderr)
        sys.exit(1)

    releases = []
    for it in raw_items:
        archived = download_image(it["img_src"], it["pid"])
        releases.append({
            "modelName": it["name"],
            "imageURL": archived,
            "productURL": it["product_url"],
            "scale": "1/64",
            "series": it["status"],
            "make": detect_make(it["name"]),
        })
    releases.sort(key=lambda e: e["modelName"].lower())

    os.makedirs("data", exist_ok=True)
    with open(OUT_PATH, "w", encoding="utf-8") as fh:
        json.dump(releases, fh, indent=2, ensure_ascii=False)
    with_img = sum(1 for r in releases if r["imageURL"])
    print(f"OK wrote {len(releases)} CM Model releases ({with_img} images) -> {OUT_PATH}")


if __name__ == "__main__":
    main()
