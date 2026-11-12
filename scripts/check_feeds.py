#!/usr/bin/env python3
"""
Feed health check — run AFTER all scrapers, BEFORE committing.

Catches the two silent failure modes that a green scraper run hides:

  1. COUNT COLLAPSE — a source changed its HTML/JSON and the scraper now matches
     only a handful of items (feed technically "succeeded" but is broken).
  2. STALENESS — a source stopped updating, so a feed hasn't meaningfully
     changed in a long time.

It compares each feed to the version currently committed in git (HEAD), records
a rolling status in data/_status.json, and exits non-zero if anything looks
wrong — which turns the Actions run red and triggers GitHub's failure email.

Usage (in the workflow, after scrapers, before the commit step):
    python scripts/check_feeds.py

Environment knobs (all optional):
    FEED_DROP_THRESHOLD   fractional count drop that fails a feed   (default 0.30)
    FEED_STALE_DAYS       days a feed may go unchanged before warn  (default 5)
    FEED_STALE_FAILS      "true" to FAIL on staleness, else just warn (default false)
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone

DATA_DIR = "data"
STATUS_PATH = os.path.join(DATA_DIR, "_status.json")

# Each feed we expect to exist. Keep in sync with the scrapers.
FEEDS = {
    "hotwheels":  "hotwheels_releases.json",
    "matchbox":   "matchbox_releases.json",
    "minigt":     "minigt_releases.json",
    "kaidohouse": "kaidohouse_releases.json",
    "poprace":    "poprace_releases.json",
    "tarmac":     "tarmac_releases.json",
    "cmmodel":    "cmmodel_releases.json",
    "bbr":        "bbr_releases.json",
}

DROP_THRESHOLD = float(os.environ.get("FEED_DROP_THRESHOLD", "0.30"))
STALE_DAYS     = int(os.environ.get("FEED_STALE_DAYS", "5"))
STALE_FAILS    = os.environ.get("FEED_STALE_FAILS", "false").lower() == "true"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def count_in_working_tree(filename):
    """Item count in the freshly-scraped file on disk (None if missing/broken)."""
    path = os.path.join(DATA_DIR, filename)
    if not os.path.exists(path):
        return None
    try:
        data = load_json(path)
        return len(data) if isinstance(data, list) else None
    except Exception:
        return None


def count_in_head(filename):
    """Item count in the version committed at HEAD (None if new / unreadable)."""
    try:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{DATA_DIR}/{filename}"],
            capture_output=True, text=True, check=True,
        ).stdout
        data = json.loads(blob)
        return len(data) if isinstance(data, list) else None
    except Exception:
        return None


def changed_vs_head(filename):
    """True if the working-tree file differs from HEAD (git diff)."""
    result = subprocess.run(
        ["git", "diff", "--quiet", "HEAD", "--", f"{DATA_DIR}/{filename}"]
    )
    # exit 1 = differences, 0 = identical
    return result.returncode != 0


def load_prev_status():
    try:
        return load_json(STATUS_PATH)
    except Exception:
        return {}


def main():
    prev = load_prev_status()
    prev_feeds = prev.get("feeds", {})

    status = {"lastCheck": now_iso(), "feeds": {}}
    failures = []
    warnings = []

    for key, filename in FEEDS.items():
        new_count = count_in_working_tree(filename)
        old_count = count_in_head(filename)
        changed = changed_vs_head(filename)

        entry = prev_feeds.get(key, {})
        last_changed = entry.get("lastChanged")
        first_seen = entry.get("firstSeen", now_iso())

        # --- Missing / unreadable feed --------------------------------------
        if new_count is None:
            failures.append(f"[{key}] feed missing or unreadable ({filename})")
            status["feeds"][key] = {
                "count": None, "ok": False, "note": "missing/unreadable",
                "lastChanged": last_changed, "firstSeen": first_seen,
                "lastCheck": now_iso(),
            }
            continue

        # --- Count collapse --------------------------------------------------
        note = "ok"
        ok = True
        if old_count and old_count > 0:
            drop = (old_count - new_count) / old_count
            if drop >= DROP_THRESHOLD:
                ok = False
                pct = int(drop * 100)
                failures.append(
                    f"[{key}] item count dropped {pct}% "
                    f"({old_count} → {new_count}) — source likely broke"
                )
                note = f"count drop {pct}%"

        # --- Staleness -------------------------------------------------------
        if changed:
            last_changed = now_iso()
        else:
            # How long since it last changed?
            ref = last_changed or first_seen
            try:
                ref_dt = datetime.fromisoformat(ref)
                age_days = (datetime.now(timezone.utc) - ref_dt).days
            except Exception:
                age_days = 0
            if age_days >= STALE_DAYS:
                msg = (f"[{key}] no change for {age_days} days "
                       f"(threshold {STALE_DAYS}) — source may have stopped updating")
                if STALE_FAILS:
                    failures.append(msg); ok = False; note = f"stale {age_days}d"
                else:
                    warnings.append(msg)
                    if note == "ok":
                        note = f"stale {age_days}d (warn)"

        status["feeds"][key] = {
            "count": new_count,
            "ok": ok,
            "note": note,
            "changedThisRun": changed,
            "lastChanged": last_changed,
            "firstSeen": first_seen,
            "lastCheck": now_iso(),
        }

    # Write the status file so it gets committed alongside the feeds and can be
    # eyeballed (or read by the app) as a single source of truth.
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(STATUS_PATH, "w", encoding="utf-8") as fh:
        json.dump(status, fh, indent=2, ensure_ascii=False)

    # Report
    print("=== Feed health ===")
    for key, s in status["feeds"].items():
        flag = "OK " if s["ok"] else "FAIL"
        chg = "changed" if s.get("changedThisRun") else "no change"
        print(f"  [{flag}] {key:11} count={s['count']}  {chg}  ({s['note']})")

    for w in warnings:
        print(f"WARN  {w}", file=sys.stderr)
    for f in failures:
        print(f"ERROR {f}", file=sys.stderr)

    if failures:
        print(f"\n❌ {len(failures)} feed problem(s) detected.", file=sys.stderr)
        sys.exit(1)
    print(f"\n✅ All feeds healthy ({len(warnings)} warning(s)).")


if __name__ == "__main__":
    main()
