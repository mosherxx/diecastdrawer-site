"""
Tarmac Works 1/64 scraper — uses Playwright (real headless Chrome) to bypass WAF.
Outputs: data/tarmac_releases.json
"""

import json
import re
import os
from playwright.sync_api import sync_playwright

BASE_URL  = "https://www.tarmacworks.com/product-category/1-64/"
OUTPUT    = "data/tarmac_releases.json"
MAX_PAGES = 20


def scrape() -> list[dict]:
    releases = []
    seen     = set()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
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
                    if img_url and ("placeholder" in img_url or img_url.endswith(".gif")):
                        img_url = None

                # Product URL
                link_el  = product.query_selector("a.woocommerce-LoopProduct-link")
                prod_url = link_el.get_attribute("href") if link_el else None

                # Product code from name (T64-xxx or T64G-xxx)
                code = extract_code(name)

                releases.append({
                    "modelName":   name,
                    "imageURL":    img_url,
                    "productURL":  prod_url,
                    "productCode": code,
                    "scale":       "1/64",
                })

            next_btn = page.query_selector("a.next.page-numbers")
            if not next_btn:
                print(f"  No next page after page {page_num}.")
                break

        browser.close()

    print(f"  Scraped {len(releases)} Tarmac Works releases.")
    return releases


def extract_code(name: str) -> str:
    """Extract Tarmac product code like T64-030-CW or T64G-TF078-CS."""
    match = re.search(r"\bT\d{2}[A-Z]?(?:G)?-[A-Z0-9]+-\d+\b", name)
    return match.group(0) if match else ""


def merge_with_existing(new: list[dict]) -> list[dict]:
    if not os.path.exists(OUTPUT):
        return new

    with open(OUTPUT) as f:
        existing = json.load(f)

    existing_map = {r["modelName"]: r for r in existing}

    for item in new:
        name = item["modelName"]
        if name in existing_map:
            if not item["imageURL"] and existing_map[name].get("imageURL"):
                item["imageURL"] = existing_map[name]["imageURL"]
            if not item["productCode"] and existing_map[name].get("productCode"):
                item["productCode"] = existing_map[name]["productCode"]
        existing_map[name] = item

    return sorted(existing_map.values(), key=lambda x: x["modelName"])


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    scraped = scrape()
    merged  = merge_with_existing(scraped)
    with open(OUTPUT, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(merged)} items to {OUTPUT}")
