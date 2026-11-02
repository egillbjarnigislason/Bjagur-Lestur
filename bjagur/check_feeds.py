"""
Feed checker - runs every few minutes from .github/workflows/check-feeds.yml.

For each outlet in the Supabase Outlets table:
  1. download its RSS feed (one small request)
  2. drop items older than max_age_hours and items matched by the feed-stage filters
  3. insert the genuinely new ones into News as 'pending'
  4. report which outlets have pending articles

The outlets that need scraping are written to $GITHUB_OUTPUT as a JSON list
(`outlets=["visir","mbl"]`); the workflow then starts one scrape job per outlet.
Nothing here reads article pages, so a run takes seconds.

Usage:  python -m bjagur.check_feeds [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

from . import db
from .filters import FilterSet, evaluate
from .http import FetchError, Http
from .outlets import by_db_name


def check_outlet(scraper, outlet: dict, filters: FilterSet, dry_run: bool = False, http=None, now=None) -> int:
    """Check one outlet's feed. Returns how many articles are waiting to be scraped."""
    rules, _ = filters.for_outlet(scraper.slug, scraper.db_name)
    feed_url = outlet.get("rss_feed")
    if not feed_url:
        print(f"[{scraper.slug}] no rss_feed set in Outlets - skipping")
        return 0

    http = http or Http(user_agent=scraper.user_agent)
    items = scraper.feed_items(http.get(feed_url).content)
    known = db.existing_urls([a.url for a in items])
    fresh = [a for a in items if a.url not in known]

    kept, dropped = [], Counter()
    for article in fresh:
        reason = evaluate(rules, article, feed_stage=True, now=now)
        if reason:
            dropped[reason.split(":")[0] if reason.startswith("too_old") else reason] += 1
        else:
            kept.append(article)

    if not dry_run:
        db.insert_pending(outlet["id"], kept)
    pending = db.count_pending(outlet["id"]) + (len(kept) if dry_run else 0)

    why = ", ".join(f"{k} x{v}" for k, v in sorted(dropped.items())) or "-"
    print(
        f"[{scraper.slug}] feed {len(items)} items | known {len(items) - len(fresh)} | "
        f"new {len(kept)} kept, {len(fresh) - len(kept)} filtered ({why}) | "
        f"pending in queue {pending}",
        flush=True,
    )
    return pending


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Check RSS feeds and queue new articles.")
    parser.add_argument("--dry-run", action="store_true", help="read feeds, write nothing")
    args = parser.parse_args(argv)

    filters = FilterSet.load()
    outlets = db.get_outlets()
    needs_scraping: list[str] = []
    failures = 0

    for outlet in outlets:
        scraper = by_db_name(outlet["name"])
        if scraper is None:
            print(f"[{outlet['name']}] no scraper registered - skipping")
            continue
        try:
            if check_outlet(scraper, outlet, filters, args.dry_run) > 0:
                needs_scraping.append(scraper.slug)
        except FetchError as exc:
            failures += 1
            print(f"[{scraper.slug}] FEED ERROR: {exc}", file=sys.stderr, flush=True)

    payload = json.dumps(needs_scraping)
    print(f"outlets to scrape: {payload}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as fh:
            fh.write(f"outlets={payload}\n")

    # Fail the run only when NO feed could be read; one flaky outlet must not hide the rest.
    return 1 if outlets and failures == len(outlets) else 0


if __name__ == "__main__":
    sys.exit(main())
