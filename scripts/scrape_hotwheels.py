"""
Hot Wheels scraper — uses the Fandom MediaWiki API (api.php) instead of
fetching rendered HTML pages, which Fandom 403s for scripts.

The API endpoint returns the page's parsed HTML inside JSON:
  https://hotwheels.fandom.com/api.php?action=parse&page=...&format=json&prop=text

Outputs: data/hotwheels_releases.json

IMAGE GOTCHA: thumbnails are lazy-loaded (data:image/gif placeholders), so the
real full-res URL lives in the anchor <a href> wrapping each thumbnail. We
extract from the href and strip the scale-to-width-down thumbnail segment.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime

CURRENT_YEAR = datetime.now().year
import requests
from bs4 import BeautifulSoup

YEARS  = list(range(2018, 2027))
OUTPUT = "data/hotwheels_releases.json"
API    = "https://hotwheels.fandom.com/api.php"

HEADERS = {
    "User-Agent": "DiecastDrawer/1.0 (collector app; contact: hello@diecastdrawer.app)",
    "Accept": "application/json",
}

IMG_HOST = "static.wikia.nocookie.net"

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def normalize(url: str) -> str:
    return re.sub(r"/revision/latest/scale-to-width-down/\d+", "/revision/latest", url)


def best_image_from_cell(cell):
    for a in cell.find_all("a", href=True):
        href = a["href"]
        if IMG_HOST in href and "/revision/latest" in href:
            return normalize(href)
    for img in cell.find_all("img"):
        for attr in ("src", "data-src"):
            val = img.get(attr, "")
            if val.startswith("http") and IMG_HOST in val:
                return normalize(val)
    return None


def fetch_page_html(page_title: str):
    params = {
        "action": "parse",
        "page":   page_title,
        "format": "json",
        "prop":   "text",
        "formatversion": "2",
    }
    try:
        resp = SESSION.get(API, params=params, timeout=30)
        if resp.status_code != 200:
            print(f"    API HTTP {resp.status_code}")
            return None
        data = resp.json()
    except Exception as e:
        print(f"    API error: {e}")
        return None

    if "error" in data:
        print(f"    API error: {data['error'].get('info', 'unknown')}")
        return None
    try:
        return data["parse"]["text"]
    except (KeyError, TypeError):
        return None


def parse_year(year: int) -> list:
    page = f"List_of_{year}_Hot_Wheels"
    print(f"  Fetching (API) {page}")
    html = fetch_page_html(page)
    if not html:
        print(f"    {year}: no content")
        return []

    soup  = BeautifulSoup(html, "html.parser")
    items = []

    for table in soup.find_all("table"):
        headers = [clean_text(th.get_text()).lower() for th in table.find_all("th")]
        if not headers or not any("model" in h for h in headers):
            continue

        def col_index(keyword):
            for i, h in enumerate(headers):
                if keyword in h:
                    return i
            return None

        idx_toy    = col_index("toy")
        idx_col    = col_index("col")
        idx_name   = col_index("model")
        idx_series = col_index("series")
        idx_photo  = col_index("photo")

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells or len(cells) < 3:
                continue

            def cell(i):
                return cells[i] if (i is not None and i < len(cells)) else None

            name_cell = cell(idx_name)
            if not name_cell:
                continue
            name = clean_text(name_cell.get_text())
            if not name:
                continue

            toy_no    = clean_text(cell(idx_toy).get_text())    if cell(idx_toy)    else ""
            col_no    = clean_text(cell(idx_col).get_text())    if cell(idx_col)    else ""
            series    = clean_text(cell(idx_series).get_text()) if cell(idx_series) else ""
            photo_url = best_image_from_cell(cell(idx_photo))   if cell(idx_photo)  else None

            category = "Mainline"
            stext = series.lower()
            if "super treasure hunt" in stext:
                category = "Super Treasure Hunt"
            elif "treasure hunt" in stext:
                category = "Treasure Hunt"
            elif "exclusive" in stext:
                category = "Premium"

            # Detect upcoming/unreleased items so the app can skip pricing them.
            # We scan the whole row's text plus the model year vs. current year.
            row_text = name_cell.get_text(" ").lower() + " " + stext
            unreleased_markers = [
                "pre-order", "pre order", "preorder", "coming soon", "upcoming",
                "tba", "to be released", "not yet released", "unreleased",
                "release date", "expected",
            ]
            is_released = True
            if any(m in row_text for m in unreleased_markers):
                is_released = False
            # Future model years are not out yet.
            if year and year > CURRENT_YEAR:
                is_released = False

            items.append({
                "modelName": name,
                "imageURL":  photo_url,
                "toyNumber": toy_no,
                "colNumber": col_no,
                "series":    series,
                "category":  category,
                "year":      year,
                "isReleased": is_released,
            })

    print(f"    {year}: {len(items)} items")
    return items


def dedupe(items: list) -> list:
    seen, out = set(), []
    for r in items:
        k = (r.get("toyNumber") or r["modelName"], r.get("year"))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return sorted(out, key=lambda x: (-(x.get("year") or 0),
                                      x.get("colNumber") or "",
                                      x["modelName"]))


def merge_with_existing(new: list) -> list:
    if not os.path.exists(OUTPUT):
        return dedupe(new)
    with open(OUTPUT) as f:
        existing = json.load(f)

    def key(r):
        return (r.get("toyNumber") or r["modelName"], r.get("year"))

    merged = {key(r): r for r in existing}
    for item in new:
        k = key(item)
        if k in merged and not item["imageURL"] and merged[k].get("imageURL"):
            item["imageURL"] = merged[k]["imageURL"]
        merged[k] = item
    return dedupe(list(merged.values()))


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    all_items = []
    for yr in YEARS:
        all_items.extend(parse_year(yr))
        time.sleep(1)
    merged = merge_with_existing(all_items)
    with_img = sum(1 for x in merged if x.get("imageURL"))
    with open(OUTPUT, "w") as f:
        json.dump(merged, f, indent=2, ensure_ascii=False)
    print(f"  Saved {len(merged)} Hot Wheels ({with_img} with images) to {OUTPUT}")
