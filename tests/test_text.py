from bjagur.text import clean_inline, dedupe, fold, parse_datetime, strip_html


def test_fold_matches_labels_and_slugs():
    assert fold("Íþróttir") == fold("ithrottir") == fold("ÍÞRÓTTIR") == "ithrottir"
    assert fold("Fréttir") == fold("Frettir")
    assert fold("Þetta þrennt...") == "thetta thrennt..."
    assert fold(None) == ""


def test_clean_inline_removes_soft_hyphens_and_collapses_space():
    assert clean_inline("Segir fé­lagið \n í skítnum") == "Segir félagið í skítnum"


def test_strip_html_keeps_spaces_around_inline_tags():
    assert strip_html('<img src="x"/> Hann <b>sagði</b>, að') == "Hann sagði, að"


def test_dedupe_ignores_case_and_accents():
    assert dedupe(["Enski boltinn", "enski boltinn", " ", "Dómsmál"]) == ["Enski boltinn", "Dómsmál"]


def test_parse_datetime_formats():
    assert parse_datetime("2026-11-02T10:02:00+0000") == "2026-11-02T10:02:00Z"
    assert parse_datetime("2026-11-02T10:12:44.408077+0000") == "2026-11-02T10:12:44Z"
    assert parse_datetime("2026-11-02T10:12:44.4080770") == "2026-11-02T10:12:44Z"  # 7 digits, no tz
    assert parse_datetime("2026-11-02T09:25:58.261000Z") == "2026-11-02T09:25:58Z"
    assert parse_datetime("2.11.2026 10:03:43 +00:00") == "2026-11-02T10:03:43Z"
    assert parse_datetime("2.11.2026 12:03:43 +02:00") == "2026-11-02T10:03:43Z"
    assert parse_datetime("not a date") is None
    assert parse_datetime(None) is None
