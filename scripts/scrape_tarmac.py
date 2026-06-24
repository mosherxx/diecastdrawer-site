"""
Tarmac Works 1/64 scraper.

Uses the WooCommerce Store API (clean JSON) instead of Playwright. See
scrape_poprace.py for the rationale. Keeps Tarmac's product-code extraction
(T64-xxx / T64G-xxx).

If the Store API is unreachable, the existing feed is preserved rather than
wiped, so a temporary outage never empties the app's Tarmac list.

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
PER_PAGE  = 100
MAX_PAGES = 30
CATEGORY_SLUG = "1-64"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

API_PATHS = [
    "/wp-json/wc/store/v1/products",
    "/wp-json/wc/store/products",
]


def fetch_json(url: str, params: dict) -> Optional[list]:
    for attempt in range(1, 4):
        try:
            r = requests.get(url, headers=HEADERS, params=params, timeout=30)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    print(f"  non-JSON response from {url}", flush=True)
                    return None
            print(f"  HTTP {r.status_code} from {url} (attempt {attempt})", flush=True)
        except requests.RequestException as e:
            print(f"  request error (attempt {attempt}): {e}", flush=True)
        time.sleep(2 * attempt)
    return None


def resolve_category_id(api_base: str) -> Optional[int]:
    data = fetch_json(api_base.replace("/products", "/products/categories"),
                      {"per_page": 100})
    if not isinstance(data, list):
        return None
    for cat in data:
        if cat.get("slug") == CATEGORY_SLUG:
            return cat.get("id")
    for cat in data:
        if "64" in str(cat.get("slug", "")) or "64" in str(cat.get("name", "")):
            return cat.get("id")
    return None


def clean_name(raw: str) -> str:
    txt = re.sub(r"<[^>]+>", "", raw or "")
    return (txt.replace("&amp;", "&").replace("&#8211;", "–")
               .replace("&#8217;", "’").replace("&quot;", '"').strip())


def best_image(prod: dict) -> Optional[str]:
    imgs = prod.get("images") or []
    if imgs:
        src = imgs[0].get("src")
        if src and "placeholder" not in src.lower():
            return src
    return None


def extract_code(name: str, sku: str = "") -> str:
    """Tarmac code like T64-030-CW or T64G-TF078-CS. Prefer the SKU if present."""
    for candidate in (sku, name):
        m = re.search(r"\bT\d{2}[A-Z]?(?:G)?-[A-Z0-9]+-\d+\b", candidate or "")
        if m:
            return m.group(0)
    return ""


def scrape() -> list[dict]:
    api_base = None
    for path in API_PATHS:
        test = fetch_json(SITE + path, {"per_page": 1})
        if isinstance(test, list):
            api_base = SITE + path
            print(f"  Using Store API: {api_base}", flush=True)
            break
    if not api_base:
        print("  Store API not reachable — keeping existing feed.", flush=True)
        return []

    cat_id = resolve_category_id(api_base)
    if cat_id:
        print(f"  1/64 category id = {cat_id}", flush=True)
    else:
        print("  Could not resolve 1/64 category; scraping all products.", flush=True)

    releases, seen = [], set()
    for page in range(1, MAX_PAGES + 1):
        params = {"per_page": PER_PAGE, "page": page}
        if cat_id:
            params["category"] = cat_id
        data = fetch_json(api_base, params)
        if not isinstance(data, list) or not data:
            break
        for prod in data:
            name = clean_name(prod.get("name", ""))
            if not name or name in seen:
                continue
            seen.add(name)
            sku = prod.get("sku", "") or ""
            releases.append({
                "modelName":   name,
                "imageURL":    best_image(prod),
                "productURL":  prod.get("permalink"),
                "productCode": extract_code(name, sku),
                "scale":       "1/64",
            })
        print(f"  page {page}: {len(data)} products ({len(releases)} kept)", flush=True)
        if len(data) < PER_PAGE:
            break
        time.sleep(0.5)

    print(f"  Scraped {len(releases)} Tarmac Works releases.", flush=True)
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
