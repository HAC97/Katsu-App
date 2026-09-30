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
    assert 'href="/stories" class="btn btn-outline btn-sm"' in html


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


def test_empty_state_echoes_filters_and_offers_actions():
    html = client.get("/stories?search=zzqq&category=horror&favorite=true").text
    assert "Carpeta vacía" in html and "«zzqq»" in html
    assert "Categoría: Historias de Terror" in html and "Solo favoritas" in html
    assert 'href="/stories" class="btn btn-outline">Quitar filtros' in html
    assert re.search(r'<form method="POST" action="/fetch"', html)


def test_origin_cell_stacks_source_and_keeps_full_name():
    _ids(1, sub_source="Glitch_in_the_Matrix")
    html = client.get("/stories").text
    assert 'class="src">reddit<' in html and 'class="sub">r/Glitch_in_the_Matrix<' in html
    assert 'title="reddit · r/Glitch_in_the_Matrix"' in html


def test_stamp_is_data_driven_and_not_duplicated_on_home():
    (sid,) = _ids(1)
    html = client.get("/").text
    assert html.count('class="stamp"') == 1, "one stamp on home (featured file), none in the hero"
    assert "Sin verificar" in html and "Favorito<" not in html
    client.post(f"/stories/{sid}/favorite")
    home = client.get("/").text
    assert home.count('class="stamp"') == 1 and "Favorito<" in home and "Sin verificar<" not in home
    detail = client.get(f"/stories/{sid}").text
    assert 'class="stamp">Favorito<' in detail
    client.post(f"/stories/{sid}/favorite")
    assert 'class="stamp">Sin verificar<' in client.get(f"/stories/{sid}").text
    assert "file-tabline .stamp" in (ROOT / "static" / "script.js").read_text(encoding="utf-8")


def test_list_excerpt_has_at_most_one_short_bar():
    _ids(1, content="Lead sentence that is long enough to matter. " * 30)
    html = client.get("/stories").text
    rest = re.search(r'class="ex-rest">([^<]*)<', html).group(1)
    first = re.search(r'class="ex-first">([^<]*)<', html).group(1)
    assert len(first) <= 80 and len(rest) <= 75, (len(first), len(rest))


def test_one_red_action_per_list_page():
    html = client.get("/stories").text
    assert 'class="btn btn-ink">Filtrar' in html and "btn-primary\">Filtrar" not in html
    empty = client.get("/stories?search=zzqqzz").text
    assert empty.count("btn-primary") == 1  # Escanear only


def test_ledger_merges_origin_and_points_into_one_column():
    _ids(1)
    html = client.get("/stories").text
    assert 'class="cell-meta"' in html and "Origen y puntos" in html
    assert html.index('class="cell-origin"') < html.index('class="cell-pts"')


def test_detail_mobile_order_summary_before_text_notes_after():
    (sid,) = _ids(1)
    html = client.get(f"/stories/{sid}").text
    assert 'class="file-summary" aria-hidden="true"' in html and 'class="notes-data"' in html  # order is visual, via grid areas
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert '"back" "head" "summary" "actions" "body" "notes"' in css


# --- static asset guards --------------------------------------------------

def test_css_a11y_rules_present():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert "prefers-reduced-motion" in css
    assert ".sr-only" in css
    assert "-webkit-line-clamp" not in css.split(".excerpt {")[1].split("}")[0], "no css ellipsis after a censor bar"
    assert "max-width: 68ch" in css


def _luminance(hex_color):
    r, g, b = (int(hex_color[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(a, b):
    la, lb = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _tokens():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    return dict(re.findall(r"--([a-z-]+): (#[0-9a-fA-F]{6});", css))


def test_text_pairs_meet_wcag_aa():
    """Every text/background pair used in style.css must be >= 4.5:1."""
    tok = _tokens()
    pairs = [
        ("text-muted", "paper"), ("text-muted", "card"),   # metadata on page and on sheets
        ("ink", "paper"), ("ink", "card"),                # body text, nav
        ("card", "ink"),                                  # current nav item, skip link, hover buttons
        ("card", "stamp"),                                # primary button label
        ("stamp", "card"),                                # stamp text, action status
        ("card", "text-muted"),                           # pressed favorite button on hover
    ]
    for fg, bg in pairs:
        assert _contrast(tok[fg], tok[bg]) >= 4.5, (fg, bg, _contrast(tok[fg], tok[bg]))
    # hover row tint is a literal in the css; muted text sits on it too
    assert _contrast(tok["text-muted"], "#E6E9E2") >= 4.5


def test_palette_is_the_approved_one():
    tok = _tokens()
    assert tok["paper"].upper() == "#D8DCD4" and tok["card"].upper() == "#EEF0EA"
    assert tok["ink"].upper() == "#14171A" and tok["censor"].upper() == "#101214"
    assert tok["stamp"].upper() == "#C8321E" and tok["text-muted"].upper() == "#4A5250"


def test_excerpt_split_keeps_all_text_and_first_line_short():
    first, rest = app_module.excerpt_split("word " * 80, 220, 110)
    assert 0 < len(first) <= 110 and rest
    assert (first + " " + rest).replace("…", "").split() == ("word " * 80).split()[: len((first + " " + rest).replace("…", "").split())]
    assert app_module.excerpt_split("", 220) == ("", "")
    assert app_module.excerpt_split("short line", 220) == ("short line", "")
    url = "https://example.com/" + "a" * 300
    f, r = app_module.excerpt_split(url, 220)
    assert (f + r) == "example.com"  # bare URL collapses to its domain in excerpts


def test_censored_excerpt_is_in_dom_and_never_hidden():
    long_text = "Primera línea legible del relato. " + "Texto secreto que sigue. " * 20
    _ids(1, content=long_text)
    html = client.get("/stories").text
    assert 'class="ex-first"' in html and 'class="ex-rest"' in html
    assert "Texto secreto que sigue." in html  # real text stays in the DOM
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert "user-select" not in css
    assert ".row:focus-within .ex-rest" in css and ".row:hover .ex-rest" in css
    assert "@media (hover: none)" in css
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)"):]
    assert "background: transparent" in reduced and "transition: none" in reduced


def test_empty_content_and_long_urls_render():
    _ids(1, content="", title="T " * 80)
    assert client.get("/stories").status_code == 200
    _ids(1, content="https://x.example/" + "a" * 400)
    assert "overflow-wrap: anywhere" in (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    assert client.get("/").status_code == 200


def test_home_has_featured_entry_and_stamp():
    _ids(3)
    html = client.get("/").text
    assert "Expediente del día" in html and "Sin verificar" in html
    assert html.index('id="fetch-btn"') < html.index("Estadísticas") < html.index("Expediente del día")


def test_script_has_single_favorite_binding_and_error_path():
    js = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    assert js.count("querySelectorAll('.favorite-btn')") == 1
    assert "resp.ok" in js and "catch" in js
