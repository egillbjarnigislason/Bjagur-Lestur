"""
Vísir (https://www.visir.is)

What the analysis of ~110 live articles showed
  RSS        https://www.visir.is/rss/allt - plain RSS 2.0 with only guid/link/title/
             description/pubDate. NO category, author or tags in the feed.
  URL        /g/<id>d/<slug>  (the section is NOT in the URL)
  <meta>     article:section   Frettir | Lifid | Skodun | Sport | Vidskipti
             article:tag       one tag per <meta>, 0..5 per article
             article:author    names joined with ","
             article:published_time / modified_time   "2.11.2026 10:03:43 +00:00"
             k5a:paywall       "open" (every sampled article) - anything else = locked
             k5a:paid          "0" | "1"
  JSON-LD    NewsArticle with author [{Person, name, url}] (invalid JSON: trailing ';')
  <article class="article-single -news|-opinion|-sport|-life|-business">
  header.article-single__header > h1, span.article-single__author, time
  div.article-single__content   body: lede <p>, an ad div, then a nested <article>
  div.tagslist > a.tag          the tags, visible
  Related    other Vísir stories are embedded inline in the body as
             <div class="iframely-embed"> ... <a href="https://www.visir.is/g/...">
             (no anchor text, so related titles are None)
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..models import Article
from ..text import clean_inline, dedupe, parse_datetime, strip_html
from .base import (
    OutletScraper,
    ParseError,
    absolute,
    add_related,
    body_text,
    entry_published,
    feed_entries,
    json_ld,
    ld_authors,
    meta_first,
    meta_values,
)

_ARTICLE_LINK = re.compile(r"/g/\d+[a-z]?/")


class Visir(OutletScraper):
    slug = "visir"
    db_name = "Vísir"
    request_delay = 2.0

    def feed_items(self, xml):
        items = []
        for entry in feed_entries(xml):
            url = entry.get("link")
            if not url:
                continue
            items.append(
                Article(
                    url=url,
                    title=clean_inline(entry.get("title")) or None,
                    summary=strip_html(entry.get("summary")) or None,
                    published_at=entry_published(entry),
                )
            )
        return items

    def parse_page(self, url, html):
        soup = BeautifulSoup(html, "html.parser")
        content = soup.select_one(".article-single__content")
        if content is None:
            raise ParseError("no .article-single__content (not a standard Vísir article)")

        article = Article(url=url)

        h1 = soup.select_one("header.article-single__header h1")
        article.title = clean_inline(h1.get_text(" ")) if h1 else None
        article.summary = meta_first(soup, "description")
        article.image_url = meta_first(soup, "og:image")
        article.published_at = parse_datetime(meta_first(soup, "article:published_time"))
        article.modified_at = parse_datetime(meta_first(soup, "article:modified_time"))

        article.section = meta_first(soup, "article:section")
        main = soup.select_one("article.article-single")
        classes = main.get("class", []) if main else []
        article.article_type = "opinion" if "-opinion" in classes else "news"

        article.tags = dedupe(
            [*meta_values(soup, "article:tag"), *(a.get_text() for a in soup.select(".tagslist a.tag"))]
        )

        article.authors = dedupe(self._authors(soup))

        paywall, paid = meta_first(soup, "k5a:paywall"), meta_first(soup, "k5a:paid")
        if paywall is None and paid is None:
            article.is_paywalled = None
        else:
            article.is_paywalled = (paywall not in (None, "open")) or paid == "1"

        # related stories: inline embeds that link to other Vísir articles
        related, seen = [], {}
        for a in content.select("a[href]"):
            href = absolute(url, a.get("href"))
            if href and _ARTICLE_LINK.search(href):
                add_related(related, seen, href, a.get_text(" "), url)
        article.related = related

        article.content = body_text(
            content,
            drop=(
                "script", "style", "figure", "figcaption", "aside", "iframe",
                ".iframely-embed", ".summary-ad", ".tagslist", "[class*=social-share]",
            ),
        )
        return article

    @staticmethod
    def _authors(soup) -> list[str]:
        names = ld_authors(json_ld(soup))
        if names:
            return names
        meta = meta_first(soup, "article:author")
        if meta:
            return [n for n in meta.split(",") if n.strip()]
        span = soup.select_one(".article-single__author")
        if span:
            linked = [a.get_text() for a in span.select("a")]
            return linked or [re.sub(r"\s+(skrifar|skrifa)$", "", clean_inline(span.get_text()))]
        return []
