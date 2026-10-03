#!/usr/bin/env python3
"""
Make feeds ADDITIVE — run AFTER the scrapers, BEFORE check_feeds.py.

Why: a release catalogue is history, not inventory. When a brand redesigns its
site and drops older models (Pop Race moved its back-catalogue off-site), a
plain scrape would silently delete cars that genuinely exist and that users may
already own. This merges each freshly-scraped feed with the version committed
at git HEAD, so entries are never lost — only added or updated.

Rules per feed:
  - Key on modelName (case-insensitive), matching the scrapers' own dedupe.
  - An entry present in BOTH: keep the NEW record (fresher image/code/status),
    but fill any field the new record left blank from the old one.
  - An entry only in the OLD feed: keep it (this is the back-catalogue rescue).
  - An entry only in the NEW feed: add it.

Set ADDITIVE_FEEDS to the feeds that should never shrink. Feeds not listed are
left exactly as scraped.
"""

import json
import os
import re
import subprocess
import sys

DATA_DIR = "data"

# Feeds whose sources drop older items and therefore must never shrink.
ADDITIVE_FEEDS = {
    "poprace_releases.json",
    "tarmac_releases.json",
    "cmmodel_releases.json",
    "bbr_releases.json",
}

FILL_FIELDS = ("imageURL", "productURL", "series", "make", "scale")


def load_head(filename):
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{DATA_DIR}/{filename}"],
            capture_output=True, text=True, check=True,
        ).stdout
        data = json.loads(blob)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def load_disk(path):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except Exception:
        return []


SCALE_RE  = re.compile(r"\b1\s*[:/]\s*(64|43|18)\b")
VENDOR_RE = re.compile(r"\s*[-–—]\s*tarmac\s*works.*$", re.I)
PUNCT_RE  = re.compile(r"[^a-z0-9 ]+")
WS_RE     = re.compile(r"\s+")


def key_of(entry):
    """Canonical match key.

    Scrapers for the same brand have changed their naming over time — the old
    Tarmac scraper kept the raw shop title ("1/64 Koenigsegg Gemera Green -
    Tarmac Works GLOBAL64") while the current one trims the scale prefix and
    the vendor suffix ("Koenigsegg Gemera Green"). Matching on the raw string
    would treat those as two different cars and duplicate the whole feed, so we
    normalize to a canonical form before comparing.
    """
    name = (entry.get("modelName") or "").strip().lower()
    if not name:
        return ""
    name = VENDOR_RE.sub("", name)   # drop "- tarmac works global64" tail
    name = SCALE_RE.sub(" ", name)   # drop 1/64, 1:43 ...
    name = PUNCT_RE.sub(" ", name)   # punctuation varies between scrapers
    return WS_RE.sub(" ", name).strip()


def merge(old, new):
    """New wins, but blank fields fall back to the old record."""
    merged = dict(new)
    for field in FILL_FIELDS:
        if not merged.get(field) and old.get(field):
            merged[field] = old[field]
    return merged


def main():
    total_rescued = 0

    for filename in sorted(ADDITIVE_FEEDS):
        path = os.path.join(DATA_DIR, filename)
        new_items = load_disk(path)
        if not new_items:
            print(f"{filename}: nothing scraped this run — leaving as is")
            continue

        old_items = load_head(filename)
        if not old_items:
            print(f"{filename}: no previous version — {len(new_items)} items")
            continue

        new_by_key = {key_of(e): e for e in new_items if key_of(e)}
        result, rescued = [], 0

        # Start from the new scrape (preserves its ordering for fresh items).
        for entry in new_items:
            k = key_of(entry)
            if not k:
                continue
            result.append(entry)

        # Add anything the new scrape no longer lists.
        seen = {key_of(e) for e in result}
        for entry in old_items:
            k = key_of(entry)
            if not k or k in seen:
                continue
            result.append(entry)
            seen.add(k)
            rescued += 1

        # Fill blanks on entries present in both.
        old_by_key = {key_of(e): e for e in old_items if key_of(e)}
        for i, entry in enumerate(result):
            k = key_of(entry)
            if k in old_by_key and k in new_by_key:
                result[i] = merge(old_by_key[k], entry)

        with open(path, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, ensure_ascii=False)

        total_rescued += rescued
        print(f"{filename}: {len(new_items)} scraped + {rescued} retained "
              f"= {len(result)} total")

    print(f"\nOK merge complete ({total_rescued} historic entries retained).")


if __name__ == "__main__":
    main()
