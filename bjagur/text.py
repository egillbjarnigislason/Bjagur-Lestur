"""Text and date helpers shared by the scrapers and the filter engine."""

from __future__ import annotations

import html
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

_ICELANDIC = str.maketrans(
    {"þ": "th", "Þ": "th", "ð": "d", "Ð": "d", "æ": "ae", "Æ": "ae", "ö": "o", "Ö": "o"}
)
# soft hyphen, zero-width space/joiners, word joiner, BOM
_INVISIBLE = dict.fromkeys(map(ord, "­​‌‍⁠﻿"), None)


def fold(value: str | None) -> str:
    """
    Accent- and case-insensitive form used for ALL filter comparisons.

    fold("Íþróttir") == fold("ithrottir") == "ithrottir", so config values can be
    written naturally in Icelandic or copied from a URL slug and still match.
    """
    if not value:
        return ""
    value = value.translate(_ICELANDIC)
    value = unicodedata.normalize("NFKD", value)
    value = "".join(c for c in value if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", value).strip().casefold()


def clean_inline(value: str | None) -> str:
    """Unescape entities, drop invisible characters, collapse all whitespace."""
    if not value:
        return ""
    value = html.unescape(value).translate(_INVISIBLE).replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def strip_html(markup: str | None) -> str:
    """Plain text of an HTML fragment (used for RSS descriptions)."""
    if not markup:
        return ""
    text = clean_inline(BeautifulSoup(markup, "html.parser").get_text(" "))
    # get_text(" ") puts a space after inline tags; take it back off before punctuation
    return re.sub(r"\s+([,.;:!?)\]])", r"\1", text)


def first_image(markup: str | None) -> str | None:
    """src of the first <img> in an HTML fragment, if any."""
    if not markup:
        return None
    img = BeautifulSoup(markup, "html.parser").find("img")
    return img.get("src") if img and img.get("src") else None


def dedupe(values) -> list[str]:
    """Order-preserving de-duplication that ignores case/accents and empties."""
    seen: set[str] = set()
    out: list[str] = []
    for v in values:
        v = clean_inline(v)
        key = fold(v)
        if v and key not in seen:
            seen.add(key)
            out.append(v)
    return out


# --------------------------------------------------------------------------- dates

_ISO = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2}))?(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?$"
)
# Vísir's <meta property="article:published_time"> uses "2.11.2026 10:03:43 +00:00"
_IS_DATE = re.compile(
    r"^(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(Z|[+-]\d{2}:?\d{2})?$"
)


def _tz(token: str | None) -> timezone:
    if not token or token == "Z":
        return timezone.utc
    sign = 1 if token[0] == "+" else -1
    digits = token[1:].replace(":", "")
    return timezone(sign * timedelta(hours=int(digits[:2]), minutes=int(digits[2:])))


def parse_datetime(value: str | None) -> str | None:
    """
    Parse the date formats these three sites use and return UTC ISO-8601
    ("2026-11-02T10:03:43Z"), or None when the value can't be understood.
    A missing timezone is treated as UTC.
    """
    if not value:
        return None
    value = value.strip()
    m = _ISO.match(value)
    if m:
        y, mo, d, hh, mm, ss, tz = m.groups()
        parts = (int(y), int(mo), int(d), int(hh or 0), int(mm or 0), int(ss or 0))
    else:
        m = _IS_DATE.match(value)
        if not m:
            return None
        d, mo, y, hh, mm, ss, tz = m.groups()
        parts = (int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0))
    try:
        dt = datetime(*parts, tzinfo=_tz(tz))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hours_since(iso: str | None, now: datetime | None = None) -> float | None:
    """Hours between an ISO timestamp (as produced above) and now."""
    if not iso:
        return None
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None
    now = now or datetime.now(timezone.utc)
    return (now - dt).total_seconds() / 3600
