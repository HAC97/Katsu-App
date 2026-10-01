import asyncio
import logging
import random
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape

from database import (
    init_db,
    insert_story,
    get_stories,
    get_story,
    toggle_favorite,
    get_stats,
    log_fetch,
)
from reddit_fetcher import fetch_reddit
from config import SUBREDDITS, CATEGORIES

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

# Ejecutamos init_db directamente para que funcione en WSGI (PythonAnywhere)
init_db()

app = FastAPI(title="ConspiracyHub", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

CATEGORY_LABELS = {
    "conspiracy": "Conspiración",
    "horror": "Terror",
    "paranormal": "Paranormal",
}
WORDS_PER_MINUTE = 200
MIN_REDACTION_CHARS = 12  # a bar over one short word reads as a rendering glitch


def build_query(category="all", source="all", search="", favorite=False, page=1) -> str:
    """Query string for /stories with defaults omitted and values URL-encoded."""
    params = {}
    if category != "all":
        params["category"] = category
    if source != "all":
        params["source"] = source
    if search:
        params["search"] = search
    if favorite:
        params["favorite"] = "true"
    if page > 1:
        params["page"] = page
    return "/stories?" + urlencode(params) if params else "/stories"


def category_label(value: str) -> str:
    return CATEGORY_LABELS.get(value, value.capitalize() if value else "")


def format_datetime(value) -> str:
    """'2026-09-29T14:03:11' or '2026-09-29 14:03:11' -> '29/09/2026 14:03'."""
    if not value:
        return "?"
    try:
        return datetime.fromisoformat(str(value)).strftime("%d/%m/%Y %H:%M")
    except ValueError:
        return str(value)[:10]


def format_date(value) -> str:
    return format_datetime(value)[:10] if value else "?"


def reading_minutes(text) -> int:
    return max(1, round(len((text or "").split()) / WORDS_PER_MINUTE))


def safe_back_url(referer: Optional[str]) -> str:
    """Return the listing URL the user came from; anything else falls back to /stories."""
    if referer:
        parts = urlsplit(referer)
        if parts.path == "/stories":
            return build_query(**_parse_listing_query(parts.query))
    return "/stories"


def _parse_listing_query(query: str) -> dict:
    q = dict(parse_qsl(query))
    try:
        page = max(1, int(q.get("page", 1)))
    except ValueError:
        page = 1
    return {
        "category": q.get("category", "all"),
        "source": q.get("source", "all"),
        "search": q.get("search", ""),
        "favorite": q.get("favorite", "").lower() == "true",
        "page": page,
    }


def compact_number(value) -> str:
    """5100 -> '5,1k'. None stays empty: an unknown count is not 0."""
    if value is None:
        return ""
    n = int(value)
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}".replace(".0", "").replace(".", ",") + "k"
    return f"{n / 1_000_000:.1f}".replace(".0", "").replace(".", ",") + "M"


def redact(text, seed, limit: int = 220, spans: int = 2) -> Markup:
    """Excerpt with a few phrases wrapped in <span class="rx"> (shown as redaction bars).

    Deterministic per `seed` (the story id) so a story looks the same on every visit.
    Text stays in the DOM, so nothing is hidden from screen readers or search.
    """
    words = " ".join((text or "").split()).split(" ")
    words = [w for w in words if w]
    cut = 0
    if len(" ".join(words)) > limit:
        acc = 0
        for i, w in enumerate(words):
            acc += len(w) + 1
            if acc > limit:
                cut = i
                break
        words = words[:cut]
    ellipsis = "…" if cut else ""
    rng = random.Random(f"redact-{seed}")
    taken = []
    if len(words) >= 14:
        for _ in range(spans):
            for _attempt in range(8):
                length = rng.randint(2, 5)
                start = rng.randint(3, len(words) - length - 2)
                long_enough = len(" ".join(words[start:start + length])) >= MIN_REDACTION_CHARS
                if long_enough and all(start + length + 1 < a or start > b + 1 for a, b in taken):
                    taken.append((start, start + length - 1))
                    break
    taken.sort()
    out, i = [], 0
    for a, b in taken:
        if a > i:
            out.append(escape(" ".join(words[i:a])))
        out.append(Markup('<span class="rx">') + escape(" ".join(words[a:b + 1])) + Markup("</span>"))
        i = b + 1
    if i < len(words):
        out.append(escape(" ".join(words[i:])))
    return Markup(" ").join(out) + ellipsis


templates.env.filters["compact"] = compact_number
templates.env.filters["redact"] = redact
templates.env.filters["category_label"] = category_label
templates.env.filters["fmt_datetime"] = format_datetime
templates.env.filters["fmt_date"] = format_date
templates.env.filters["reading_minutes"] = reading_minutes


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    stats = get_stats()
    recent_stories, _ = get_stories(limit=12, source="reddit")
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "stats": stats,
            "recent_stories": recent_stories,
            "categories": CATEGORIES,
        },
    )


@app.get("/stories", response_class=HTMLResponse)
async def stories_list(
    request: Request,
    category: str = Query("all"),
    source: str = Query("all"),
    search: str = Query(""),
    favorite: bool = Query(False),
    page: int = Query(1, ge=1),
):
    per_page = 20
    offset = (page - 1) * per_page
    stories, total = get_stories(
        category=category,
        source=source,
        search=search,
        favorite_only=favorite,
        limit=per_page,
        offset=offset,
    )
    total_pages = max(1, (total + per_page - 1) // per_page)

    def page_url(n: int) -> str:
        return build_query(category, source, search, favorite, n)

    return templates.TemplateResponse(
        "stories.html",
        {
            "request": request,
            "stories": stories,
            "categories": CATEGORIES,
            "current_category": category,
            "current_source": source,
            "current_search": search,
            "favorite_only": favorite,
            "page": page,
            "total_pages": total_pages,
            "total": total,
            "page_url": page_url,
        },
    )


@app.get("/stories/{story_id}", response_class=HTMLResponse)
async def story_detail(request: Request, story_id: int):
    story = get_story(story_id)
    if not story:
        return templates.TemplateResponse(
            "story_detail.html",
            {"request": request, "story": None, "categories": CATEGORIES},
            status_code=404,
        )
    same_category, _ = get_stories(category=story["category"], limit=4)
    related = [r for r in same_category if r["id"] != story["id"]][:3]
    return templates.TemplateResponse(
        "story_detail.html",
        {
            "request": request,
            "story": story,
            "categories": CATEGORIES,
            "back_url": safe_back_url(request.headers.get("referer")),
            "related": related,
        },
    )


@app.post("/stories/{story_id}/favorite")
async def story_toggle_favorite(story_id: int):
    if not get_story(story_id):
        return JSONResponse({"error": "not_found"}, status_code=404)
    state = toggle_favorite(story_id)
    return JSONResponse({"id": story_id, "is_favorite": state})


@app.post("/fetch")
async def trigger_fetch(request: Request):
    results = {"reddit": {"found": 0, "new": 0}}
    errors = []

    try:
        outcome = await asyncio.to_thread(fetch_reddit, SUBREDDITS)
        errors.extend(outcome.errors)
        results["reddit"]["found"] = len(outcome.posts)
        new = 0
        for post in outcome.posts:
            sid = await asyncio.to_thread(insert_story, post)
            if sid:
                new += 1
        results["reddit"]["new"] = new
        status = "ok" if not errors else ("partial" if outcome.posts else "error")
        await asyncio.to_thread(
            log_fetch, "reddit", len(outcome.posts), new, status, "; ".join(errors)
        )
    except Exception as e:
        logger.exception("Reddit fetch failed")
        errors.append(f"Reddit: {e}")
        await asyncio.to_thread(log_fetch, "reddit", 0, 0, "error", str(e))

    stats = get_stats()
    recent_stories, _ = get_stories(limit=12)
    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "stats": stats,
            "recent_stories": recent_stories,
            "categories": CATEGORIES,
            "fetch_result": results,
            "fetch_errors": errors,
        },
    )


@app.post("/api/clear")
async def clear_database():
    from database import delete_all_stories
    deleted = delete_all_stories()
    return JSONResponse({"deleted": deleted})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
