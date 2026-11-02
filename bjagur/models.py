"""The one data structure every scraper produces."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse


@dataclass
class Article:
    """
    One news article. The RSS stage fills the first block, the page scraper
    fills the rest. Everything except `url` is optional so the filter engine can
    run on a partially filled article at the RSS stage.
    """

    url: str

    # --- from RSS -----------------------------------------------------------
    title: str | None = None
    summary: str | None = None
    published_at: str | None = None  # UTC ISO-8601
    image_url: str | None = None
    feed_category: str | None = None  # RSS <category> or URL slug, before the page is read

    # --- from the article page ---------------------------------------------
    section: str | None = None  # top level, e.g. "Sport", "Íþróttir", "Innlendar fréttir"
    section_slug: str | None = None  # URL-style form when the site has one, e.g. "ithrottir"
    subsection: str | None = None  # e.g. "Enski boltinn", "Handbolti"
    article_type: str | None = None  # "news", "opinion", "recipe", "færsla", ...
    byline_source: str | None = None  # Mbl: "Morgunblaðið", "AFP", "mbl", ...
    authors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    related: list[dict] = field(default_factory=list)  # [{"url":..., "title":...}]
    modified_at: str | None = None
    is_paywalled: bool | None = None
    content: str | None = None

    # ------------------------------------------------------------------------
    @property
    def path(self) -> str:
        return urlparse(self.url).path

    def section_keys(self) -> list[str]:
        """Every label this article's section may be known by (used for filtering)."""
        return [k for k in (self.section, self.section_slug, self.feed_category) if k]

    def metadata_row(self) -> dict:
        """Columns written for every scraped article, whatever its final status."""
        return {
            "authors": self.authors,
            "tags": self.tags,
            "related": self.related,
            "section": self.section,
            "subsection": self.subsection,
            "article_type": self.article_type,
            "byline_source": self.byline_source,
            "modified_at": self.modified_at,
            "is_paywalled": self.is_paywalled,
        }
