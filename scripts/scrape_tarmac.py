"""
Tarmac Works 1/64 scraper — Shopify storefront.

Tarmac migrated to Shopify, which exposes a public, auth-free products feed at
    /products.json?limit=250&page=N
This is stable and clean (no HTML rendering, no WooCommerce). We pull all
products, keep the 1/64 ones, and extract the Tarmac product code.

If the endpoint is unreachable, the existing feed is preserved (never wiped).

Outputs: data/tarmac_releases.json
"""

import json
import os
import re
import sys
import time
from typing import Optional

import requests

SITE      = "https://www.tarmacworks.com"
OUTPUT    = "data/tarmac_releases.json"
PER_PAGE  = 250          # Shopify max
MAX_PAGES = 40

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def fetch_page(page: int) -> Optional[list]:
    url = f"{SITE}/products.json"
    for attempt in range(1, 4):
        try:
            r = requests.get(url, headers=HEADERS,
                             params={"limit": PER_PAGE, "page": page}, timeout=30)
            if r.status_code == 200:
                try:
                    return r.json().get("products", [])
                except ValueError:
                    print(f"  non-JSON response (page {page})", flush=True)
                    return None
            body = (r.text or "")[:160].replace("\n", " ")
            print(f"  HTTP {r.status_code} (page {page}, attempt {attempt}): {body}", flush=True)
        except requests.RequestException as e:
            print(f"  request error (page {page}, attempt {attempt}): {e}", flush=True)
        time.sleep(2 * attempt)
    return None


def extract_code(text: str) -> str:
    """Tarmac code like T64-030-CW or T64G-TF078-CS."""
    m = re.search(r"\bT\d{2}[A-Z]?(?:G)?-[A-Z0-9]+-\d+\b", text or "")
    return m.group(0) if m else ""


def is_164(prod: dict) -> bool:
    """Keep 1/64 items. Tarmac tags scale in product_type/tags/title."""
    tags = prod.get("tags", [])
    if isinstance(tags, list):
        tags_str = " ".join(tags)
    else:
        tags_str = str(tags)
    hay = " ".join([
        prod.get("product_type", "") or "",
        prod.get("title", "") or "",
        tags_str,
    ]).lower()
    return bool(re.search(r"1[\s:/\-]?64", hay))


def best_image(prod: dict) -> Optional[str]:
    imgs = prod.get("images") or []
    if imgs:
        return imgs[0].get("src")
    return None


def first_sku(prod: dict) -> str:
    for v in prod.get("variants", []) or []:
        if v.get("sku"):
            return v["sku"]
    return ""


def scrape() -> list[dict]:
    releases, seen = [], set()
    any_data = False
    for page in range(1, MAX_PAGES + 1):
        products = fetch_page(page)
        if products is None:
            break
        any_data = True
        if not products:
            break
        kept_before = len(releases)
        for prod in products:
            if not is_164(prod):
                continue
            name = (prod.get("title") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            code = extract_code(name) or extract_code(first_sku(prod))
            releases.append({
                "modelName":   name,
                "imageURL":    best_image(prod),
                "productURL":  f"{SITE}/products/{prod.get('handle','')}",
                "productCode": code,
                "scale":       "1/64",
            })
        print(f"  page {page}: {len(products)} products, "
              f"{len(releases) - kept_before} were 1/64 ({len(releases)} total)", flush=True)
        if len(products) < PER_PAGE:
            break
        time.sleep(0.4)

    if not any_data:
        print("  products.json unreachable — keeping existing feed.", flush=True)
        return []
    print(f"  Scraped {len(releases)} Tarmac Works 1/64 releases.", flush=True)
    return releases


def merge_with_existing(new: list[dict]) -> list[dict]:
    if not os.path.exists(OUTPUT):
        return new
    with open(OUTPUT) as f:
        existing = json.load(f)
    existing_map = {r["modelName"]: r for r in existing}
    for item in new:
        name = item["modelName"]
        if name in existing_map:
            if not item.get("imageURL") and existing_map[name].get("imageURL"):
                item["imageURL"] = existing_map[name]["imageURL"]
            if not item.get("productCode") and existing_map[name].get("productCode"):
                item["productCode"] = existing_map[name]["productCode"]
        existing_map[name] = item
    return sorted(existing_map.values(), key=lambda x: x["modelName"])


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    scraped = scrape()
    if not scraped:
        print("  No new data scraped — leaving existing feed untouched.", flush=True)
        sys.exit(0)
    merged = merge_with_existing(scraped)
    with open(OUTPUT, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(merged)} items to {OUTPUT}", flush=True)
