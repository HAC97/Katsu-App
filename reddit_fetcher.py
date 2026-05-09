import logging
from datetime import datetime
from typing import List, Dict

import requests

from config import REDDIT_USER_AGENT, DEFAULT_FETCH_LIMIT

logger = logging.getLogger(__name__)

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
})


def fetch_subreddit(
    subreddit: str, limit: int = DEFAULT_FETCH_LIMIT, sort: str = "hot"
) -> List[Dict]:
    posts = []
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json?limit={limit}&raw_json=1"
    try:
        resp = SESSION.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        for child in data.get("data", {}).get("children", []):
            p = child["data"]
            if p.get("stickied"):
                continue
            posts.append(
                {
                    "title": p.get("title", "(sin titulo)")[:500],
                    "content": p.get("selftext", "")[:10000],
                    "author": p.get("author", "[eliminado]"),
                    "source": "reddit",
                    "source_url": f"https://reddit.com{p.get('permalink', '')}",
                    "sub_source": subreddit,
                    "score": p.get("score", 0),
                    "comment_count": p.get("num_comments", 0),
                    "published_at": datetime.utcfromtimestamp(
                        p.get("created_utc", 0)
                    ).isoformat(),
                }
            )
        logger.info(f"Reddit r/{subreddit}: {len(posts)} posts fetched")
    except requests.RequestException as e:
        logger.error(f"Reddit r/{subreddit} error: {e}")
    return posts


def fetch_all_reddit(subreddits: Dict[str, str]) -> List[Dict]:
    all_posts = []
    for subreddit, category in subreddits.items():
        posts = fetch_subreddit(subreddit)
        for post in posts:
            post["category"] = category
        all_posts.extend(posts)
    return all_posts
