import pytest

from bjagur.outlets import Mbl, Ruv, Visir, by_db_name, by_slug
from bjagur.outlets.base import ParseError


# ------------------------------------------------------------------------ Vísir


def test_visir_feed(fixture):
    items = Visir().feed_items(fixture("visir_rss.xml"))
    assert len(items) == 2
    first = items[0]
    assert first.url.endswith("a-attunda-tug-taka-afstodu")
    assert first.title == "Á áttunda tug taka afstöðu til sakar í Landsbankamálinu"  # soft hyphen gone
    assert first.published_at == "2026-11-02T10:14:43Z"
    assert first.feed_category is None  # Vísir's feed has no category


def test_visir_page(fixture):
    url = "https://www.visir.is/g/20262956706d/segir-felagid-i-skitnum"
    a = Visir().parse_page(url, fixture("visir_article.html"))

    assert a.title == "Segir félagið í skítnum"
    assert a.section == "Sport"
    assert a.article_type == "news"
    assert a.authors == ["Valur Páll Eiríksson"]
    assert a.tags == ["Nottingham Forest", "Enski boltinn"]  # meta + tagslist, de-duplicated
    assert a.is_paywalled is False
    assert a.published_at == "2026-11-01T12:01:09Z"
    assert a.modified_at == "2026-11-01T13:30:00Z"
    assert a.image_url == "https://www.visir.is/i/ABC_1600x900.jpg"

    assert [r["url"] for r in a.related] == [
        "https://www.visir.is/g/20262956545d/brentford-bjargadi-united-fra-fallsaeti",
        "https://www.visir.is/g/20262956642d/sjadu-morkin-og-raudu-spjoldin",
    ]

    lines = a.content.split("\n")
    assert lines[0].startswith("Öskureiður Oliver Glasner gaf í skyn að hann gæti gengið á brott frá")
    assert "Glasner sagði að félagið væri „í skítnum“" in a.content  # spaces around <a> preserved
    assert "Hvað næst?" in lines
    assert lines.count("Við verðum að bæta okkur.") == 1  # blockquote > p not duplicated
    assert "Næsti leikur á laugardag" in lines
    assert "Glasner fór mikinn" not in a.content  # figure caption
    assert "AUGLÝSING" not in a.content and "social-share" not in a.content


def test_visir_opinion_and_paywall_detection(fixture):
    html = fixture("visir_article.html")
    html = html.replace("article-single -sport", "article-single -opinion", 1)
    html = html.replace('content="open"', 'content="locked"')
    a = Visir().parse_page("https://www.visir.is/g/1d/x", html)
    assert a.article_type == "opinion"
    assert a.is_paywalled is True


def test_visir_two_authors_from_meta_when_json_ld_missing(fixture):
    import re

    html = re.sub(r'<script type="application/ld\+json">.*?</script>', "", fixture("visir_article.html"), flags=re.S)
    html = html.replace('content="Valur Páll Eiríksson"', 'content="Ragnar Þór Reynisson,Valdimar Víðisson"')
    a = Visir().parse_page("https://www.visir.is/g/1d/x", html)
    assert a.authors == ["Ragnar Þór Reynisson", "Valdimar Víðisson"]


def test_visir_rejects_non_article_pages():
    with pytest.raises(ParseError):
        Visir().parse_page("https://www.visir.is/x", "<html><body>hello</body></html>")


# -------------------------------------------------------------------------- Mbl


def test_mbl_feed(fixture):
    items = Mbl().feed_items(fixture("mbl_rss.xml"))
    assert [i.feed_category for i in items] == ["Íþróttir", "Innlent"]
    sports = items[0]
    assert sports.summary == "Landsliðskonan Sveindís Jane Jónsdóttir skoraði sitt tíunda mark."
    assert sports.image_url == "https://c.arvakur.is/frimg/1/68/54/1685442A.jpg"
    assert sports.published_at == "2026-11-02T10:10:00Z"


def test_mbl_page(fixture):
    url = "https://www.mbl.is/frettir/erlent/2026/11/02/republikanar_eiga_i_vok_ad_verjast/"
    a = Mbl().parse_page(url, fixture("mbl_article.html"))

    assert a.title == "Repúblikanar eiga í vök að verjast"
    assert a.section == "Erlent"
    assert a.section_slug == "erlent"
    assert a.byline_source == "Morgunblaðið"
    assert a.subsection is None
    assert a.article_type == "news"
    assert a.authors == []  # staff piece: the organisation is not an author
    assert a.tags == ["Donald Trump Bandaríkjaforseti"]  # the "knippi" topic bundle
    assert a.published_at == "2026-11-02T08:45:00Z"
    assert a.modified_at == "2026-11-02T10:12:44Z"
    assert a.is_paywalled is True

    urls = [r["url"] for r in a.related]
    assert urls == [
        "https://www.mbl.is/frettir/erlent/2026/11/01/trump_muni_ekki_stjorna_lengd_naesta_strids/",
        "https://www.mbl.is/frettir/erlent/2026/11/01/trump_getum_gert_thad_a_tiu_minutum/",
        "https://www.mbl.is/frettir/erlent/2026/10/31/nordmenn_breyttu_modgun_trumps_i_auglysingu/",
        "https://www.mbl.is/frettir/erlent/2026/11/01/demokratinn_stydur_republikanann_gegn_el_sayed/",
    ]
    assert a.related[0]["title"] == "Trump muni ekki stjórna lengd næsta stríðs"

    assert a.content.split("\n") == [
        "Á morgun ganga Bandaríkjamenn til þingkosninga. Allt bendir til þess að Repúblikanar missi meirihluta sinn.",
        "Í bandarískum þingkosningum á miðju kjörtímabili tapar flokkur forsetans nær alltaf sætum.",
        "Baráttan um öldungadeildina",
        "Demókratar þurfa að ná fjórum sætum til viðbótar.",
    ]
    for junk in ("AUGLÝSING", "GALLERÍ", "áskrift", "Tengdar fréttir", "Nánar um málið", "AFP/Mario"):
        assert junk not in a.content


@pytest.mark.parametrize(
    "dateline, section, subsection, source",
    [
        ("Innlent | mbl | 2.11.2026 | 10:02", "Innlent", None, "mbl"),
        ("Íþróttir | Enski boltinn | mbl | 2.11.2026 | 9:45", "Íþróttir", "Enski boltinn", "mbl"),
        ("Menning | Bækur | Morgunblaðið | 2.11.2026 | 6:25", "Menning", "Bækur", "Morgunblaðið"),
        ("200 mílur | mbl | 2.11.2026 | 9:10 | Uppfært 10:15", "200 mílur", None, "mbl"),
        ("Erlent | AFP | 2.11.2026 | 8:45", "Erlent", None, "AFP"),
        ("Innlent | Spursmál | mbl | 1.11.2026 | 22:45", "Innlent", "Spursmál", "mbl"),
        ("Ferðalög | Ferðalög erlendis | mbl | 2.11.2026 | 8:00", "Ferðalög", "Ferðalög erlendis", "mbl"),
    ],
)
def test_mbl_dateline_variants(fixture, dateline, section, subsection, source):
    html = fixture("mbl_article.html")
    start = html.index('<div class="dateline">')
    end = html.index("</div>", start) + len("</div>")
    html = html[:start] + f'<div class="dateline">{dateline}</div>' + html[end:]
    a = Mbl().parse_page("https://www.mbl.is/frettir/innlent/2026/11/02/x/", html)
    assert (a.section, a.subsection, a.byline_source) == (section, subsection, source)


def test_mbl_signed_piece_and_free_article(fixture):
    html = fixture("mbl_article.html")
    html = html.replace('<meta name="keywords" content="">', '<meta name="cXenseParse:author" content="Ragnheiður Birgisdóttir">')
    html = html.replace("mnga", "wall")  # no paywall box
    a = Mbl().parse_page("https://www.mbl.is/menning/baekur/2026/11/02/x/", html)
    assert a.authors == ["Ragnheiður Birgisdóttir"]
    assert a.is_paywalled is False


def test_mbl_section_slug():
    f = Mbl._section_slug
    assert f("https://www.mbl.is/frettir/innlent/2026/11/02/x/") == "innlent"
    assert f("https://www.mbl.is/sport/fotbolti/2026/11/02/x/") == "sport"
    assert f("https://www.mbl.is/vidskipti/frettir/2026/11/02/x/") == "vidskipti"
    assert f("https://www.mbl.is/200milur/frettir/2026/11/02/x/") == "200milur"


# -------------------------------------------------------------------------- RÚV


def test_ruv_feed(fixture):
    items = Ruv().feed_items(fixture("ruv_rss.xml"))
    assert [i.feed_category for i in items] == ["innlent", "ithrottir"]
    assert items[0].image_url == "https://myndir.ruv.is/hiti.jpg"
    assert items[0].summary.startswith("Hiti verður allt að 15 stigum")
    assert items[0].published_at == "2026-11-02T06:54:25Z"


def test_ruv_page(fixture):
    url = "https://www.ruv.is/frettir/innlent/2026-11-02-adalmedferd-hafin-i-mali-irisar-helgu-491469/"
    a = Ruv().parse_page(url, fixture("ruv_article.html"))

    assert a.title == "Aðalmeðferð hafin í máli Írisar Helgu"
    assert a.section == "Innlendar fréttir" and a.section_slug == "innlent"
    assert a.subsection == "Dómstólar"
    assert a.article_type == "færsla"
    assert a.authors == ["Ragnar Jón Hrólfsson"]
    assert a.tags == ["dómstólar", "Íris Helga Jónatansdóttir"]
    assert a.is_paywalled is False
    assert a.published_at == "2026-11-02T09:25:58Z"
    assert a.modified_at == "2026-11-02T09:31:30Z"
    assert a.image_url == "https://myndir.ruv.is/large.jpg"

    assert [r["url"] for r in a.related] == [
        "https://www.ruv.is/frettir/innlent/2026-04-10-vaknadi-og-sa-andlat-sitt-tilkynnt-i-morgunbladinu-472363/",
        "https://www.ruv.is/frettir/innlent/2026-10-27-70-akaerdir-fyrir-fjarsvik-og-peningathvaetti-491028/",
    ]
    assert a.related[0]["title"].startswith("Vaknaði og sá andlát")

    assert a.content.split("\n") == [
        "Aðalmeðferð er hafin í máli Írisar Helgu Jónatansdóttur, sem er ákærð fyrir umsáturseinelti.",
        "Hún neitar sök.",
        "Tímalínan",
        "2024: Fyrsta kæran",
        "Hvað gerist næst?",
        "Dómur fellur eftir viku.",
        "Fyrsti liður",
        "Annar liður",
    ]


def test_ruv_rejects_pages_without_next_data():
    with pytest.raises(ParseError):
        Ruv().parse_page("https://www.ruv.is/x", "<html><body></body></html>")


# ---------------------------------------------------------------------- registry


def test_registry_lookup():
    assert by_slug("MBL").slug == "mbl"
    assert by_db_name("Vísir").slug == "visir"
    assert by_db_name("visir").slug == "visir"
    assert by_db_name("RÚV").slug == "ruv"
    assert by_db_name("Stundin") is None
    with pytest.raises(SystemExit):
        by_slug("nope")
