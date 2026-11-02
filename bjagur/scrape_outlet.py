"""
Scrape one outlet's pending articles. Started per outlet by the feed checker
(.github/workflows/scrape-outlet.yml), or by hand.

For every pending row (newest first, at most max_articles_per_run):
  fetch the page -> parse metadata + text -> apply filters.yml -> save

Outcome per article (News.scrape_status):
  done       text and metadata saved, read_article = true
  filtered   a filters.yml rule matched; reason in filter_reason; metadata saved, no text
  paywalled  locked article; metadata saved, no text
  failed     page gone (404/410), unparseable, or max_attempts reached
  pending    temporary failure, retried on a later run

Usage:
  python -m bjagur.scrape_outlet visir
  python -m bjagur.scrape_outlet mbl --limit 5 --dry-run
  python -m bjagur.scrape_outlet ruv --backfill --limit 100   # fill new columns on old rows
"""

from __future__ import annotations

import argparse
import sys
import time

from . import db
from .filters import FilterSet, evaluate
from .http import FetchError, Http
from .outlets import by_db_name, by_slug
from .outlets.base import ParseError


def process_row(scraper, row: dict, rules, scraping, http, backfill: bool = False) -> tuple[str, dict]:
    """
    Scrape one queued row. Returns (label for the log, columns to write to the row).
    Never raises for expected problems; they become status updates.
    """
    attempts = (row.get("scrape_attempts") or 0) + 1
    try:
        page = http.get(row["url"])
        article = scraper.parse_page(row["url"], page.text)
    except FetchError as exc:
        give_up = exc.permanent or attempts >= scraping.max_attempts
        return (
            f"{'FAILED' if give_up else 'retry'}: {exc}",
            {"scrape_attempts": attempts, "scrape_status": "failed" if give_up else "pending"},
        )
    except ParseError as exc:
        give_up = attempts >= scraping.max_attempts
        return (
            f"{'FAILED' if give_up else 'retry'}: {exc}",
            {"scrape_attempts": attempts, "scrape_status": "failed" if give_up else "pending"},
        )

    fields = {
        **article.metadata_row(),
        "scrape_attempts": attempts,
        "scraped_at": db.now_iso(),
    }
    # Fill gaps left by the RSS stage, but never overwrite what the feed gave us.
    for column in ("title", "summary", "published_at", "image_url"):
        if not row.get(column) and getattr(article, column):
            fields[column] = getattr(article, column)

    if backfill:  # old, already-scraped row: add metadata only, keep content and status
        return "backfilled", fields

    reason = evaluate(rules, article)
    if reason is None and not article.content:
        reason = "no_text"  # e.g. a video-only page

    if reason:
        status = "paywalled" if reason == "paywalled" else "filtered"
        fields.update(scrape_status=status, filter_reason=reason, content=None, read_article=False)
        return f"{status} ({reason})", fields

    fields.update(
        scrape_status="done", filter_reason=None, content=article.content, read_article=True
    )
    return f"done ({len(article.content)} chars)", fields


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Scrape one outlet's pending articles.")
    parser.add_argument("outlet", help="outlet slug: visir | mbl | ruv")
    parser.add_argument("--limit", type=int, help="max articles this run (default: filters.yml)")
    parser.add_argument("--dry-run", action="store_true", help="fetch and log, write nothing")
    parser.add_argument("--backfill", action="store_true",
                        help="fill the new metadata columns on rows scraped before they existed")
    args = parser.parse_args(argv)

    scraper = by_slug(args.outlet)
    rules, scraping = FilterSet.load().for_outlet(scraper.slug, scraper.db_name)
    limit = args.limit or scraping.max_articles_per_run

    outlet = next((o for o in db.get_outlets() if by_db_name(o["name"]) is scraper), None)
    if outlet is None:
        print(f"No row in the Outlets table matches '{scraper.db_name}'.", file=sys.stderr)
        return 1

    rows = (db.get_backfill if args.backfill else db.get_pending)(outlet["id"], limit)
    delay = scraping.request_delay_seconds
    http = Http(user_agent=scraper.user_agent, delay=scraper.request_delay if delay is None else delay)
    print(f"[{scraper.slug}] {len(rows)} article(s) to process (limit {limit}, "
          f"delay {http.delay:g}s){' [dry run]' if args.dry_run else ''}", flush=True)

    counts: dict[str, int] = {}
    started = time.monotonic()
    for i, row in enumerate(rows, 1):
        label, fields = process_row(scraper, row, rules, scraping, http, args.backfill)
        print(f"  [{i}/{len(rows)}] {row['url']} -> {label}", flush=True)
        status = fields.get("scrape_status", "backfilled" if args.backfill else "unknown")
        counts[status] = counts.get(status, 0) + 1
        if not args.dry_run:
            try:
                db.update_row(row["id"], fields)
            except Exception as exc:  # keep going; the row stays as it was and is retried
                print(f"    could not save row {row['id']}: {exc}", file=sys.stderr, flush=True)

    summary = ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) or "nothing to do"
    print(f"[{scraper.slug}] finished in {time.monotonic() - started:.0f}s: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
