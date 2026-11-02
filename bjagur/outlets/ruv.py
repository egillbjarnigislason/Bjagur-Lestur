"""
RÚV (https://www.ruv.is) - public broadcaster, no paywall.

What the analysis of 40 live articles showed
  RSS        https://www.ruv.is/rss/frettir - <dc:creator> is always "Fréttastofa RÚV"
             (not a real author), an <enclosure> image, and the full article HTML
             in <description>.
  URL        /frettir/<section>/<YYYY-MM-DD>-<slug>-<id>/
             sections: innlent, erlent, ithrottir, menning-og-daegurmal
  Page       a Next.js app. The article text is NOT in the HTML; the page embeds the
             whole article as JSON in <script id="__NEXT_DATA__">, at
             props.pageProps.data.article:
               title, subtitle, slug, first_published_at, last_published_at
               authors[]      {name, email}        - real bylines (1-2 per article)
               tags[]         {name, slug}         - free-form keywords
               parent         {title, slug}        - section ("Innlendar fréttir"/"innlent")
               topic          {name, category}     - sub-section ("Handbolti", "Dómstólar")
               content_type   "Færsla" | "Fréttamoli"
               clip_type      "video" | "audio" | null
               main_image     {renditions: {large: {src}}, credit, description}
               related_pages[]  {title, url, subtitle}   (3 on 1 of 40 articles)
               body[]         blocks, each with a `block_type`:
                   text_block, side_container_block, faq_block   -> editorial text
                   internal_embed_block {page:{title,url}}       -> related article card
                   clip_block, player_block, image_block, highcharts_block,
                   reusable_code_block, external_embed_block     -> not text
  Hosts      links inside the JSON use the host nyr.ruv.is; they are rewritten to
             www.ruv.is.
"""

from __future__ import annotations

import json
import re

from bs4 import BeautifulSoup

from ..models import Article
from ..text import clean_inline, dedupe, first_image, parse_datetime, strip_html
from .base import (
    BODY_TAGS,
    OutletScraper,
    ParseError,
    add_related,
    entry_published,
    feed_entries,
)

_SUMMARY_CHARS = 500


class Ruv(OutletScraper):
    slug = "ruv"
    db_name = "RÚV"
    request_delay = 2.0

    # ----------------------------------------------------------------- feed

    def feed_items(self, xml):
        items = []
        for entry in feed_entries(xml):
            url = entry.get("link")
            if not url:
                continue
            enclosures = entry.get("enclosures") or []
            text = strip_html(entry.get("summary"))
            items.append(
                Article(
                    url=url,
                    title=clean_inline(entry.get("title")) or None,
                    summary=_shorten(text, _SUMMARY_CHARS) or None,
                    published_at=entry_published(entry),
                    image_url=(enclosures[0].get("href") if enclosures else None)
                    or first_image(entry.get("summary")),
                    feed_category=_url_section(url),
                )
            )
        return items

    # ----------------------------------------------------------------- page

    def parse_page(self, url, html):
        soup = BeautifulSoup(html, "html.parser")
        tag = soup.find("script", id="__NEXT_DATA__")
        if tag is None or not (tag.string or tag.get_text()):
            raise ParseError("no __NEXT_DATA__ (not a standard RÚV article)")
        try:
            data = json.loads(tag.string or tag.get_text())
            art = data["props"]["pageProps"]["data"]["article"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ParseError(f"unexpected RÚV page structure: {exc!r}") from exc
        if not isinstance(art, dict):
            raise ParseError("RÚV page has no article")

        article = Article(url=url)
        article.title = clean_inline(art.get("title")) or None
        article.summary = clean_inline(art.get("subtitle")) or None
        article.published_at = parse_datetime(art.get("first_published_at"))
        article.modified_at = parse_datetime(art.get("last_published_at"))

        parent = art.get("parent") or {}
        article.section = parent.get("title")
        article.section_slug = parent.get("slug")
        article.subsection = (art.get("topic") or {}).get("name")
        content_type = clean_inline(art.get("content_type")).casefold()
        article.article_type = content_type or None

        article.authors = dedupe(a.get("name") for a in art.get("authors") or [] if a)
        article.tags = dedupe(t.get("name") for t in art.get("tags") or [] if t)
        article.is_paywalled = False

        image = art.get("main_image") or {}
        article.image_url = (
            ((image.get("renditions") or {}).get("large") or {}).get("src")
            or image.get("original_url")
        )

        related, seen = [], {}
        for block in art.get("body") or []:
            if block.get("block_type") == "internal_embed_block":
                page = (block.get("internal_embed_block") or {}).get("page") or {}
                add_related(related, seen, _public_url(page.get("url")), page.get("title"), url)
        for page in art.get("related_pages") or []:
            add_related(related, seen, _public_url(page.get("url")), page.get("title"), url)
        article.related = related

        parts: list[str] = []
        for block in art.get("body") or []:
            parts.extend(_block_text(block))
        article.content = "\n".join(parts) or None
        return article


# --------------------------------------------------------------------- helpers


def _url_section(url: str) -> str | None:
    match = re.search(r"/frettir/([^/]+)/", url)
    return match.group(1) if match else None


def _public_url(url: str | None) -> str | None:
    return url.replace("://nyr.ruv.is", "://www.ruv.is") if url else None


def _shorten(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def _html_lines(markup: str) -> list[str]:
    """Paragraph/heading/list/quote text of an HTML fragment, one entry per block."""
    fragment = BeautifulSoup(markup, "html.parser")
    lines = []
    for el in fragment.find_all(BODY_TAGS):
        if any(p.name in ("blockquote", "li") for p in el.parents):
            continue
        text = clean_inline(el.get_text(""))
        if text:
            lines.append(text)
    return lines or ([clean_inline(fragment.get_text(""))] if clean_inline(fragment.get_text("")) else [])


def _block_text(block: dict) -> list[str]:
    """Editorial text of one body block, or [] for video, embeds, widgets, ads."""
    kind = block.get("block_type")

    if kind == "text_block":
        html = (block.get("text_block") or {}).get("html")
        return _html_lines(html) if html else []

    if kind == "side_container_block":
        side = block.get("side_container_block") or {}
        lines = [clean_inline(side["side_title"])] if side.get("side_title") else []
        if side.get("side_text"):
            lines.extend(_html_lines(side["side_text"]))
        return lines

    if kind == "faq_block":
        lines = []
        for item in (block.get("faq_block") or {}).get("question_items", []):
            if item.get("question"):
                lines.append(clean_inline(item["question"]))
            if item.get("answer"):
                lines.extend(_html_lines(item["answer"]))
        return lines

    return []
