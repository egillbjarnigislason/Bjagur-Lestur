-- 001_news_metadata.sql
--
-- Adds the columns the new scrapers fill in. Run ONCE in the Supabase SQL editor
-- (or `supabase db push`) BEFORE the first workflow run.
--
-- Design notes
--   * Lists live in a single row: authors/tags are text[]; related articles are a
--     jsonb array of {"url": "...", "title": "..."} objects. No extra tables.
--   * `scrape_status` is the new work-queue column the scrapers use:
--         pending    -> waiting to be scraped
--         done       -> content + metadata stored
--         filtered   -> skipped by a rule in filters.yml (see `filter_reason`)
--         paywalled  -> skipped because the article is behind a paywall
--         failed     -> gave up (404/410, or too many failed attempts)
--   * `read_article` is kept for compatibility and is TRUE only when status = 'done'.
--     Downstream jobs (clustering, tone scoring) should keep selecting on
--     `content is not null`, which is true only for scraped articles.
--   * Row Level Security is untouched. The scrapers use the service-role key, which
--     bypasses RLS; the website keeps reading through your existing policies.

begin;

alter table public."News"
  add column if not exists authors         text[]      not null default '{}',
  add column if not exists tags            text[]      not null default '{}',
  add column if not exists related         jsonb       not null default '[]'::jsonb,
  add column if not exists section         text,
  add column if not exists subsection      text,
  add column if not exists article_type    text,
  add column if not exists byline_source   text,
  add column if not exists image_url       text,
  add column if not exists modified_at     timestamptz,
  add column if not exists is_paywalled    boolean,
  add column if not exists scrape_status   text        not null default 'pending',
  add column if not exists filter_reason   text,
  add column if not exists scrape_attempts integer     not null default 0,
  add column if not exists scraped_at      timestamptz;

-- Existing rows: anything already scraped is 'done', the rest go back in the queue.
-- (scraped_at stays NULL for these, which is how `--backfill` finds rows whose new
--  metadata columns still need filling.)
update public."News"
   set scrape_status = case when read_article and content is not null then 'done'
                            else 'pending' end;

alter table public."News"
  drop constraint if exists news_scrape_status_check;
alter table public."News"
  add constraint news_scrape_status_check
  check (scrape_status in ('pending', 'done', 'filtered', 'paywalled', 'failed'));

-- Work-queue lookups: "oldest/newest pending rows for outlet X".
create index if not exists news_pending_idx
  on public."News" (outlet_id, published_at desc)
  where scrape_status = 'pending';

-- Tag searches on the website: where tags @> '{Dómsmál}'
create index if not exists news_tags_gin
  on public."News" using gin (tags);

commit;
