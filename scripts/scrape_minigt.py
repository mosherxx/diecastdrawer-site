#!/usr/bin/env python3
"""
Scrape MINI GT + Kaido House releases from minigt.tsm-models.com and write two
JSON feeds the DiecastDrawer app consumes:

    data/minigt_releases.json
    data/kaidohouse_releases.json

Each item: { "modelName", "productCode", "imageURL", "isReleased", "marque" }

Why this exists:
The app used to scrape + parse this HTML live on-device, which pegged the CPU and
froze scrolling. Doing it here (once, on GitHub's runners) means the app just
fetches a small static JSON — exactly like the Hot Wheels / Matchbox feeds.

Lives in scripts/ alongside the other scrapers; run by .github/workflows/scrape.yml.

Real HTML structure (per product) on a product-list page:

    <div class="product_hover product_box">
        <img src="upload/picfile/.../xxxx.jpg" alt="Model Name"/>
        <a href="index.php?action=product-detail&id=NNNN"></a>
    </div>
    <div class="position-relative pt-3">
        <a class="h6 ..." href="...product-detail&id=NNNN&b_id=BB">Model Name</a>
        <p class="m-0">KHMG263</p>                         <-- product code
        <div class="mb-2" ...>
            <a href="...product-detail&id=NNNN">Pre-Order</a>  <-- status (or "Released")
        </div>
    </div>
"""

import json
import os
import re
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

import requests
from bs4 import BeautifulSoup

BASE = "https://minigt.tsm-models.com/index.php?action=product-list&b_id="
IMG_BASE = "https://minigt.tsm-models.com/"
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://minigt.tsm-models.com/",
}

# Kaido House is its own b_id; everything else is MINI GT marques.
KAIDO_BID = 21

# MINI GT marque b_ids → display name. (Kaido handled separately below.)
# "Full Collection" (b_id=13) covers everything, so scraping it alone gets all
# MINI GT items; per-marque ids are kept for the `marque` label.
MINIGT_MARQUES = {
    13: "Full Collection",
    23: "Limited Edition",
    34: "MINI GT Set",
    39: "Regional Exclusive",
    73: "007 Movie Car",
    11: "Accessories",
    47: "Alfa Romeo", 19: "Audi", 46: "Bentley", 8: "BMW", 41: "Bugatti",
    42: "Cadillac", 40: "Chevrolet", 43: "Ducati", 24: "Ford", 44: "HKS",
    1: "Honda", 5: "Hyundai", 25: "Isuzu", 16: "Jaguar", 12: "Lamborghini",
    26: "Lancia", 27: "Land Rover", 3: "LB Works", 28: "Lincoln", 29: "Lotus",
    2: "Mazda", 4: "McLaren", 6: "Mercedes-Benz", 18: "Nissan", 22: "Pagani",
    30: "Pandem", 10: "Porsche", 38: "Range Rover", 31: "Red Bull Racing",
    20: "Shelby", 9: "Subaru", 33: "Tommykaira", 35: "Top Secret", 7: "Toyota",
    36: "VeilSide", 45: "Volkswagen", 37: "Western Star",
}

CODE_RE = re.compile(r"\b(?:KHMG|MGT)\-?\d{2,6}\b", re.IGNORECASE)


def fetch(url: str, retries: int = 3) -> Optional[str]:
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200 and r.text:
                return r.text
            print(f"  HTTP {r.status_code} for {url} (attempt {attempt})")
        except requests.RequestException as e:
            print(f"  request error (attempt {attempt}): {e}")
        time.sleep(2 * attempt)
    return None


def detect_total_pages(soup: BeautifulSoup) -> int:
    """Find the highest page number from pagination links (&p=N)."""
    pages = {1}
    for a in soup.find_all("a", href=True):
        m = re.search(r"[?&]p=(\d+)", a["href"])
        if m:
            pages.add(int(m.group(1)))
    return max(pages)


def parse_products(soup: BeautifulSoup, marque: str) -> list[dict]:
    """Extract products from one product-list page."""
    items = []
    # Each product's data lives in the sibling div after the image box. We anchor
    # on the product code <p class="m-0">CODE</p> and read name + status around it.
    for p in soup.find_all("p", class_="m-0"):
        text = (p.get_text() or "").strip()
        if not CODE_RE.search(text):
            continue
        code = CODE_RE.search(text).group(0).upper().replace(" ", "")

        # Container that holds name, code, and status (the parent of <p>).
        container = p.parent
        if container is None:
            continue

        # Model name — the <a class="h6 ..."> just before the code.
        name = ""
        name_a = container.find("a", class_=re.compile(r"\bh6\b"))
        if name_a:
            name = " ".join(name_a.get_text().split())
        if not name:
            # Fallback: any link text in the container that isn't the status.
            for a in container.find_all("a"):
                t = " ".join(a.get_text().split())
                if t and t.lower() not in ("pre-order", "preorder", "released"):
                    name = t
                    break
        if not name:
            continue

        # Status — the <div class="mb-2"> after the code, containing an <a>.
        status_div = container.find("div", class_=re.compile(r"\bmb-2\b"))
        status_text = ""
        if status_div:
            status_text = " ".join(status_div.get_text().split()).lower()
        is_released = True
        if "pre-order" in status_text or "preorder" in status_text:
            is_released = False
        elif "released" in status_text:
            is_released = True

        # Image — search the previous sibling image box for the <img src>.
        image_url = None
        img_box = container.find_previous("div", class_=re.compile(r"product_box"))
        if img_box:
            img = img_box.find("img", src=True)
            if img:
                src = img["src"].strip()
                if src.startswith("http"):
                    image_url = src
                elif src.startswith("//"):
                    image_url = "https:" + src
                elif src.startswith("/"):
                    image_url = "https://minigt.tsm-models.com" + src
                else:
                    image_url = IMG_BASE + src

        items.append({
            "modelName": name,
            "productCode": code,
            "imageURL": image_url,
            "isReleased": is_released,
            "marque": marque,
        })
    return items


def scrape_bid(b_id: int, marque: str) -> list[dict]:
    """Scrape all pages for one b_id."""
    first = fetch(f"{BASE}{b_id}")
    if not first:
        return []
    soup = BeautifulSoup(first, "html.parser")
    total = detect_total_pages(soup)
    all_items = parse_products(soup, marque)
    for page in range(2, total + 1):
        html = fetch(f"{BASE}{b_id}&p={page}")
        if not html:
            continue
        all_items += parse_products(BeautifulSoup(html, "html.parser"), marque)
        time.sleep(1)  # be polite
    return all_items


def dedupe(items: list[dict]) -> list[dict]:
    """Keep first occurrence per product code (or name if code missing)."""
    seen = set()
    out = []
    for it in items:
        key = it["productCode"] or it["modelName"].lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(it)
    return out


def is_kaido(item: dict) -> bool:
    code = item.get("productCode", "")
    name = item.get("modelName", "").lower().replace(" ", "")
    return code.upper().startswith("KHMG") or "kaido" in name


def main():
    repo_root = Path(__file__).resolve().parent.parent
    data_dir = repo_root / "data"
    data_dir.mkdir(exist_ok=True)

    # 1) MINI GT base list — "Full Collection" (b_id=13) has every MINI GT item.
    print("Scraping MINI GT Full Collection (b_id=13)…")
    minigt_items = scrape_bid(13, "Full Collection")

    # 2) Special collections — scraped separately so we know which items belong
    #    to each. An item can be in several (e.g. a Set that's also a Limited
    #    Edition), so we collect a SET of marque names per product code.
    special_bids = {
        23: "Limited Edition",
        34: "MINI GT Set",
        39: "Regional Exclusive",
        73: "007 Movie Car",
        11: "Accessories",
    }
    code_to_marques: dict[str, set] = {}
    for bid, label in special_bids.items():
        print(f"Scraping special collection: {label} (b_id={bid})…")
        for it in scrape_bid(bid, label):
            key = (it.get("productCode") or it.get("modelName", "")).upper()
            if not key:
                continue
            code_to_marques.setdefault(key, set()).add(label)

    # 3) Kaido House (b_id=21) separately.
    print(f"Scraping Kaido House (b_id={KAIDO_BID})…")
    kaido_items = scrape_bid(KAIDO_BID, "Kaido House")

    # Split Kaido out of the MINI GT list.
    minigt_clean = dedupe([i for i in minigt_items if not is_kaido(i)])
    kaido_clean = dedupe(kaido_items + [i for i in minigt_items if is_kaido(i)])

    # Attach the marques array to every MINI GT item (everything is at least in
    # "Full Collection"; specials add their labels).
    for it in minigt_clean:
        key = (it.get("productCode") or it.get("modelName", "")).upper()
        marques = {"Full Collection"}
        marques |= code_to_marques.get(key, set())
        it["marques"] = sorted(marques)
    for it in kaido_clean:
        it["marques"] = ["Kaido House"]

    stamp = datetime.now(timezone.utc).isoformat()

    (data_dir / "minigt_releases.json").write_text(
        json.dumps(minigt_clean, indent=2, ensure_ascii=False))
    (data_dir / "kaidohouse_releases.json").write_text(
        json.dumps(kaido_clean, indent=2, ensure_ascii=False))

    pre_mg = sum(1 for i in minigt_clean if not i["isReleased"])
    pre_kh = sum(1 for i in kaido_clean if not i["isReleased"])
    le = sum(1 for i in minigt_clean if "Limited Edition" in i.get("marques", []))
    st = sum(1 for i in minigt_clean if "MINI GT Set" in i.get("marques", []))
    re_ = sum(1 for i in minigt_clean if "Regional Exclusive" in i.get("marques", []))
    print(f"\nMINI GT:     {len(minigt_clean)} items ({pre_mg} pre-order) "
          f"→ data/minigt_releases.json")
    print(f"  tagged: Limited Edition {le}, Set {st}, Regional Exclusive {re_}")
    print(f"Kaido House: {len(kaido_clean)} items ({pre_kh} pre-order) "
          f"→ data/kaidohouse_releases.json")
    print(f"Scraped at {stamp}")

    if not minigt_clean and not kaido_clean:
        print("ERROR: scraped 0 items — not overwriting feeds is safer.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
