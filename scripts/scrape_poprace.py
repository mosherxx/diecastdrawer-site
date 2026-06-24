"""
Pop Race scraper — Fandom wiki via the MediaWiki API.

Pulls every Pop Race collection page (Regular, Enigma, Event Exclusives, Dark
Chrome, TS Exclusives, Blind Box, Xcartoys China) as wikitext, parses the
release tables, and resolves Fandom image filenames to real image URLs through
the API. No HTML rendering, no Playwright — same approach as the working Hot
Wheels / Matchbox scrapers.

Each item is tagged with the collection(s) it appears in (`marques`), like the
MiniGT special-collections scraper.

If the API is unreachable, the existing feed is preserved (never wiped).

Outputs: data/poprace_releases.json
"""

import json
import os
import re
import sys
import time
from typing import Optional

import requests

API     = "https://pop-race.fandom.com/api.php"
OUTPUT  = "data/poprace_releases.json"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}

# Wiki page → collection label.
PAGES = {
    "Regular_Collection": "Regular Collection",
    "Enigma":             "Enigma",
    "Event_Exclusives":   "Event Exclusives",
    "Dark_Chrome_Series": "Dark Chrome Series",
    "TS_Exclusives":      "TS Exclusives",
    "Blind_Box_Series":   "Blind Box Series",
    "Xcartoys_China":     "Xcartoys China",
}


def api_get(params: dict) -> Optional[dict]:
    p = {"format": "json", **params}
    for attempt in range(1, 4):
        try:
            r = requests.get(API, headers=HEADERS, params=p, timeout=30)
            if r.status_code == 200:
                try:
                    return r.json()
                except ValueError:
                    print(f"  non-JSON from API ({params.get('page','')})", flush=True)
                    return None
            print(f"  HTTP {r.status_code} ({params.get('page','')}, attempt {attempt})", flush=True)
        except requests.RequestException as e:
            print(f"  request error (attempt {attempt}): {e}", flush=True)
        time.sleep(2 * attempt)
    return None


def get_wikitext(page: str) -> Optional[str]:
    data = api_get({"action": "parse", "page": page, "prop": "wikitext"})
    if not data:
        return None
    try:
        return data["parse"]["wikitext"]["*"]
    except (KeyError, TypeError):
        return None


def clean_link(text: str) -> str:
    """Turn '[[A|B]]' -> 'B', '[[A]]' -> 'A', strip wiki/HTML noise."""
    if text is None:
        return ""
    t = text.strip()
    # [[Target|Display]] or [[Target]]
    def repl(m):
        inner = m.group(1)
        return inner.split("|")[-1] if "|" in inner else inner
    t = re.sub(r"\[\[([^\]]+)\]\]", repl, t)
    t = re.sub(r"<br\s*/?>", " ", t, flags=re.IGNORECASE)
    t = re.sub(r"<[^>]+>", "", t)
    t = t.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", t).strip()


def extract_image_filename(cell: str) -> Optional[str]:
    """From '[[File:NAME.jpg|thumb|center]]' -> 'NAME.jpg'."""
    m = re.search(r"\[\[\s*File:\s*([^\|\]]+)", cell, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return None


def resolve_image_urls(filenames: list) -> dict:
    """Batch-resolve File: names to URLs via imageinfo. Returns {name: url}."""
    out = {}
    # The API accepts up to 50 titles per imageinfo call.
    for i in range(0, len(filenames), 50):
        chunk = filenames[i:i + 50]
        titles = "|".join(f"File:{n}" for n in chunk)
        data = api_get({"action": "query", "titles": titles,
                        "prop": "imageinfo", "iiprop": "url"})
        if not data:
            continue
        pages = (data.get("query") or {}).get("pages") or {}
        for _, page in pages.items():
            title = (page.get("title") or "").replace("File:", "")
            info = page.get("imageinfo")
            if info:
                out[title] = info[0].get("url")
        time.sleep(0.3)
    return out


def is_pop_race(item: dict) -> bool:
    """Keep only actual Pop Race products. The wiki pages mix in other makers
    (BM Creations, INNO64-only items, etc.). Real Pop Race codes start with
    'PR64'; we also keep anything whose manufacturer/photo clearly says Pop Race.
    """
    code = (item.get("productCode") or "").upper()
    if code.startswith("PR64"):
        return True
    # Some items have no PR64 code but are tagged Pop Race in the make/name.
    blob = " ".join([item.get("make", ""), item.get("modelName", "")]).lower()
    return "pop race" in blob or "pop-race" in blob


def parse_table(wikitext: str, collection: str) -> list:
    """Parse the release rows from a collection page's wikitext."""
    items = []
    # Rows are separated by '|-'. Each cell line starts with '|' (or '| align=').
    rows = re.split(r"\n\|-", wikitext)
    for row in rows:
        # Cells: lines that begin with '|' but not header '!' or table markup.
        cells = []
        for line in row.split("\n"):
            line = line.strip()
            if not line.startswith("|"):
                continue
            if line.startswith("|+") or line.startswith("|}") or line.startswith("|-"):
                continue
            # Strip a leading '| align="left" |' style prefix.
            val = re.sub(r'^\|\s*(align="[^"]*"\s*\|)?', "", line).strip()
            cells.append(val)
        # Expected layout: [Model#, Model, Description, Make, Release, Manufacturer, Photo]
        if len(cells) < 7:
            continue
        code  = clean_link(cells[0])
        name  = clean_link(cells[1])
        make  = clean_link(cells[3])
        photo = extract_image_filename(cells[6])
        if not name:
            continue
        items.append({
            "modelName":    name,
            "productCode":  code,
            "make":         make,
            "_imageFile":   photo,   # resolved to imageURL later
            "marques":      [collection],
        })
    return items


def scrape() -> list:
    by_key = {}          # key (code or name) -> merged item
    all_files = set()
    any_ok = False

    for page, label in PAGES.items():
        print(f"Fetching Pop Race collection: {label} ({page})…", flush=True)
        wt = get_wikitext(page)
        if wt is None:
            print(f"  [{label}] could not fetch — skipping.", flush=True)
            continue
        any_ok = True
        rows = parse_table(wt, label)
        rows = [r for r in rows if is_pop_race(r)]
        print(f"  [{label}] parsed {len(rows)} Pop Race items.", flush=True)
        for it in rows:
            key = (it["productCode"] or it["modelName"]).upper()
            if key in by_key:
                # Merge collection tags; keep first image/code.
                existing = by_key[key]
                for m in it["marques"]:
                    if m not in existing["marques"]:
                        existing["marques"].append(m)
                if not existing.get("_imageFile") and it.get("_imageFile"):
                    existing["_imageFile"] = it["_imageFile"]
            else:
                by_key[key] = it
            if it.get("_imageFile"):
                all_files.add(it["_imageFile"])
        time.sleep(0.4)

    if not any_ok:
        print("  Wiki API unreachable — keeping existing feed.", flush=True)
        return []

    # Resolve all image filenames to URLs in batches.
    print(f"Resolving {len(all_files)} image URLs…", flush=True)
    url_map = resolve_image_urls(sorted(all_files))

    releases = []
    for it in by_key.values():
        fn = it.pop("_imageFile", None)
        it["imageURL"] = url_map.get(fn) if fn else None
        it["scale"] = "1/64"
        # The app reads/filters/searches a single `series` string. Use the
        # primary (first) collection so the collection surfaces in the existing
        # UI; keep the full `marques` array too for future multi-tag filtering.
        it["series"] = it["marques"][0] if it.get("marques") else ""
        # Build a productURL-less item; the app's PopRaceRelease ignores extra keys.
        releases.append(it)

    releases.sort(key=lambda x: x["modelName"].lower())
    with_img = sum(1 for r in releases if r.get("imageURL"))
    print(f"\nPop Race: {len(releases)} items ({with_img} with images)", flush=True)
    for label in PAGES.values():
        n = sum(1 for r in releases if label in r["marques"])
        print(f"  tagged {label}: {n}", flush=True)
    return releases


def merge_with_existing(new: list) -> list:
    if not os.path.exists(OUTPUT):
        return new
    with open(OUTPUT) as f:
        existing = json.load(f)
    existing_map = {r["modelName"]: r for r in existing}
    for item in new:
        name = item["modelName"]
        if name in existing_map and not item.get("imageURL") and existing_map[name].get("imageURL"):
            item["imageURL"] = existing_map[name]["imageURL"]
        existing_map[name] = item
    return sorted(existing_map.values(), key=lambda x: x["modelName"].lower())


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
