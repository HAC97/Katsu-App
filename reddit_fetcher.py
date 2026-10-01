"""Reddit scraper over the public Atom feeds.

Why RSS and not `.json`: Reddit answers 403 to anonymous `.json` requests (any
User-Agent), and anonymous `.rss` allows roughly one request per rate window
(`x-ratelimit-*`). So subreddits are batched into multireddit feeds
(`/r/a+b+c/hot.rss`) and requests are paced by those headers.

The feed has no score or comment count, so those fields are `None` (unknown),
never 0.
"""
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

FEED_URL = "https://www.reddit.com/r/{subs}/{sort}.rss?limit={limit}"
FEED_LIMIT = 100
MAX_SUBS_PER_REQUEST = 5
MAX_ATTEMPTS = 3
MAX_WAIT = 90  # seconds; never block a web request longer than this per wait
DEFAULT_RETRY_WAIT = 60

ATOM = "{http://www.w3.org/2005/Atom}"

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (compatible; ConspiracyHub/1.0)",
    "Accept": "application/atom+xml, application/xml;q=0.9",
})


class FetchError(Exception):
    """A feed could not be fetched or understood. The message is shown to the user."""


@dataclass
class FetchOutcome:
    posts: List[Dict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)


# --- parsing ---------------------------------------------------------------

_BLOCK_TAGS = {"p", "br", "li", "blockquote", "pre", "hr", "h1", "h2", "h3", "h4", "h5", "h6", "tr"}


class _SelfTextParser(HTMLParser):
    """Collects the text inside <div class="md"> (the post body), one line per block."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.depth = 0
        self.lines: List[str] = []
        self.buf: List[str] = []

    def _flush(self):
        text = "".join(self.buf).strip()
        if text:
            self.lines.append(text)
        self.buf = []

    def handle_starttag(self, tag, attrs):
        if self.depth == 0:
            if tag == "div" and ("class", "md") in attrs:
                self.depth = 1
            return
        if tag == "div":
            self.depth += 1
        if tag in _BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if self.depth == 0:
            return
        if tag in _BLOCK_TAGS:
            self._flush()
        if tag == "div":
            self.depth -= 1
            if self.depth == 0:
                self._flush()

    def handle_data(self, data):
        if self.depth:
            self.buf.append(data)


def html_to_text(html: str) -> str:
    parser = _SelfTextParser()
    parser.feed(html or "")
    parser.close()
    parser._flush()
    return "\n".join(parser.lines)


def _published(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.strip())
    except ValueError:
        return None
    if dt.tzinfo:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt.isoformat()


def parse_feed(xml_bytes: bytes, categories: Dict[str, str]) -> List[Dict]:
    """Turn an Atom feed into story dicts. `categories` maps subreddit -> category."""
    wanted = {name.lower(): cat for name, cat in categories.items()}
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        raise FetchError(f"respuesta no válida de Reddit ({e})") from e
    if root.tag != f"{ATOM}feed":
        raise FetchError("respuesta no válida de Reddit (no es un feed)")

    posts = []
    for entry in root.findall(f"{ATOM}entry"):
        cat_el = entry.find(f"{ATOM}category")
        sub = (cat_el.get("term") if cat_el is not None else "") or ""
        category = wanted.get(sub.lower())
        link_el = entry.find(f"{ATOM}link")
        url = link_el.get("href") if link_el is not None else ""
        if not category or not url:
            continue
        author = (entry.findtext(f"{ATOM}author/{ATOM}name") or "").strip()
        author = author[3:] if author.startswith("/u/") else author
        posts.append({
            "title": (entry.findtext(f"{ATOM}title") or "(sin título)")[:500],
            "content": html_to_text(entry.findtext(f"{ATOM}content") or "")[:10000],
            "author": author or "[eliminado]",
            "source": "reddit",
            "source_url": url,
            "sub_source": sub,
            "score": None,
            "comment_count": None,
            "category": category,
            "published_at": _published(entry.findtext(f"{ATOM}published")),
        })
    return posts


# --- fetching --------------------------------------------------------------

def group_subreddits(subreddits: Dict[str, str], size: int = MAX_SUBS_PER_REQUEST) -> List[List[str]]:
    """Batch subreddits for multireddit feeds: same category together, at most `size` each."""
    by_cat: Dict[str, List[str]] = {}
    for name, cat in subreddits.items():
        by_cat.setdefault(cat, []).append(name)
    return [names[i:i + size] for names in by_cat.values() for i in range(0, len(names), size)]


def _seconds(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _wait_time(headers, default: float = 0) -> float:
    reset = headers.get("x-ratelimit-reset") or headers.get("retry-after")
    return min(_seconds(reset, default) + 1, MAX_WAIT) if reset or default else 0


def _get_feed(session, url: str, sleep: Callable[[float], None]):
    """GET with 429 retries. Raises FetchError with a user-facing message."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = session.get(url, timeout=20)
        except requests.RequestException as e:
            raise FetchError(str(e)) from e
        if resp.status_code == 429 and attempt < MAX_ATTEMPTS:
            wait = _wait_time(resp.headers, DEFAULT_RETRY_WAIT)
            logger.warning("Reddit 429, waiting %.0fs (attempt %d)", wait, attempt)
            sleep(wait)
            continue
        if resp.status_code == 429:
            raise FetchError("Reddit limitó las peticiones (429). Espera un minuto y reintenta.")
        if resp.status_code == 403:
            raise FetchError("Reddit bloqueó la petición (403).")
        try:
            resp.raise_for_status()
        except requests.RequestException as e:
            raise FetchError(str(e)) from e
        return resp
    raise FetchError("Reddit no respondió.")  # unreachable; keeps type checkers calm


def fetch_reddit(
    subreddits: Dict[str, str],
    sort: str = "hot",
    session=None,
    sleep: Callable[[float], None] = time.sleep,
) -> FetchOutcome:
    """Fetch every subreddit. One failing group never discards the others."""
    session = session or SESSION
    outcome = FetchOutcome()
    seen = set()
    groups = group_subreddits(subreddits)
    for i, names in enumerate(groups):
        url = FEED_URL.format(subs="+".join(names), sort=sort, limit=FEED_LIMIT)
        try:
            resp = _get_feed(session, url, sleep)
            posts = parse_feed(resp.content, subreddits)
        except FetchError as e:
            label = ", ".join(f"r/{n}" for n in names)
            logger.error("Reddit %s: %s", label, e)
            outcome.errors.append(f"{label}: {e}")
            continue
        for post in posts:
            if post["source_url"] not in seen:
                seen.add(post["source_url"])
                outcome.posts.append(post)
        logger.info("Reddit %s: %d posts", "+".join(names), len(posts))
        if i < len(groups) - 1 and _seconds(resp.headers.get("x-ratelimit-remaining"), 1) < 1:
            sleep(_wait_time(resp.headers))
    return outcome
