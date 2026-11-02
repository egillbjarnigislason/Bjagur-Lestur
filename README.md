# Bjagur-Lestur

Collects Icelandic news (Vísir, Mbl, RÚV) into Supabase, on GitHub Actions.

```
every ~10 min   Check feeds ── reads the 3 RSS feeds (seconds), applies feed-stage filters,
                    │          queues new articles in News as 'pending'
                    ▼
                for each outlet with pending articles:
                Scrape outlet (visir | mbl | ruv, in parallel)
                    fetch page → parse text + author/tags/related → apply filters.yml → save
```

* **One scraper per outlet**: `bjagur/outlets/visir.py`, `mbl.py`, `ruv.py`.
* **One filter file**: [`filters.yml`](filters.yml), with a separate rule set per outlet
  because each site names its categories differently.
* **No extra tables**: authors and tags are `text[]` columns, related articles a `jsonb`
  column, all on the existing `News` row.

## First-time setup

0. **Move the workflows into place.** GitHub only runs workflows from `.github/workflows/`, and
   that folder could not be written from the tool that created these files, so the two workflow
   files are in `github-workflows/`. From the repo root:
   ```bash
   mkdir -p .github/workflows
   git mv github-workflows/*.yml .github/workflows/ 2>/dev/null || mv github-workflows/*.yml .github/workflows/
   rmdir github-workflows
   ```
1. **Run the migration** — paste [`migrations/001_news_metadata.sql`](migrations/001_news_metadata.sql)
   into the Supabase SQL editor and run it once. It only adds columns, indexes and a status
   constraint; existing rows are kept (already-scraped rows become `scrape_status = 'done'`).
2. **Add two repository secrets** (Settings → Secrets and variables → Actions):
   `SUPABASE_URL` and `SUPABASE_KEY` (the **service-role** key, because `News` has RLS on).
   The repo is public, so never commit keys; `.env` is git-ignored.
3. **Push** this folder. In the Actions tab, run **Scrape outlet** by hand once per outlet with
   *dry_run* ticked and *limit* 5 to look at the logs, then untick dry-run. After that
   **Check feeds** takes over on its own schedule.

## What was found on each site

These structures were mapped from live pages (≈110 Vísir, 60 Mbl, 40 RÚV articles across all
sections) and are documented at the top of each scraper file.

| | Vísir | Mbl | RÚV |
|---|---|---|---|
| **RSS gives** | title, link, summary, date | + `<category>`, image | + image, full text; `dc:creator` is always "Fréttastofa RÚV" |
| **Section** | `<meta article:section>`: Frettir, Lifid, Skodun, Sport, Vidskipti (page only, not in RSS/URL) | RSS category + the page's `.dateline` (`Íþróttir \| Enski boltinn \| mbl`) + URL path | `parent` in the page's embedded JSON (`innlent`, `erlent`, `ithrottir`, `menning-og-daegurmal`; also in the URL) |
| **Subsection** | – | dateline's middle label | `topic.name` (Handbolti, Dómstólar, Veður…) |
| **Authors** | JSON-LD / `article:author` (1–2 names) | `cXenseParse:author` / JSON-LD Person on signed pieces only; staff news has none (`span.credit` is a *photo* credit) | `authors[]` |
| **Tags** | `<meta article:tag>` + `.tagslist` (0–5) | the "Tengdar fréttir" topic bundle name | `tags[]` |
| **Related** | inline embeds linking to other `/g/` articles (no titles) | "Tengdar fréttir" box + inline links, with titles | `internal_embed_block` cards + `related_pages[]`, with titles |
| **Paywall** | `k5a:paywall` meta (`open` on all 109 sampled) | `#mnga-wrapper` – 5 of 60 sampled, all "Morgunblaðið"-sourced | none |
| **Article type** | `opinion` (section Skodun) or `news` | `recipe` (JSON-LD) or `news` | `content_type`: færsla, fréttamoli |
| **Body** | `.article-single__content` | `.main-layout` (ads, galleries, paywall box, mobile related copy removed) | `body[]` blocks in `__NEXT_DATA__` JSON |

New columns on `News` (all filled for every scraped article, even filtered ones):
`authors`, `tags`, `related` (`[{"url","title"}]`), `section`, `subsection`, `article_type`,
`byline_source` (Mbl), `image_url`, `modified_at`, `is_paywalled`, plus the queue columns
`scrape_status`, `filter_reason`, `scrape_attempts`, `scraped_at`.

`scrape_status`: `pending` → `done` · `filtered` (see `filter_reason`) · `paywalled` · `failed`.
`read_article` is `true` only for `done`; `content is not null` is still the right test
for "has text".

## Filters

Open [`filters.yml`](filters.yml): every knob is documented at the top, and each outlet's
section lists the real category names that site uses. Out of the box, sports are excluded
everywhere:

```yaml
outlets:
  visir: { filters: { exclude_sections: [Sport] } }
  mbl:   { filters: { exclude_sections: [Íþróttir], exclude_url_prefixes: ["/sport/"] } }
  ruv:   { filters: { exclude_sections: [Íþróttir] } }          # matches slug "ithrottir" too
```

Knobs: sections, subsections, tags (exclude / require-any), article types, authors, Mbl source
labels, URL prefixes and regexes, title regexes, paywall on/off, minimum text length,
maximum feed-item age, and per-outlet scraping limits (articles per run, delay between
requests). Names match ignoring case and accents, and `*` is a wildcard. A typo in a key makes
the run fail with a clear message instead of silently doing nothing.

Rules at the **feed** stage (Mbl category, URL, title, age) stop an article before it is stored.
Rules that need the page (Vísir's section, tags, authors, paywall, length) mark the article
`filtered` with the reason, so it is not fetched again. Filtering is not retroactive: existing
rows (for example old sports articles) stay as they are.

## Things worth knowing

* **Paywalled Mbl articles** (Morgunblaðið pieces): the text is present in the page HTML behind
  a client-side wall. By default they are recorded as `paywalled` and the text is **not** stored.
  `skip_paywalled: false` stores it; consider the site's terms before doing that.
* **Mbl rate limit**: mbl.is answers 429 to fast requests, and 403 to the default
  python-requests User-Agent. The scraper waits `request_delay_seconds` (10 s) between pages,
  backs off automatically on 429, and uses a browser-style User-Agent. A run is capped at 40
  Mbl articles; the rest wait for the next run, newest first.
* **GitHub's cron is best-effort.** `Check feeds` is scheduled about every 10 minutes but can
  start 5–30 minutes late, or be skipped when GitHub is busy. Public repos have no minute quota.
* **Text extraction fix**: the earlier collector joined text with `get_text(strip=True)`, which
  can glue words together around links and bold text. This version keeps the spaces.
* **Old rows**: to fill the new columns on articles scraped before this change, run
  *Scrape outlet* with *backfill* ticked (slow for Mbl because of the delay; use *limit*).

## Running locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # fill in SUPABASE_URL / SUPABASE_KEY
pytest                          # 52 tests, no network needed
python -m bjagur.check_feeds --dry-run
python -m bjagur.scrape_outlet ruv --limit 3 --dry-run
```

## Adding an outlet

1. Add a row to the Supabase `Outlets` table (`name`, `rss_feed`).
2. Write `bjagur/outlets/<name>.py` (subclass `OutletScraper`: `feed_items` + `parse_page`), add it
   to `SCRAPERS` in `bjagur/outlets/__init__.py`.
3. Add its rules to `filters.yml` and its slug to the `outlet` options in
   `.github/workflows/scrape-outlet.yml`.

## Layout

```
filters.yml                       all filter rules (edit this)
migrations/001_news_metadata.sql  one-time schema change
bjagur/check_feeds.py             RSS check + queue (entry point)
bjagur/scrape_outlet.py           per-outlet scrape (entry point)
bjagur/filters.py                 rule engine behind filters.yml
bjagur/outlets/                   visir.py · mbl.py · ruv.py · base.py
bjagur/db.py  http.py  text.py  models.py
.github/workflows/                check-feeds.yml · scrape-outlet.yml
tests/                            fixtures + 52 tests
```
