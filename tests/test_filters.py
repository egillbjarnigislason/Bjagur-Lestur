from datetime import datetime, timezone

import pytest

from bjagur.filters import DEFAULT_PATH, FilterSet, Rules, evaluate
from bjagur.models import Article


def art(**kw):
    kw.setdefault("url", "https://www.example.is/frettir/innlent/2026/11/02/x/")
    return Article(**kw)


# ------------------------------------------------------------- the shipped filters.yml


@pytest.fixture(scope="module")
def shipped():
    return FilterSet.load(DEFAULT_PATH)


def test_shipped_filters_load_for_every_outlet(shipped):
    for slug, name in (("visir", "Vísir"), ("mbl", "Mbl"), ("ruv", "RÚV")):
        rules, scraping = shipped.for_outlet(slug, name)
        assert rules.skip_paywalled is True
        assert rules.exclude_sections, f"{slug} should exclude sports by default"
        assert scraping.max_articles_per_run > 0


def test_sports_excluded_in_each_outlets_own_vocabulary(shipped):
    visir, _ = shipped.for_outlet("visir")
    mbl, _ = shipped.for_outlet("mbl")
    ruv, _ = shipped.for_outlet("ruv")

    # Vísir: section "Sport" only exists on the article page
    assert evaluate(visir, art(section="Sport")) == "section:Sport"
    assert evaluate(visir, art(section="Frettir")) is None

    # Mbl: RSS category at the feed stage, URL prefix as a second net, dateline label later
    assert evaluate(mbl, art(feed_category="Íþróttir")) == "section:Íþróttir"
    assert evaluate(mbl, art(url="https://www.mbl.is/sport/fotbolti/2026/11/02/x/")) == "url_prefix:/sport/"
    assert evaluate(mbl, art(section="Íþróttir", subsection="Enski boltinn")) == "section:Íþróttir"
    assert evaluate(mbl, art(feed_category="Innlent")) is None

    # RÚV: URL slug at the feed stage, label ("Íþróttir") on the page - both hit
    assert evaluate(ruv, art(feed_category="ithrottir")) == "section:ithrottir"
    assert evaluate(ruv, art(section="Íþróttir", section_slug="ithrottir")) == "section:Íþróttir"
    assert evaluate(ruv, art(section="Innlendar fréttir", section_slug="innlent")) is None


def test_outlet_lists_are_added_to_defaults_and_scalars_replace():
    fs = FilterSet(
        {
            "defaults": {"filters": {"exclude_tags": ["a"], "min_content_chars": 100}},
            "outlets": {"mbl": {"filters": {"exclude_tags": ["b"], "min_content_chars": 5}}},
        }
    )
    rules, _ = fs.for_outlet("mbl", "Mbl")
    assert rules.exclude_tags == ["a", "b"]
    assert rules.min_content_chars == 5
    other, _ = fs.for_outlet("ruv")
    assert other.exclude_tags == ["a"] and other.min_content_chars == 100


def test_typos_in_filters_yml_fail_loudly():
    with pytest.raises(ValueError, match="unknown filters key"):
        FilterSet({"outlets": {"mbl": {"filters": {"exclude_section": ["x"]}}}}).for_outlet("mbl")
    with pytest.raises(ValueError, match="unknown scraping key"):
        FilterSet({"outlets": {"mbl": {"scraping": {"delay": 3}}}}).for_outlet("mbl")
    with pytest.raises(ValueError, match="top-level"):
        FilterSet({"filters": {}})
    with pytest.raises(Exception):  # invalid regex
        FilterSet({"defaults": {"filters": {"exclude_title_regex": ["("]}}}).for_outlet("x")


# ------------------------------------------------------------------------- each knob


def test_matching_ignores_case_accents_and_supports_wildcards():
    r = Rules(exclude_subsections=["fotbolt*"], exclude_tags=["DOMSMAL"])
    assert evaluate(r, art(subsection="Fótboltinn")) == "subsection:Fótboltinn"
    assert evaluate(r, art(tags=["Reykjavík", "Dómsmál"])) == "tag:Dómsmál"
    assert evaluate(r, art(subsection="Handbolti", tags=["Veður"])) is None


def test_include_sections_is_lenient_when_section_unknown():
    r = Rules(include_sections=["Innlent", "Erlent"])
    assert evaluate(r, art(section="Viðskipti")) == "section_not_included:Viðskipti"
    assert evaluate(r, art(section="Innlent")) is None
    assert evaluate(r, art()) is None  # unknown at the feed stage -> keep, decide later


def test_include_tags_any_and_article_types():
    r = Rules(include_tags_any=["Efnahagsmál"], exclude_article_types=["opinion"])
    assert evaluate(r, art(tags=["Veður"])) == "tags_not_included"
    assert evaluate(r, art(tags=["Veður", "efnahagsmál"])) is None
    assert evaluate(r, art(tags=[])) is None
    assert evaluate(r, art(article_type="opinion", tags=["Efnahagsmál"])) == "article_type:opinion"
    r2 = Rules(include_article_types=["news"])
    assert evaluate(r2, art(article_type="recipe")) == "article_type_not_included:recipe"


def test_authors_and_source_labels():
    r = Rules(exclude_authors=["Fréttastofa RÚV"], exclude_source_labels=["AFP"])
    assert evaluate(r, art(authors=["Fréttastofa RÚV"])) == "author:Fréttastofa RÚV"
    assert evaluate(r, art(byline_source="AFP")) == "source:AFP"
    assert evaluate(r, art(byline_source="mbl", authors=["Valur Páll Eiríksson"])) is None


def test_url_and_title_rules():
    r = Rules(
        exclude_url_prefixes=["/k100/"],
        exclude_url_regex=[r"/beint_"],
        exclude_title_regex=["^dagskráin í dag"],
    )
    assert evaluate(r, art(url="https://www.mbl.is/k100/frettir/2026/11/02/x/")) == "url_prefix:/k100/"
    assert evaluate(r, art(url="https://x.is/a/beint_utsending")).startswith("url_regex")
    assert evaluate(r, art(title="Dagskráin í dag: stórleikur")).startswith("title_regex")
    assert evaluate(r, art(title="Fréttir dagsins")) is None


def test_paywall_flag_and_switch():
    assert evaluate(Rules(), art(is_paywalled=True)) == "paywalled"
    assert evaluate(Rules(skip_paywalled=False), art(is_paywalled=True)) is None
    assert evaluate(Rules(), art(is_paywalled=None)) is None


def test_min_content_chars_only_applies_once_text_is_known():
    r = Rules(min_content_chars=50)
    assert evaluate(r, art()) is None  # feed stage: no content yet
    assert evaluate(r, art(content="stutt")) == "too_short:5"
    assert evaluate(r, art(content="x" * 60)) is None


def test_max_age_hours():
    now = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)
    r = Rules(max_age_hours=24)
    old = art(published_at="2026-10-30T12:00:00Z")
    assert evaluate(r, old, feed_stage=True, now=now).startswith("too_old")
    assert evaluate(r, art(published_at="2026-11-02T06:00:00Z"), feed_stage=True, now=now) is None
    assert evaluate(r, art(published_at=None), feed_stage=True, now=now) is None
    # a queued backlog is scraped however old it is
    assert evaluate(r, old, now=now) is None
