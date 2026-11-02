"""
Mbl (https://www.mbl.is)

What the analysis of 60 live articles across every section showed
  RSS        https://www.mbl.is/feeds/nyjast - has <category> (Innlent, Erlent,
             Viðskipti, Íþróttir, Menning, Smartland, Fólk, Matur, Ferðalög, K100,
             200 mílur) and an HTML <description> that starts with an <img>.
  URL        /<section>/<subsection>/YYYY/MM/DD/<slug>/
             e.g. /sport/fotbolti/2026/11/02/..., /frettir/innlent/..., /vidskipti/frettir/...
  <meta>     og:*, description, article:section (coarse: "Fréttir" for innlent+erlent),
             article:modified_time. `keywords` is empty. `cXenseParse:author` appears
             only on some pieces (columnists, culture).
  JSON-LD    NewsArticle. `author` is the organisation "mbl.is" for staff news, and a
             Person (with e-mail) for signed pieces. Recipe pages use @type Recipe.
  div.dateline   "Innlent | mbl | 2.11.2026 | 10:02"
                 "Íþróttir | Enski boltinn | mbl | 2.11.2026 | 9:45"
                 "Menning | Bækur | Morgunblaðið | 2.11.2026 | 6:25"
                 = section | [subsection] | source label | date | time [| Uppfært hh:mm]
  span.credit    PHOTO credits ("AFP/Mario Tama") - NOT the author.
  div.main-layout   the body. Besides <p>/<h3> it contains ad slots (aside.augl),
             photo galleries (.pswp), a mobile copy of the related box
             (.sidebar-mobile), a toolbar and the paywall box (#mnga-wrapper).
  div.all-related > div.related-news   "Tengdar fréttir": an <h3><a href=".../knippi/N/">
             (the topic bundle name, used here as a tag), one teaser and a list of
             headlines.
  Paywall    locked articles (source label "Morgunblaðið") contain #mnga-wrapper /
             .mnga. 5 of the 60 sampled were locked. The text is still in the HTML,
             but this scraper does not store it unless skip_paywalled is turned off.
  Rate limit mbl.is returns 429 when pages are fetched quickly and 403 for the default
             python-requests User-Agent, so a browser-like User-Agent is used here.
"""

from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..models import Article
from ..text import clean_inline, dedupe, fold, first_image, parse_datetime, strip_html
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
)

BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_ARTICLE_URL = re.compile(r"^https?://(www\.)?mbl\.is/.+/20\d\d/\d\d/\d\d/[^/]+/?$")
_DATE = re.compile(r"\d{1,2}\.\d{1,2}\.\d{4}")
_TIME = re.compile(r"\d{1,2}:\d{2}")
# labels that name where a story came from, as opposed to a sub-section
_SOURCES = {fold(s) for s in ("mbl", "Morgunblaðið", "AFP", "Reuters", "AP", "BBC", "mbl.is")}

# Parts of .main-layout that are not article text
_DROP = (
    "script", "style", "noscript", "aside", "figure", "iframe",
    ".augl", ".pswp", ".sidebar-mobile", ".newsitem-bottom-toolbar",
    ".all-related", ".related-news", "#postlistaskraning",
    "#mnga-wrapper", ".mnga", ".bw-locked-overlay", ".btn-subscription",
    ".subscr-left", ".subscr-right", ".credit", "[class*=caption]",
)


class Mbl(OutletScraper):
    slug = "mbl"
    db_name = "Mbl"
    user_agent = BROWSER_UA
    request_delay = 10.0

    # ----------------------------------------------------------------- feed

    def feed_items(self, xml):
        items = []
        for entry in feed_entries(xml):
            url = entry.get("link")
            if not url:
                continue
            tags = entry.get("tags") or []
            summary_html = entry.get("summary")
            items.append(
                Article(
                    url=url,
                    title=clean_inline(entry.get("title")) or None,
                    summary=strip_html(summary_html) or None,
                    published_at=entry_published(entry),
                    image_url=first_image(summary_html),
                    feed_category=clean_inline(tags[0].get("term")) if tags else None,
                )
            )
        return items

    # ----------------------------------------------------------------- page

    def parse_page(self, url, html):
        soup = BeautifulSoup(html, "html.parser")
        layout = soup.select_one(".main-layout")
        if layout is None:
            raise ParseError("no .main-layout (not a standard mbl.is article)")

        article = Article(url=url)
        ld = json_ld(soup)
        news = next((o for o in ld if o.get("@type") in ("NewsArticle", "Recipe")), {})

        h1 = soup.select_one(".newsitem-main h1")
        article.title = clean_inline(h1.get_text(" ")) if h1 else meta_first(soup, "og:title")
        article.summary = meta_first(soup, "description")
        article.image_url = meta_first(soup, "og:image")
        article.published_at = parse_datetime(news.get("datePublished"))
        article.modified_at = parse_datetime(news.get("dateModified")) or parse_datetime(
            meta_first(soup, "article:modified_time")
        )
        article.article_type = "recipe" if news.get("@type") == "Recipe" else "news"

        # section | [subsection] | source  from the dateline
        labels = self._dateline_labels(soup)
        article.section = labels[0] if labels else meta_first(soup, "article:section")
        article.byline_source, article.subsection = None, None
        if len(labels) >= 3:
            article.subsection, article.byline_source = labels[1], labels[-1]
        elif len(labels) == 2:
            if fold(labels[1]) in _SOURCES:
                article.byline_source = labels[1]
            else:
                article.subsection = labels[1]
        article.section_slug = self._section_slug(url)

        # authors: signed pieces only (the organisation "mbl.is" is not a person)
        meta_author = meta_first(soup, "cXenseParse:author")
        names = re.split(r"\s*(?:,| og )\s*", meta_author) if meta_author else []
        article.authors = dedupe([*names, *ld_authors(ld)])

        # "Tengdar fréttir": the topic bundle name is a tag, the links are related
        related, seen = [], {}
        # The box appears twice (desktop sidebar and a mobile copy inside the body);
        # take the fuller one in case the copies ever differ.
        boxes = soup.select(".related-news")
        box = max(boxes, key=lambda b: len(b.select("a[href]")), default=None)
        topics = []
        if box:
            topics = [a.get_text() for a in box.select("h3 a[href*='/knippi/']")]
            for a in box.select("a[href]"):
                href = absolute(url, a.get("href"))
                if href and _ARTICLE_URL.match(href):
                    add_related(related, seen, href, a.get("title") or a.get_text(" "), url)
        article.tags = dedupe(topics)

        article.is_paywalled = bool(soup.select_one("#mnga-wrapper, .mnga"))

        # body, after the junk is removed; inline links in it are related too
        text = body_text(layout, drop=_DROP)
        for a in layout.select("a[href]"):
            href = absolute(url, a.get("href"))
            if href and _ARTICLE_URL.match(href):
                add_related(related, seen, href, a.get_text(" "), url)
        article.related = related
        article.content = text
        return article

    # -------------------------------------------------------------- helpers

    @staticmethod
    def _dateline_labels(soup) -> list[str]:
        dateline = soup.select_one(".dateline")
        if dateline is None:
            return []
        labels = []
        for part in dateline.get_text(" ").split("|"):
            part = clean_inline(part)
            if not part:
                continue
            if _DATE.fullmatch(part) or _TIME.fullmatch(part) or part.startswith("Uppfært"):
                break
            labels.append(part)
        return labels

    @staticmethod
    def _section_slug(url: str) -> str | None:
        parts = [p for p in re.sub(r"^https?://[^/]+", "", url).split("/") if p]
        if not parts:
            return None
        if parts[0] == "frettir" and len(parts) > 1 and not parts[1].isdigit():
            return parts[1]  # frettir/innlent -> "innlent"
        return parts[0]
