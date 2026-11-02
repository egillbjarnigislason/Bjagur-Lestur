"""
check_feeds and scrape_outlet end to end, with the network and Supabase replaced by
fakes. The fixtures are small pages shaped like the real ones.
"""

import json
from datetime import datetime, timezone

import pytest

from bjagur import check_feeds, db, scrape_outlet
from bjagur.filters import FilterSet
from bjagur.http import FetchError
from bjagur.outlets import Mbl, Ruv, Visir


NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)  # fixtures are dated this day


class FakeResponse:
    def __init__(self, body: str):
        self.text = body
        self.content = body.encode("utf-8")


class FakeHttp:
    """Maps URL -> body, or an exception to raise."""

    delay = 0

    def __init__(self, pages):
        self.pages = pages

    def get(self, url):
        result = self.pages[url]
        if isinstance(result, Exception):
            raise result
        return FakeResponse(result)


class FakeDb:
    """Just enough of bjagur.db for the two entry points."""

    def __init__(self, existing=(), pending=0):
        self.existing = set(existing)
        self.pending = pending
        self.inserted = []
        self.updates = {}

    def existing_urls(self, urls):
        return self.existing & set(urls)

    def insert_pending(self, outlet_id, articles):
        self.inserted.extend(articles)

    def count_pending(self, outlet_id):
        return self.pending + len(self.inserted)


@pytest.fixture
def filters():
    return FilterSet.load()


# ---------------------------------------------------------------------- check_feeds


def test_check_feeds_queues_new_articles_and_drops_sports(monkeypatch, fixture, filters):
    fake = FakeDb()
    monkeypatch.setattr(check_feeds, "db", fake)
    http = FakeHttp({"https://www.mbl.is/feeds/nyjast": fixture("mbl_rss.xml")})

    pending = check_feeds.check_outlet(
        Mbl(), {"id": 2, "rss_feed": "https://www.mbl.is/feeds/nyjast"}, filters, http=http, now=NOW
    )

    assert [a.url for a in fake.inserted] == [
        "https://www.mbl.is/frettir/innlent/2026/11/02/thingsetning_i_landsbankamali_klidur_i_heradsdomi/"
    ]  # the Íþróttir item never reaches the database
    assert pending == 1


def test_check_feeds_skips_known_urls_and_reports_backlog(monkeypatch, fixture, filters):
    known = {
        "https://www.visir.is/g/20262957052d/a-attunda-tug-taka-afstodu",
        "https://www.visir.is/g/20262956939d/thjalfari-hakons-gret-eftir-leik",
    }
    fake = FakeDb(existing=known, pending=3)
    monkeypatch.setattr(check_feeds, "db", fake)
    http = FakeHttp({"https://www.visir.is/rss/allt": fixture("visir_rss.xml")})
    pending = check_feeds.check_outlet(
        Visir(), {"id": 1, "rss_feed": "https://www.visir.is/rss/allt"}, filters, http=http, now=NOW
    )
    assert fake.inserted == []
    assert pending == 3  # nothing new, but 3 older articles are still waiting


def test_check_feeds_dry_run_writes_nothing(monkeypatch, fixture, filters):
    fake = FakeDb()
    monkeypatch.setattr(check_feeds, "db", fake)
    http = FakeHttp({"https://www.ruv.is/rss/frettir": fixture("ruv_rss.xml")})
    check_feeds.check_outlet(
        Ruv(), {"id": 3, "rss_feed": "https://www.ruv.is/rss/frettir"}, filters, dry_run=True, http=http, now=NOW
    )
    assert fake.inserted == []


def test_check_feeds_main_writes_github_output(monkeypatch, tmp_path, fixture, filters):
    out = tmp_path / "gh_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    fake = FakeDb()
    fake.get_outlets = lambda: [
        {"id": 1, "name": "Vísir", "rss_feed": "https://www.visir.is/rss/allt"},
        {"id": 3, "name": "RÚV", "rss_feed": "https://www.ruv.is/rss/frettir"},
        {"id": 9, "name": "Stundin", "rss_feed": "https://x.is/rss"},  # no scraper -> skipped
    ]
    monkeypatch.setattr(check_feeds, "db", fake)
    pages = {
        "https://www.visir.is/rss/allt": FetchError("boom"),  # one feed down
        "https://www.ruv.is/rss/frettir": fixture("ruv_rss.xml"),
    }
    monkeypatch.setattr(check_feeds, "Http", lambda **kw: FakeHttp(pages))
    # RÚV items in the fixture are from 2 Nov 2026; keep the age rule out of the way
    monkeypatch.setattr(check_feeds.FilterSet, "load", classmethod(lambda cls, *a: FilterSet({"outlets": {"ruv": {"filters": {"max_age_hours": None}}}})))

    assert check_feeds.main([]) == 0  # one outlet worked, so the run is not a failure
    assert out.read_text().strip() == 'outlets=["ruv"]'


# ------------------------------------------------------------------- scrape_outlet


def row(url, **kw):
    return {"id": 1, "url": url, "title": None, "summary": None, "published_at": None,
            "image_url": None, "scrape_attempts": 0, **kw}


def scrape(scraper, url, body, filters, **kw):
    rules, scraping = filters.for_outlet(scraper.slug, scraper.db_name)
    http = FakeHttp({url: body})
    return scrape_outlet.process_row(scraper, row(url, **kw), rules, scraping, http)


def test_scrape_saves_content_and_metadata(fixture, filters):
    url = "https://www.visir.is/g/20262956706d/x"
    html = fixture("visir_article.html").replace("Sport", "Frettir", 1)  # a non-sports article
    label, fields = scrape(Visir(), url, html, filters)
    assert label.startswith("done")
    assert fields["scrape_status"] == "done" and fields["read_article"] is True
    assert fields["authors"] == ["Valur Páll Eiríksson"]
    assert fields["tags"] == ["Nottingham Forest", "Enski boltinn"]
    assert fields["title"] == "Segir félagið í skítnum"  # row had no title -> filled in
    assert "Glasner" in fields["content"]
    assert len(fields["related"]) == 2


def test_scrape_does_not_overwrite_rss_values(fixture, filters):
    url = "https://www.visir.is/g/1d/x"
    html = fixture("visir_article.html").replace("Sport", "Frettir", 1)
    _, fields = scrape(Visir(), url, html, filters, title="Titill úr RSS", published_at="2026-11-01T10:00:00Z")
    assert "title" not in fields and "published_at" not in fields


def test_scrape_filters_sports_and_keeps_metadata_but_no_text(fixture, filters):
    url = "https://www.visir.is/g/20262956706d/x"
    label, fields = scrape(Visir(), url, fixture("visir_article.html"), filters)  # section Sport
    assert label.startswith("filtered")
    assert fields["scrape_status"] == "filtered"
    assert fields["filter_reason"] == "section:Sport"
    assert fields["content"] is None and fields["read_article"] is False
    assert fields["section"] == "Sport" and fields["tags"]  # still recorded


def test_scrape_marks_paywalled_without_storing_text(fixture, filters):
    url = "https://www.mbl.is/frettir/erlent/2026/11/02/republikanar_eiga_i_vok_ad_verjast/"
    label, fields = scrape(Mbl(), url, fixture("mbl_article.html"), filters)
    assert fields["scrape_status"] == "paywalled" and fields["filter_reason"] == "paywalled"
    assert fields["content"] is None
    assert fields["byline_source"] == "Morgunblaðið"


def test_paywall_can_be_switched_off_in_filters_yml(fixture):
    fs = FilterSet({"outlets": {"mbl": {"filters": {"skip_paywalled": False}}}})
    url = "https://www.mbl.is/frettir/erlent/2026/11/02/x/"
    _, fields = scrape(Mbl(), url, fixture("mbl_article.html"), fs)
    assert fields["scrape_status"] == "done" and "Demókratar" in fields["content"]


def test_scrape_short_article_is_filtered(fixture):
    fs = FilterSet({"defaults": {"filters": {"min_content_chars": 10_000}}})
    html = fixture("visir_article.html").replace("Sport", "Frettir", 1)
    _, fields = scrape(Visir(), "https://www.visir.is/g/1d/x", html, fs)
    assert fields["scrape_status"] == "filtered" and fields["filter_reason"].startswith("too_short")


def test_video_only_page_is_filtered_as_no_text(fixture, filters):
    page = json.loads(fixture("ruv_article.html").split('type="application/json">')[1].split("</script>")[0])
    page["props"]["pageProps"]["data"]["article"]["body"] = [{"block_type": "clip_block"}]
    html = f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(page)}</script>'
    url = "https://www.ruv.is/frettir/innlent/2026-11-02-x-1/"
    _, fields = scrape(Ruv(), url, html, FilterSet({}))
    assert fields["scrape_status"] == "filtered" and fields["filter_reason"] == "no_text"


def test_fetch_failures_retry_then_fail(filters):
    url = "https://www.visir.is/g/1d/x"
    rules, scraping = filters.for_outlet("visir")
    flaky = FakeHttp({url: FetchError("timeout")})

    label, fields = scrape_outlet.process_row(Visir(), row(url, scrape_attempts=0), rules, scraping, flaky)
    assert fields == {"scrape_attempts": 1, "scrape_status": "pending"}

    _, fields = scrape_outlet.process_row(Visir(), row(url, scrape_attempts=2), rules, scraping, flaky)
    assert fields["scrape_status"] == "failed"  # third attempt = max_attempts

    gone = FakeHttp({url: FetchError("404", 404, permanent=True)})
    _, fields = scrape_outlet.process_row(Visir(), row(url), rules, scraping, gone)
    assert fields["scrape_status"] == "failed"


def test_unparseable_page_is_retried_then_failed(filters):
    url = "https://www.visir.is/g/1d/x"
    rules, scraping = filters.for_outlet("visir")
    http = FakeHttp({url: "<html><body>maintenance</body></html>"})
    _, fields = scrape_outlet.process_row(Visir(), row(url), rules, scraping, http)
    assert fields["scrape_status"] == "pending"
    _, fields = scrape_outlet.process_row(Visir(), row(url, scrape_attempts=2), rules, scraping, http)
    assert fields["scrape_status"] == "failed"


def test_backfill_adds_metadata_without_touching_content_or_status(fixture, filters):
    url = "https://www.visir.is/g/20262956706d/x"
    rules, scraping = filters.for_outlet("visir")
    http = FakeHttp({url: fixture("visir_article.html")})  # a Sport article: would normally be filtered
    label, fields = scrape_outlet.process_row(Visir(), row(url, title="x"), rules, scraping, http, backfill=True)
    assert label == "backfilled"
    assert "content" not in fields and "scrape_status" not in fields and "read_article" not in fields
    assert fields["authors"] == ["Valur Páll Eiríksson"] and fields["scraped_at"]


def test_db_module_imports_without_credentials():
    assert callable(db.now_iso)  # the client is created lazily, so importing needs no secrets
