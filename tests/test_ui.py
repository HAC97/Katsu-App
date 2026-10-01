"""Gate tests for the UI layer: routes, templates, static assets.

Each bug from the design review has a regression test that fails on the old code.
"""
import os
import re
import tempfile
from pathlib import Path

import pytest

_DB_DIR = tempfile.mkdtemp()
os.environ["DATABASE_PATH"] = str(Path(_DB_DIR) / "test.db")

from fastapi.testclient import TestClient  # noqa: E402

import app as app_module  # noqa: E402
import database  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
client = TestClient(app_module.app)


def _story(n, **over):
    data = {
        "title": f"Historia {n}",
        "content": "Primer párrafo.\nSegundo párrafo.",
        "author": "autor",
        "source": "reddit",
        "source_url": f"https://reddit.com/r/x/{n}",
        "sub_source": "nosleep",
        "score": n,
        "comment_count": n,
        "category": "horror",
        "published_at": "2026-09-01T10:00:00",
    }
    data.update(over)
    return data


@pytest.fixture(autouse=True)
def fresh_db():
    database.delete_all_stories()
    database.init_db()
    yield


def _ids(count, **over):
    return [database.insert_story(_story(i, **over)) for i in range(count)]


# --- bug 1: favorite handler must be bound exactly once -------------------

def test_favorite_handler_is_not_duplicated_inline():
    (sid,) = _ids(1)
    html = client.get(f"/stories/{sid}").text
    assert "favorite-btn').forEach" not in html, "inline handler duplicates static/script.js"
    assert "<script>" not in html
    assert "/static/script.js" in html


def test_favorite_endpoint_toggles_once_per_call():
    (sid,) = _ids(1)
    assert client.post(f"/stories/{sid}/favorite").json()["is_favorite"] is True
    assert client.post(f"/stories/{sid}/favorite").json()["is_favorite"] is False


def test_favorite_unknown_story_is_404():
    assert client.post("/stories/99999/favorite").status_code == 404


def test_favorite_button_exposes_pressed_state():
    (sid,) = _ids(1)
    assert 'aria-pressed="false"' in client.get(f"/stories/{sid}").text
    client.post(f"/stories/{sid}/favorite")
    assert 'aria-pressed="true"' in client.get(f"/stories/{sid}").text


# --- bug 2: empty state must not GET the POST-only /fetch -----------------

def test_empty_state_has_no_get_link_to_fetch():
    html = client.get("/stories?search=nothingmatchesthis").text
    assert 'href="/fetch"' not in html
    assert re.search(r'<form method="POST" action="/fetch"', html)
    assert client.get("/fetch").status_code == 405  # documents why


# --- bug 3: pagination URLs are encoded and drop defaults -----------------

def test_build_query_encodes_and_omits_defaults():
    assert app_module.build_query() == "/stories"
    assert app_module.build_query(search="a&b c", page=2) == "/stories?search=a%26b+c&page=2"
    q = app_module.build_query(category="horror", favorite=True, page=3)
    assert q == "/stories?category=horror&favorite=true&page=3"
    assert "True" not in q and "False" not in q


def test_pagination_links_keep_filters_encoded():
    _ids(45, content="needle & thread")
    html = client.get("/stories?search=needle+%26+thread").text
    hrefs = re.findall(r'href="(/stories\?[^"]*page=\d+[^"]*)"', html)
    assert hrefs, "expected pagination links"
    for h in hrefs:
        assert "search=needle+%26+thread" in h.replace("&amp;", "&")
        assert "favorite=False" not in h and "favorite=True" not in h


def test_pagination_favorite_flag_roundtrips():
    ids = _ids(45)
    for sid in ids:
        client.post(f"/stories/{sid}/favorite")
    html = client.get("/stories?favorite=true").text
    assert "favorite=true" in html and "page=2" in html
    assert client.get("/stories?favorite=true&page=2").status_code == 200


# --- bug 4: dead 4chan leftovers ------------------------------------------

def test_no_4chan_leftovers():
    assert "4chan" not in (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert "4chan" not in client.get("/stories").text


# --- design: back link, labels, accents, a11y -----------------------------

def test_back_link_preserves_listing_filters():
    (sid,) = _ids(1)
    ref = "http://testserver/stories?category=horror&search=a+b&page=2"
    html = client.get(f"/stories/{sid}", headers={"referer": ref}).text
    assert 'href="/stories?category=horror&amp;search=a+b&amp;page=2"' in html


@pytest.mark.parametrize("ref", [
    None,
    "https://evil.example/stories?x=1",
    "http://testserver/other",
    "javascript:alert(1)",
])
def test_back_link_ignores_foreign_referers(ref):
    (sid,) = _ids(1)
    headers = {"referer": ref} if ref else {}
    html = client.get(f"/stories/{sid}", headers=headers).text
    assert 'href="/stories" class="btn btn-outline btn-sm detail-back"' in html


def test_safe_back_url_handles_bad_page():
    assert app_module.safe_back_url("http://h/stories?page=abc") == "/stories"


def test_categories_are_labelled_in_spanish():
    _ids(1)
    html = client.get("/").text
    assert "Terror" in html
    assert "badge-horror\">horror<" not in html
    assert app_module.category_label("conspiracy") == "Conspiración"


def test_spanish_accents_present():
    html = client.get("/").text
    for word in ("Estadísticas", "teorías", "fenómenos"):
        assert word in html
    for bad in ("Estadisticas", "Categoria", "Ultimo escaneo", "fenomenos", "boton"):
        assert bad not in html


def test_nav_marks_current_page():
    home = client.get("/").text
    assert re.search(r'<a href="/" aria-current="page">Inicio', home)
    stories = client.get("/stories").text
    assert re.search(r'<a href="/stories" aria-current="page">Historias', stories)
    favs = client.get("/stories?favorite=true").text
    assert re.search(r'href="/stories\?favorite=true" aria-current="page"', favs)
    assert 'href="/stories" aria-current' not in favs


def test_base_has_a11y_and_meta():
    html = client.get("/").text
    assert 'class="skip-link"' in html and 'id="contenido"' in html
    assert 'name="theme-color"' in html and 'rel="icon"' in html


def test_no_inline_styles_in_templates():
    for f in (ROOT / "templates").glob("*.html"):
        assert 'style="' not in f.read_text(encoding="utf-8"), f.name


def test_card_shows_subreddit_on_home_and_list():
    _ids(1)
    assert "r/nosleep" in client.get("/").text
    assert "r/nosleep" in client.get("/stories").text


def test_fetch_is_prominent_in_hero():
    html = client.get("/").text
    assert html.index('id="fetch-btn"') < html.index("Estadísticas")


def test_datetime_and_reading_filters():
    assert app_module.format_datetime("2026-09-29T14:03:11") == "29/09/2026 14:03"
    assert app_module.format_datetime(None) == "?"
    assert app_module.format_date("2026-09-29 14:03:11") == "29/09/2026"
    assert app_module.reading_minutes("") == 1
    assert app_module.reading_minutes("palabra " * 600) == 3
    (sid,) = _ids(1)
    assert "1 min de lectura" in client.get(f"/stories/{sid}").text


def test_not_found_detail_renders_404():
    r = client.get("/stories/424242")
    assert r.status_code == 404 and "Historia no encontrada" in r.text


# --- static asset guards --------------------------------------------------

def test_css_a11y_rules_present():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert ".sr-only" in css
    assert "line-clamp" in css
    assert "max-width: 68ch" in css


def _luminance(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _theme_blocks(css):
    """Token dicts for every CSS block that defines --bg-primary (light, and dark twice)."""
    blocks = []
    for chunk in css.split("}"):
        if "--bg-primary:" in chunk:
            blocks.append(dict(re.findall(r"--([a-z-]+):\s*(#[0-9a-fA-F]{6});", chunk)))
    return blocks


def test_both_themes_are_defined():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    blocks = _theme_blocks(css)
    assert len(blocks) == 3, "light + system dark + manual dark"
    assert '[data-theme="dark"]' in css and 'prefers-color-scheme: dark' in css
    assert ':root:not([data-theme="light"])' in css, "manual light must beat a dark system"
    assert blocks[1] == blocks[2], "system-dark and manual-dark tokens must not drift apart"


def test_text_meets_wcag_aa_on_all_surfaces_in_both_themes():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    light, dark, _ = _theme_blocks(css)
    for name, tok in (("light", light), ("dark", dark)):
        for surface in ("bg-primary", "bg-secondary", "bg-card", "bg-card-hover"):
            for text in ("text-primary", "text-secondary", "text-muted"):
                assert _contrast(tok[text], tok[surface]) >= 4.5, (name, text, surface)
        for cat in ("cat-conspiracy", "cat-horror", "cat-paranormal", "accent"):
            assert _contrast(tok[cat], tok["bg-primary"]) >= 4.5, (name, cat)
            assert _contrast(tok[cat], tok["bg-card"]) >= 4.5, (name, cat)
        assert _contrast(tok["on-cat"], tok["cat-conspiracy"]) >= 4.5, name
        assert _contrast(tok["on-cat"], tok["cat-horror"]) >= 4.5, name
        assert _contrast(tok["on-cat"], tok["cat-paranormal"]) >= 4.5, name
        assert _contrast(tok["btn-text"], tok["btn-bg"]) >= 4.5, name
        assert _contrast(tok["top-text"], tok["top-bg"]) >= 4.5, name
        assert _contrast(tok["panel-text"], tok["panel-bg"]) >= 4.5, name
        assert _contrast(tok["panel-accent"], tok["panel-bg"]) >= 4.5, name
    # hl / on-hl are shared by both themes (declared once, in the light block)
    assert _contrast(light["on-hl"], light["hl"]) >= 4.5


def test_hero_redaction_bar_only_exists_when_motion_is_allowed():
    """The bar hides the headline word; without the animation that lifts it, it must not render."""
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    start = css.index("@media (prefers-reduced-motion: no-preference)")
    assert ".redacted::after" in css[start:]
    assert ".redacted::after" not in css[:start]


def test_touch_devices_see_redactions_as_highlights():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert "@media (hover: none)" in css


def test_script_has_single_favorite_binding_and_error_path():
    js = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    assert js.count("querySelectorAll('.favorite-btn')") == 1
    assert "resp.ok" in js and "catch" in js


# --- /fetch: Reddit errors must reach the user, unknown metrics stay unknown ---

def _fake_outcome(posts=(), errors=()):
    import reddit_fetcher
    return reddit_fetcher.FetchOutcome(posts=list(posts), errors=list(errors))


def test_fetch_shows_reddit_errors_instead_of_silent_zero(monkeypatch):
    monkeypatch.setattr(app_module, "fetch_reddit",
                        lambda subs: _fake_outcome(errors=["r/nosleep: Reddit bloqueó la petición (403)."]))
    html = client.post("/fetch").text
    assert "Reddit bloqueó la petición (403)" in html
    assert 'role="alert"' in html
    assert "No se encontraron historias nuevas" not in html


def test_fetch_inserts_new_posts_and_dedupes(monkeypatch):
    post = _story(1, score=None, comment_count=None)
    monkeypatch.setattr(app_module, "fetch_reddit", lambda subs: _fake_outcome(posts=[post]))
    assert "1 nuevos" in client.post("/fetch").text
    assert "0 nuevos" in client.post("/fetch").text
    _, total = database.get_stories()
    assert total == 1


def test_fetch_partial_failure_keeps_posts_and_logs_partial(monkeypatch):
    monkeypatch.setattr(app_module, "fetch_reddit",
                        lambda subs: _fake_outcome(posts=[_story(2, score=None, comment_count=None)],
                                                   errors=["r/x: boom"]))
    html = client.post("/fetch").text
    assert "boom" in html and "1 nuevos" in html
    assert database.get_stats()["last_fetch"]["status"] == "partial"


def test_unknown_score_is_not_rendered_as_zero_points():
    sid = database.insert_story(_story(3, score=None, comment_count=None))
    for url in ("/", "/stories", f"/stories/{sid}"):
        html = client.get(url).text
        assert "0 pts" not in html and "None" not in html, url
        assert "0 puntos" not in html and "0 comentarios" not in html, url


# --- redesign: theme, redaction, compact numbers ---------------------------

def test_theme_script_is_external_blocking_and_before_css():
    html = client.get("/").text
    head = html[: html.index("</head>")]
    assert '<script src="/static/theme.js"></script>' in head
    assert head.index("theme.js") < head.index("style.css")
    assert 'class="theme-toggle"' in html
    assert "<script>" not in html


def test_theme_js_reads_saved_choice_safely():
    js = (ROOT / "static" / "theme.js").read_text(encoding="utf-8")
    assert "localStorage" in js and "catch" in js and "data-theme" in js


def test_script_binds_theme_toggle_and_persists_choice():
    js = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    assert "theme-toggle" in js and "localStorage.setItem('theme'" in js


def test_both_theme_color_metas_present():
    html = client.get("/").text
    assert 'content="#e8ebe8" media="(prefers-color-scheme: light)"' in html
    assert 'content="#0d1015" media="(prefers-color-scheme: dark)"' in html


LONG = ("Trabajé tres años en una instalación que según los papeles fue clausurada en 1998 "
        "y cada martes recibíamos órdenes de un departamento que no aparece en ningún organigrama "
        "ni en ninguna lista de contactos de la empresa.")


def _strip_tags(markup):
    return re.sub(r"<[^>]+>", "", str(markup))


def test_redact_is_deterministic_per_seed():
    assert app_module.redact(LONG, 7) == app_module.redact(LONG, 7)
    assert any(app_module.redact(LONG, 7) != app_module.redact(LONG, n) for n in range(8, 20))


def test_redact_never_loses_or_reorders_words():
    for seed in range(40):
        out = app_module.redact(LONG, seed, limit=400, spans=3)
        assert _strip_tags(out) == " ".join(LONG.split()), seed
        assert str(out).count('<span class="rx">') >= 1, seed


def test_redact_leaves_start_of_text_readable():
    first = " ".join(LONG.split()[:3])
    for seed in range(40):
        assert str(app_module.redact(LONG, seed, limit=400)).startswith(first), seed


def test_redact_escapes_html_in_user_content():
    nasty = "<script>alert(1)</script> " + LONG
    out = str(app_module.redact(nasty, 1, limit=500))
    assert "<script>" not in out and "&lt;script&gt;" in out


def test_redact_short_text_untouched_and_truncation_adds_ellipsis():
    assert str(app_module.redact("Texto corto.", 1)) == "Texto corto."
    assert str(app_module.redact(LONG, 1, limit=60)).endswith("…")
    assert len(_strip_tags(app_module.redact(LONG, 1, limit=60))) <= 62
    assert str(app_module.redact("", 1)) == "" and str(app_module.redact(None, 1)) == ""


def test_cards_render_redactions_and_stay_in_dom():
    sid = database.insert_story(_story(1, content=LONG))
    html = client.get("/stories").text
    assert 'class="rx"' in html
    assert "EXP-%04d" % sid in html


def test_compact_number():
    f = app_module.compact_number
    assert [f(0), f(999), f(1000), f(5100), f(12345), f(2_500_000)] == ["0", "999", "1k", "5,1k", "12,3k", "2,5M"]
    assert f(None) == ""


def test_home_has_category_bands_with_counts_and_featured_case():
    database.insert_story(_story(1, category="conspiracy", content=LONG))
    html = client.get("/").text
    assert 'class="band" data-cat="conspiracy" href="/stories?category=conspiracy"' in html
    assert 'class="featured"' in html
    assert "Último caso con texto" in html


def test_home_without_text_posts_has_no_featured_block():
    database.insert_story(_story(1, content=""))
    html = client.get("/").text
    assert 'class="featured"' not in html and "Publicación sin texto" in html


def test_filters_use_category_radios_and_keep_selection():
    html = client.get("/stories?category=horror").text
    assert re.search(r'<input type="radio" name="category" value="horror" checked>', html)
    assert html.count('type="radio"') == 4


def test_detail_hides_unknown_metrics_but_shows_known_ones():
    unknown = database.insert_story(_story(1, score=None, comment_count=None))
    known = database.insert_story(_story(2, score=12, comment_count=3))
    assert "<dt>Puntos</dt>" not in client.get(f"/stories/{unknown}").text
    html = client.get(f"/stories/{known}").text
    assert "<dt>Puntos</dt><dd>12</dd>" in html and "<dt>Comentarios</dt><dd>3</dd>" in html


# --- critic round 1: related stories, aligned columns, dark redaction ------

def test_detail_lists_related_stories_from_same_category_only():
    main = database.insert_story(_story(1, category="horror"))
    same = database.insert_story(_story(2, category="horror", title="Hermana de terror"))
    database.insert_story(_story(3, category="paranormal", title="Otra categoria"))
    html = client.get(f"/stories/{main}").text
    assert "Más en Terror" in html and "Hermana de terror" in html
    assert "Otra categoria" not in html
    section = html[html.index('class="related"'):]
    assert f'href="/stories/{main}"' not in section, "the story must not list itself"
    assert f'href="/stories/{same}"' in section


def test_detail_without_related_has_no_related_section():
    (sid,) = _ids(1)
    assert 'class="related"' not in client.get(f"/stories/{sid}").text


def test_rows_keep_id_and_badge_in_a_left_rail_and_stats_on_the_footer_line():
    database.insert_story(_story(1, score=None, comment_count=None))
    database.insert_story(_story(2, score=10, comment_count=2))
    html = client.get("/stories").text
    rows = re.findall(r'<a href="/stories/\d+" class="story-row".*?</a>', html, re.S)
    assert len(rows) == 2
    for r in rows:
        rail = re.search(r'<div class="row-rail">(.*?)</div>', r, re.S).group(1)
        assert 'class="row-id"' in rail and rail.count('class="badge') == 1
    unknown, known = rows[1], rows[0]  # newest first: story 2 is first
    assert 'class="num"' in known and "<b>10</b> pts" in known and "<b>2</b> com." in known
    assert 'class="num"' not in unknown, "unknown metrics must not render at all"


def test_hero_stamp_is_decorative_and_shows_the_archive_size():
    _ids(3)
    html = client.get("/").text
    stamp = re.search(r'<p class="hero-stamp"[^>]*>(.*?)</p>', html, re.S)
    assert stamp and 'aria-hidden="true"' in stamp.group(0) and "3 expedientes" in stamp.group(1)


def test_dark_theme_redaction_is_not_an_offwhite_blank():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    _, dark, _ = _theme_blocks(css)
    assert dark["bar"] != dark["text-primary"], "off-white bars read as rendering gaps on dark"
    assert _luminance(dark["bar"]) < _luminance(dark["bg-primary"]), "censor bar must be darker than the page"
    assert _contrast(dark["bar-edge"], dark["bg-primary"]) >= 3, "its outline is what keeps it visible"
    assert _contrast(dark["bar-edge"], dark["bg-card"]) >= 3


def test_featured_rail_shows_facts_and_a_call_to_action():
    database.insert_story(_story(1, content=LONG, score=None, comment_count=None, author="ex_turno_c"))
    html = client.get("/").text
    rail = html[html.index('class="featured-side"'):]
    assert "Abrir expediente" in rail and "r/nosleep" in rail and "ex_turno_c" in rail
    assert "<dt>Puntos</dt>" not in rail, "unknown score must not appear"


def test_filters_group_search_with_submit_and_keep_favorites_in_the_chip_row():
    html = client.get("/stories").text
    start = html.index('class="filters"')
    form = html[start:html.index("</form>", start)]
    assert form.index('class="seg"') < form.index('type="checkbox"') < form.index('class="search-group"')
    group = form[form.index('class="search-group"'):]
    assert 'type="search"' in group and 'type="submit"' in group


def test_mono_microcopy_is_at_least_12px():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    for sel in (".row-foot", ".fetch-hint", ".facts", ".site-footer", ".tape-list"):
        block = re.search(re.escape(sel) + r" \{[^}]*?font-size: ([0-9.]+)rem", css)
        assert block and float(block.group(1)) >= 0.75, sel


def test_redaction_bars_are_never_tiny():
    for seed in range(200):
        out = str(app_module.redact(LONG, seed, limit=400, spans=3))
        for span in re.findall(r'<span class="rx">(.*?)</span>', out):
            assert len(span) >= app_module.MIN_REDACTION_CHARS, (seed, span)


def test_redaction_bars_do_not_wrap_across_lines():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    rx = re.search(r"\n\.rx \{(.*?)\}", css, re.S).group(1)
    assert "white-space: nowrap" in rx


def test_featured_rail_has_dated_facts():
    database.insert_story(_story(1, content=LONG, published_at="2026-09-24T10:00:00"))
    html = client.get("/").text
    rail = html[html.index('class="featured-side"'):]
    assert "<dt>Publicado</dt><dd>24/09/2026</dd>" in rail and "<dt>Categoría</dt>" in rail
    assert 'class="stamp"' not in rail, "the hero already carries the stamp"
