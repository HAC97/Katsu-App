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


def test_muted_text_meets_wcag_aa_on_all_surfaces():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    tok = dict(re.findall(r"--([a-z-]+): (#[0-9a-fA-F]{6});", css))
    for surface in ("bg-primary", "bg-secondary", "bg-card", "bg-card-hover"):
        assert _contrast(tok["text-muted"], tok[surface]) >= 4.5, surface


def test_script_has_single_favorite_binding_and_error_path():
    js = (ROOT / "static" / "script.js").read_text(encoding="utf-8")
    assert js.count("querySelectorAll('.favorite-btn')") == 1
    assert "resp.ok" in js and "catch" in js
