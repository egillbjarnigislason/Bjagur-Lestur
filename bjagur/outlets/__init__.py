"""
Registry of outlets. To add an outlet: write bjagur/outlets/<name>.py with a
subclass of OutletScraper, add it to SCRAPERS below, add an entry for it in
filters.yml, and add its slug to the `outlet` choices in
.github/workflows/scrape-outlet.yml.
"""

from __future__ import annotations

from ..text import fold
from .base import OutletScraper
from .mbl import Mbl
from .ruv import Ruv
from .visir import Visir

SCRAPERS: dict[str, OutletScraper] = {s.slug: s for s in (Visir(), Mbl(), Ruv())}


def by_slug(slug: str) -> OutletScraper:
    try:
        return SCRAPERS[slug.lower()]
    except KeyError:
        raise SystemExit(f"Unknown outlet '{slug}'. Known: {', '.join(sorted(SCRAPERS))}")


def by_db_name(name: str) -> OutletScraper | None:
    """Scraper for an Outlets.name value such as "Vísir" (accent/case-insensitive)."""
    for scraper in SCRAPERS.values():
        if fold(scraper.db_name) == fold(name):
            return scraper
    return None
