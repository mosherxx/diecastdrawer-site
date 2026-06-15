"""
Matchbox scraper — uses the Fandom MediaWiki API (api.php), same approach as
the Hot Wheels scraper (HTML pages 403 for scripts; the API does not).

Outputs: data/matchbox_releases.json

Matchbox year-list pages: List_of_<year>_Matchbox
Columns vary by year but generally: MBX#/Toy# | Model Name | Series | Photo.
We key on the "model" and "photo" header names so column order doesn't matter.
"""

from __future__ import annotations

import json
import os
import re
import time
import requests
from bs4 import BeautifulSoup

YEARS  = list(range(2018, 2027))
OUTPUT = "data/matchbox_releases.json"
API    = "https://matchbox.fandom.com/api.php"

HEADERS = {
    "User-Agent": "DiecastDrawer/1.0 (collector app; contact: hello@diecastdrawer.app)",
    "Accept": "application/json",
}

IMG_HOST     = "static.wikia.nocookie.net"
PLACEHOLDERS = ("image_not_available", "no_image", "noimage", "placeholder")

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip()


def normalize(url: str) -> str:
    return re.sub(r"/revision/latest/scale-to-width-down/\d+", "/revision/latest", url)


def _is_placeholder(url: str) -> bool:
    u = url.lower()
    return any(p in u for p in PLACEHOLDERS)


def best_image_from_cell(cell):
    for a in cell.find_all("a", href=True):
        href = a["href"]
        if IMG_HOST in href and "/revision/latest" in href and not _is_placeholder(href):
            return normalize(href)
    for img in cell.find_all("img"):
        for attr in ("src", "data-src"):
            val = img.get(attr, "")
            if val.startswith("http") and IMG_HOST in val and not _is_placeholder(val):
                return normalize(val)
    return None


def fetch_page_html(page_title: str):
    params = {
        "action": "parse", "page": page_title,
        "format": "json", "prop": "text", "formatversion": "2",
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
    page = f"List_of_{year}_Matchbox"
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

        def col_index(*keywords):
            for i, h in enumerate(headers):
                if any(k in h for k in keywords):
                    return i
            return None

        # IMPORTANT: a column headed "Model #" / "Model No." contains the word
        # "model" but holds the NUMBER, not the name. Find the name column as the
        # one containing "model" or "name" but NOT a number marker (#, no, no.).
        def name_col_index():
            # Prefer an explicit "model name" / "name" header.
            for i, h in enumerate(headers):
                if "name" in h:
                    return i
            # Otherwise a "model" header that is NOT a number column.
            for i, h in enumerate(headers):
                if "model" in h and not any(n in h for n in ("#", "no.", "no ", " no", "num")):
                    return i
            return None

        idx_no     = col_index("mbx", "toy", "casting #", "model #", "no.", "#")
        idx_name   = name_col_index()
        idx_series = col_index("series", "sub-series", "subseries", "collection")
        idx_photo  = col_index("photo", "image", "picture")

        # Fallback: if header matching didn't find the name column, detect it by
        # CONTENT — the model-name column is the one whose cells link to model
        # pages (real names are wiki-linked; MB numbers are plain text). Scan the
        # first few data rows and pick the column with the most <a> links to
        # /wiki/ pages that aren't images.
        if idx_name is None:
            body_rows = [r for r in table.find_all("tr") if r.find_all("td")][:8]
            link_counts: dict[int, int] = {}
            for r in body_rows:
                for ci, td in enumerate(r.find_all("td")):
                    for a in td.find_all("a", href=True):
                        href = a["href"]
                        if "/wiki/" in href and IMG_HOST not in href and a.get_text(strip=True):
                            link_counts[ci] = link_counts.get(ci, 0) + 1
            if link_counts:
                idx_name = max(link_counts, key=link_counts.get)

        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if not cells or len(cells) < 2:
                continue

            def cell(i):
                return cells[i] if (i is not None and i < len(cells)) else None

            name_cell = cell(idx_name)
            if not name_cell:
                continue
            name = clean_text(name_cell.get_text())
            if not name:
                continue

            mbx_no    = clean_text(cell(idx_no).get_text())     if cell(idx_no)     else ""
            series    = clean_text(cell(idx_series).get_text()) if cell(idx_series) else ""
            photo_url = best_image_from_cell(cell(idx_photo))   if cell(idx_photo)  else None

            category = "Mainline"
            stext = series.lower()
            if "super chase" in stext:
                category = "Super Chase"
            elif "collectors" in stext or "premium" in stext:
                category = "Collectors"
            elif "moving parts" in stext:
                category = "Moving Parts"

            items.append({
                "modelName": name,
                "imageURL":  photo_url,
                "mbxNumber": mbx_no,
                "series":    series,
                "category":  category,
                "year":      year,
            })

    print(f"    {year}: {len(items)} items")
    return items


def dedupe(items: list) -> list:
    seen, out = set(), []
    for r in items:
        k = (r.get("mbxNumber") or r["modelName"], r.get("year"))
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
    return sorted(out, key=lambda x: (-(x.get("year") or 0), x["modelName"]))


def merge_with_existing(new: list) -> list:
    if not os.path.exists(OUTPUT):
        return dedupe(new)
    with open(OUTPUT) as f:
        existing = json.load(f)

    def key(r):
        return (r.get("mbxNumber") or r["modelName"], r.get("year"))

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
    print(f"  Saved {len(merged)} Matchbox ({with_img} with images) to {OUTPUT}")
