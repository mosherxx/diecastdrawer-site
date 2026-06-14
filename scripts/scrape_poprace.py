"""
Pop Race 1/64 scraper — uses Playwright (real headless Chrome) to bypass WAF.
Outputs: data/poprace_releases.json
"""

import json
import re
import os
from playwright.sync_api import sync_playwright

BASE_URL   = "https://www.poprace.net/product-category/1-64-scale/"
OUTPUT     = "data/poprace_releases.json"
MAX_PAGES  = 20   # safety limit


def scrape() -> list[dict]:
    releases = []
    seen     = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_4 like Mac OS X) "
                "AppleWebKit/605.1.15 (KHTML, like Gecko) "
                "Version/17.4 Mobile/15E148 Safari/604.1"
            ),
            viewport={"width": 390, "height": 844},
        )
        page = context.new_page()

        for page_num in range(1, MAX_PAGES + 1):
            url = BASE_URL if page_num == 1 else f"{BASE_URL}page/{page_num}/"
            print(f"  Fetching {url}")

            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                page.wait_for_selector(".products", timeout=15_000)
            except Exception as e:
                print(f"  Page {page_num} failed: {e}")
                break

            # Extract product cards
            products = page.query_selector_all("li.product")
            if not products:
                print(f"  No products on page {page_num}, stopping.")
                break

            for product in products:
                # Title
                title_el = product.query_selector(".woocommerce-loop-product__title")
                if not title_el:
                    title_el = product.query_selector("h2, h3")
                name = title_el.inner_text().strip() if title_el else ""
                if not name or name in seen:
                    continue
                seen.add(name)

                # Image
                img_el  = product.query_selector("img")
                img_url = None
                if img_el:
                    img_url = (
                        img_el.get_attribute("data-src")
                        or img_el.get_attribute("src")
                    )
                    # Skip tiny placeholder images
                    if img_url and ("placeholder" in img_url or img_url.endswith(".gif")):
                        img_url = None

                # Product URL
                link_el = product.query_selector("a.woocommerce-LoopProduct-link")
                prod_url = link_el.get_attribute("href") if link_el else None

                releases.append({
                    "modelName":  name,
                    "imageURL":   img_url,
                    "productURL": prod_url,
                    "scale":      "1/64",
                    "series":     detect_series(name),
                })

            # Check if there's a next page
            next_btn = page.query_selector("a.next.page-numbers")
            if not next_btn:
                print(f"  No next page after page {page_num}.")
                break

        browser.close()

    print(f"  Scraped {len(releases)} Pop Race releases.")
    return releases


def detect_series(name: str) -> str:
    """Detect series from product name."""
    name_lower = name.lower()
    if "hong kong" in name_lower:
        return "Hong Kong Edition"
    if "japan" in name_lower:
        return "Japan Edition"
    if "limited" in name_lower:
        return "Limited Edition"
    return ""


def merge_with_existing(new: list[dict]) -> list[dict]:
    """
    Merge new scrape results with existing JSON.
    Preserves manually added imageURLs and avoids removing existing items
    in case the scraper misses a page.
    """
    if not os.path.exists(OUTPUT):
        return new

    with open(OUTPUT) as f:
        existing = json.load(f)

    existing_map = {r["modelName"]: r for r in existing}

    for item in new:
        name = item["modelName"]
        if name in existing_map:
            # Keep existing imageURL if we don't have a new one
            if not item["imageURL"] and existing_map[name].get("imageURL"):
                item["imageURL"] = existing_map[name]["imageURL"]
        existing_map[name] = item

    # Sort by name for clean diffs
    result = sorted(existing_map.values(), key=lambda x: x["modelName"])
    return result


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    scraped  = scrape()
    merged   = merge_with_existing(scraped)
    with open(OUTPUT, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(merged)} items to {OUTPUT}")
