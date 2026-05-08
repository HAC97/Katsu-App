import logging
import re
from datetime import datetime
from typing import List, Dict

import requests

from config import DEFAULT_FETCH_LIMIT

logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
})


def _clean_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&gt;", ">").replace("&lt;", "<")
    text = text.replace("&amp;", "&").replace("&quot;", '"')
    text = text.replace("&#039;", "'").replace("&#x27;", "'")
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def fetch_catalog(board: str, limit: int = DEFAULT_FETCH_LIMIT) -> List[Dict]:
    url = f"https://a.4cdn.org/{board}/catalog.json"
    try:
        resp = SESSION.get(url, timeout=30)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"4chan /{board}/ catalog error: {e}")
        return []

    threads = []
    for page in resp.json():
        for thread in page.get("threads", []):
            sub = thread.get("sub", "")
            no = thread.get("no", 0)
            com = thread.get("com", "")
            title = _clean_html(sub) if sub else f"Hilo No. {no}"
            content = _clean_html(com)
            threads.append({
                "title": title[:500],
                "content": content[:10000],
                "author": "Anonymous",
                "source": "4chan",
                "source_url": f"https://boards.4chan.org/{board}/thread/{no}",
                "sub_source": board,
                "score": thread.get("replies", 0),
                "comment_count": thread.get("replies", 0),
                "published_at": datetime.utcfromtimestamp(
                    thread.get("time", 0)
                ).isoformat(),
            })
        if len(threads) >= limit:
            break

    logger.info(f"4chan /{board}/: {len(threads)} threads fetched")
    return threads[:limit]


def fetch_thread(board: str, thread_id: int) -> List[Dict]:
    url = f"https://a.4cdn.org/{board}/thread/{thread_id}.json"
    try:
        resp = SESSION.get(url, timeout=15)
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"4chan thread {thread_id} error: {e}")
        return []

    posts = resp.json().get("posts", [])
    results = []
    for post in posts[:50]:
        com = post.get("com", "")
        if not com:
            continue
        no = post.get("no", 0)
        results.append({
            "title": f"Post No. {no} en hilo {thread_id}",
            "content": _clean_html(com)[:10000],
            "author": post.get("name", "Anonymous"),
            "source": "4chan",
            "source_url": f"https://boards.4chan.org/{board}/thread/{thread_id}#p{no}",
            "sub_source": board,
            "score": 0,
            "comment_count": 0,
            "published_at": datetime.utcfromtimestamp(
                post.get("time", 0)
            ).isoformat(),
        })
    logger.info(f"4chan thread {thread_id}: {len(results)} posts fetched")
    return results


def fetch_all_boards(boards: Dict[str, str]) -> List[Dict]:
    all_posts = []
    for board, category in boards.items():
        threads = fetch_catalog(board, limit=DEFAULT_FETCH_LIMIT)
        for post in threads:
            post["category"] = category
        all_posts.extend(threads)
    return all_posts
