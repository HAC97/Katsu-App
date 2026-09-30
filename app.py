import asyncio
import logging
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import parse_qsl, urlencode, urlsplit

from fastapi import FastAPI, Request, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from database import (
    init_db,
    insert_story,
    get_stories,
    get_story,
    toggle_favorite,
    get_stats,
    log_fetch,
)
from reddit_fetcher import fetch_all_reddit, fetch_subreddit
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


_URL_RE = re.compile(r"https?://(?:www\.)?([^/\s?#)\]]+)[^\s)\]]*", re.I)
_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!~>|])")


def excerpt_split(text, limit=220, first_len=110):
    """Split a story into (first line, rest) for the censored excerpt.

    Excerpt-only cleanup: markdown backslash escapes are removed and bare URLs
    collapse to their domain. Truncation is done here at a word boundary and
    adds no ellipsis, so nothing ever trails a censor bar. The detail page
    renders the untouched text.
    """
    flat = _ESCAPE_RE.sub(lambda m: m.group(1), text or "")
    flat = _URL_RE.sub(lambda m: m.group(1), flat)
    flat = " ".join(flat.split())
    if not flat:
        return ("", "")
    if len(flat) > limit:
        flat = flat[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-–—([{\"'")
    if len(flat) <= first_len:
        return (flat, "")
    cut = flat.rfind(" ", 0, first_len)
    cut = cut if cut > 0 else first_len
    return (flat[:cut], flat[cut:].strip())


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


templates.env.filters["category_label"] = category_label
templates.env.filters["fmt_datetime"] = format_datetime
templates.env.filters["fmt_date"] = format_date
templates.env.filters["reading_minutes"] = reading_minutes
templates.env.filters["excerpt_split"] = excerpt_split


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
    return templates.TemplateResponse(
        "story_detail.html",
        {
            "request": request,
            "story": story,
            "categories": CATEGORIES,
            "back_url": safe_back_url(request.headers.get("referer")),
        },
    )


@app.post("/stories/{story_id}/favorite")
async def story_toggle_favorite(story_id: int):
    if not get_story(story_id):
        return JSONResponse({"error": "not_found"}, status_code=404)
    state = toggle_favorite(story_id)
    return JSONResponse({"id": story_id, "is_favorite": state})


async def _run_fetch_reddit(subreddits: dict) -> tuple:
    all_posts = []
    sem = asyncio.Semaphore(6)

    async def fetch_one(sub_name, category):
        async with sem:
            posts = await asyncio.to_thread(fetch_subreddit, sub_name)
            for p in posts:
                p["category"] = category
            return posts

    tasks = [fetch_one(name, cat) for name, cat in subreddits.items()]
    results_list = await asyncio.gather(*tasks, return_exceptions=True)

    for result in results_list:
        if isinstance(result, Exception):
            logger.error(f"Reddit fetch error: {result}")
        else:
            all_posts.extend(result)

    return all_posts

@app.post("/fetch")
async def trigger_fetch(request: Request):
    results = {"reddit": {"found": 0, "new": 0}}
    errors = []

    try:
        posts = await _run_fetch_reddit(SUBREDDITS)
        results["reddit"]["found"] = len(posts)
        new = 0
        for post in posts:
            sid = await asyncio.to_thread(insert_story, post)
            if sid:
                new += 1
        results["reddit"]["new"] = new
        await asyncio.to_thread(log_fetch, "reddit", len(posts), new)
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
