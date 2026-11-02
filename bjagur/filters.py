"""
Filter engine. The rules themselves live in `filters.yml` at the repo root.

Filters run at two moments:
  * "feed"    - right after the RSS feed is read, using only what the feed offers
                (title, URL, RSS category, age). Matching articles are never stored.
  * "article" - after the article page is read, using the full metadata
                (section, tags, authors, paywall flag, content length, ...). Matching
                articles are stored with scrape_status 'filtered'/'paywalled' and the
                reason in `filter_reason`, so they are not fetched again.

Rules that need data an article does not have yet are simply skipped, so one rule
set works for both stages. Every name/label comparison goes through `fold()`:
case- and accent-insensitive, and `*` is a wildcard ("Fótbolti*").
"""

from __future__ import annotations

import fnmatch
import re
from dataclasses import dataclass, field, fields
from pathlib import Path

import yaml

from .models import Article
from .text import fold, hours_since

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "filters.yml"

LIST_RULES = (
    "exclude_sections",
    "include_sections",
    "exclude_subsections",
    "exclude_tags",
    "include_tags_any",
    "exclude_article_types",
    "include_article_types",
    "exclude_authors",
    "exclude_source_labels",
    "exclude_url_prefixes",
    "exclude_url_regex",
    "exclude_title_regex",
)


@dataclass
class Rules:
    exclude_sections: list[str] = field(default_factory=list)
    include_sections: list[str] = field(default_factory=list)
    exclude_subsections: list[str] = field(default_factory=list)
    exclude_tags: list[str] = field(default_factory=list)
    include_tags_any: list[str] = field(default_factory=list)
    exclude_article_types: list[str] = field(default_factory=list)
    include_article_types: list[str] = field(default_factory=list)
    exclude_authors: list[str] = field(default_factory=list)
    exclude_source_labels: list[str] = field(default_factory=list)
    exclude_url_prefixes: list[str] = field(default_factory=list)
    exclude_url_regex: list[str] = field(default_factory=list)
    exclude_title_regex: list[str] = field(default_factory=list)
    skip_paywalled: bool = True
    min_content_chars: int = 0
    max_age_hours: float | None = None


@dataclass
class Scraping:
    max_articles_per_run: int = 60
    request_delay_seconds: float | None = None  # None -> the outlet's own default
    max_attempts: int = 3


def _fields(cls) -> set[str]:
    return {f.name for f in fields(cls)}


def _merge(base: dict, extra: dict, list_keys=()) -> dict:
    out = dict(base)
    for key, value in (extra or {}).items():
        if key in list_keys:
            out[key] = list(dict.fromkeys([*(out.get(key) or []), *(value or [])]))
        else:
            out[key] = value
    return out


class FilterSet:
    """All rules from filters.yml, resolved per outlet."""

    def __init__(self, data: dict):
        if not isinstance(data, dict):
            raise ValueError("filters.yml must contain a mapping")
        unknown_top = set(data) - {"defaults", "outlets"}
        if unknown_top:
            raise ValueError(f"filters.yml: unknown top-level keys {sorted(unknown_top)}")
        self._defaults = data.get("defaults") or {}
        self._outlets = {fold(k): v or {} for k, v in (data.get("outlets") or {}).items()}

    @classmethod
    def load(cls, path: str | Path = DEFAULT_PATH) -> "FilterSet":
        with open(path, encoding="utf-8") as fh:
            return cls(yaml.safe_load(fh) or {})

    def for_outlet(self, *names: str) -> tuple[Rules, Scraping]:
        """Rules for an outlet, found by slug or database name ("visir" or "Vísir")."""
        outlet = {}
        for name in names:
            if fold(name) in self._outlets:
                outlet = self._outlets[fold(name)]
                break

        rules = _merge(
            self._defaults.get("filters") or {}, outlet.get("filters") or {}, LIST_RULES
        )
        scraping = _merge(self._defaults.get("scraping") or {}, outlet.get("scraping") or {})

        for label, data, allowed in (
            ("filters", rules, _fields(Rules)),
            ("scraping", scraping, _fields(Scraping)),
        ):
            bad = set(data) - allowed
            if bad:
                raise ValueError(
                    f"filters.yml: unknown {label} key(s) {sorted(bad)}. "
                    f"Allowed: {sorted(allowed)}"
                )
        rules_obj = Rules(**rules)
        for pattern in (*rules_obj.exclude_url_regex, *rules_obj.exclude_title_regex):
            re.compile(pattern)  # fail fast on a bad regex
        return rules_obj, Scraping(**scraping)


# --------------------------------------------------------------------- evaluation


def _first_match(patterns: list[str], values: list[str]) -> str | None:
    """First value (original spelling) matching any pattern, else None."""
    folded = [(v, fold(v)) for v in values if v]
    for pattern in patterns:
        wanted = fold(pattern)
        for original, candidate in folded:
            if fnmatch.fnmatchcase(candidate, wanted):
                return original
    return None


def evaluate(rules: Rules, article: Article, *, feed_stage: bool = False, now=None) -> str | None:
    """
    Return the reason an article must be dropped, or None to keep it.
    A reason equal to "paywalled" means paywall (not a filter) - callers store those
    with status 'paywalled'. `max_age_hours` only applies at the feed stage, so a
    backlog of queued articles is still scraped however old it is.
    """
    path = article.path

    for prefix in rules.exclude_url_prefixes:
        if path.startswith(prefix):
            return f"url_prefix:{prefix}"
    for pattern in rules.exclude_url_regex:
        if re.search(pattern, article.url):
            return f"url_regex:{pattern}"
    if article.title:
        for pattern in rules.exclude_title_regex:
            if re.search(pattern, article.title, re.IGNORECASE):
                return f"title_regex:{pattern}"

    keys = article.section_keys()
    hit = _first_match(rules.exclude_sections, keys)
    if hit:
        return f"section:{hit}"
    if rules.include_sections and keys and not _first_match(rules.include_sections, keys):
        return f"section_not_included:{keys[0]}"

    hit = _first_match(rules.exclude_subsections, [article.subsection or ""])
    if hit:
        return f"subsection:{hit}"

    hit = _first_match(rules.exclude_article_types, [article.article_type or ""])
    if hit:
        return f"article_type:{hit}"
    if (
        rules.include_article_types
        and article.article_type
        and not _first_match(rules.include_article_types, [article.article_type])
    ):
        return f"article_type_not_included:{article.article_type}"

    hit = _first_match(rules.exclude_source_labels, [article.byline_source or ""])
    if hit:
        return f"source:{hit}"

    hit = _first_match(rules.exclude_tags, article.tags)
    if hit:
        return f"tag:{hit}"
    if rules.include_tags_any and article.tags and not _first_match(
        rules.include_tags_any, article.tags
    ):
        return "tags_not_included"

    hit = _first_match(rules.exclude_authors, article.authors)
    if hit:
        return f"author:{hit}"

    if feed_stage and rules.max_age_hours is not None:
        age = hours_since(article.published_at, now)
        if age is not None and age > rules.max_age_hours:
            return f"too_old:{age:.0f}h"

    if rules.skip_paywalled and article.is_paywalled:
        return "paywalled"

    if rules.min_content_chars and article.content is not None:
        if len(article.content) < rules.min_content_chars:
            return f"too_short:{len(article.content)}"

    return None
