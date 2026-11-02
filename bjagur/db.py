"""Supabase access. The only module that talks to the database."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import lru_cache

from dotenv import load_dotenv

from .models import Article

load_dotenv()

TABLE = "News"
_URL_CHUNK = 20  # keeps the PostgREST query string comfortably short


@lru_cache(maxsize=1)
def client():
    from supabase import create_client

    try:
        return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])
    except KeyError as exc:
        raise SystemExit(f"Missing environment variable {exc}. See .env.example.")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------- outlets


def get_outlets() -> list[dict]:
    return client().table("Outlets").select("*").order("id").execute().data


# ------------------------------------------------------------------ feed check


def existing_urls(urls: list[str]) -> set[str]:
    """Which of these URLs already have a row in News (any status)."""
    found: set[str] = set()
    for i in range(0, len(urls), _URL_CHUNK):
        chunk = urls[i : i + _URL_CHUNK]
        rows = client().table(TABLE).select("url").in_("url", chunk).execute().data
        found.update(r["url"] for r in rows)
    return found


def insert_pending(outlet_id: int, articles: list[Article]) -> int:
    """Insert new articles as 'pending'. Existing rows are left untouched."""
    if not articles:
        return 0
    rows = [
        {
            "outlet_id": outlet_id,
            "url": a.url,
            "title": a.title,
            "summary": a.summary,
            "published_at": a.published_at,
            "image_url": a.image_url,
            "scrape_status": "pending",
        }
        for a in articles
    ]
    client().table(TABLE).upsert(rows, on_conflict="url", ignore_duplicates=True).execute()
    return len(rows)


def count_pending(outlet_id: int) -> int:
    response = (
        client()
        .table(TABLE)
        .select("id", count="exact")
        .eq("outlet_id", outlet_id)
        .eq("scrape_status", "pending")
        .limit(1)
        .execute()
    )
    return response.count or 0


# ---------------------------------------------------------------- scrape queue

_QUEUE_COLUMNS = "id, url, title, summary, published_at, image_url, scrape_attempts"


def get_pending(outlet_id: int, limit: int) -> list[dict]:
    """Newest pending rows first, so fresh news is never stuck behind a backlog."""
    return (
        client()
        .table(TABLE)
        .select(_QUEUE_COLUMNS)
        .eq("outlet_id", outlet_id)
        .eq("scrape_status", "pending")
        .order("published_at", desc=True, nullsfirst=False)
        .limit(limit)
        .execute()
        .data
    )


def get_backfill(outlet_id: int, limit: int) -> list[dict]:
    """Already-scraped rows from before the metadata columns existed."""
    return (
        client()
        .table(TABLE)
        .select(_QUEUE_COLUMNS)
        .eq("outlet_id", outlet_id)
        .eq("scrape_status", "done")
        .is_("scraped_at", "null")
        .order("published_at", desc=True, nullsfirst=False)
        .limit(limit)
        .execute()
        .data
    )


def update_row(news_id: int, fields: dict) -> None:
    client().table(TABLE).update(fields).eq("id", news_id).execute()
