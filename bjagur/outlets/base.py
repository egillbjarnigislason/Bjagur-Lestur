"""Shared scaffolding for the per-outlet scrapers."""

from __future__ import annotations

import json
import time
from urllib.parse import urljoin

import feedparser
from bs4 import BeautifulSoup, Tag

from ..http import DEFAULT_USER_AGENT
from ..models import Article
from ..text import clean_inline, parse_datetime

BODY_TAGS = ["p", "h2", "h3", "h4", "h5", "h6", "blockquote", "li"]


class ParseError(Exception):
    """The page was fetched but does not look like an article we understand."""


class OutletScraper:
    """
    One subclass per outlet. Both methods are pure (no network, no database) so
    they can be tested against saved pages.

    slug        short id used on the command line and in filters.yml
    db_name     the `name` of the outlet's row in the Supabase Outlets table
    """

    slug: str = ""
    db_name: str = ""
    user_agent: str = DEFAULT_USER_AGENT
    request_delay: float = 2.0  # seconds between page requests; filters.yml can override

    def feed_items(self, xml: bytes | str) -> list[Article]:
        """Parse the RSS feed into Articles (title, url, summary, date, ...)."""
        raise NotImplementedError

    def parse_page(self, url: str, html: str) -> Article:
        """Parse one article page into an Article with metadata and content."""
        raise NotImplementedError


# ------------------------------------------------------------------ helpers


def feed_entries(xml: bytes | str):
    return feedparser.parse(xml).entries


def entry_published(entry) -> str | None:
    parsed = entry.get("published_parsed")
    if parsed:
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", parsed)
    return parse_datetime(entry.get("published"))


def meta_values(soup: BeautifulSoup, name: str) -> list[str]:
    """All non-empty <meta property|name="..."> contents for `name`, in page order."""
    out = []
    for attr in ("property", "name"):
        for tag in soup.find_all("meta", attrs={attr: name}):
            value = clean_inline(tag.get("content"))
            if value:
                out.append(value)
    return out


def meta_first(soup: BeautifulSoup, name: str) -> str | None:
    values = meta_values(soup, name)
    return values[0] if values else None


def json_ld(soup: BeautifulSoup) -> list[dict]:
    """Every JSON-LD object on the page. Tolerates the trailing ';' Vísir emits."""
    found = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = (tag.string or tag.get_text() or "").strip().rstrip(";").strip()
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        found.extend(data if isinstance(data, list) else [data])
    return [d for d in found if isinstance(d, dict)]


def ld_authors(objects: list[dict]) -> list[str]:
    """Person names from JSON-LD `author` (ignores organisations such as mbl.is)."""
    names = []
    for obj in objects:
        author = obj.get("author")
        for a in author if isinstance(author, list) else [author]:
            if isinstance(a, dict) and a.get("@type") == "Person" and a.get("name"):
                names.append(a["name"])
    return names


def absolute(base: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(base, href.strip())


def body_text(container: Tag, drop: tuple[str, ...] = ()) -> str | None:
    """
    Text of an article body: paragraphs, sub-headings, quotes and list items in
    page order, one per line. `drop` are CSS selectors removed first (ads, widgets,
    paywall boxes...). The container is modified in place.
    """
    for selector in drop:
        for node in container.select(selector):
            node.decompose()
    for br in container.find_all("br"):
        br.replace_with(" ")

    lines = []
    for el in container.find_all(BODY_TAGS):
        if _inside_quote_or_item(el, container):
            continue  # its text is already part of the enclosing blockquote/li
        text = clean_inline(el.get_text(""))
        if text:
            lines.append(text)
    return "\n".join(lines) or None


def _inside_quote_or_item(el: Tag, container: Tag) -> bool:
    for parent in el.parents:
        if parent is container:
            return False
        if parent.name in ("blockquote", "li"):
            return True
    return False


def add_related(related: list[dict], seen: dict, url: str | None, title: str | None, self_url: str) -> None:
    """
    Append {"url","title"} once per URL, never the article itself. `seen` maps
    URL -> its entry; a later sighting of the same URL fills in a missing title
    (an image link comes before the headline link that carries the text).
    """
    if not url:
        return
    key = url.rstrip("/")
    if key == self_url.rstrip("/"):
        return
    title = clean_inline(title) or None
    if key in seen:
        if seen[key]["title"] is None and title:
            seen[key]["title"] = title
        return
    entry = {"url": url, "title": title}
    seen[key] = entry
    related.append(entry)
