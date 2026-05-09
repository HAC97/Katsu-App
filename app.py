import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

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
    init_db()
    yield


app = FastAPI(title="ConspiracyHub", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


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
        {"request": request, "story": story, "categories": CATEGORIES},
    )


@app.post("/stories/{story_id}/favorite")
async def story_toggle_favorite(story_id: int):
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
