"""Reddit scraper: OAuth API when configured, anonymous Atom feeds otherwise.

Why RSS and not `.json`: Reddit answers 403 to anonymous `.json` requests (any
User-Agent), and anonymous `.rss` allows roughly one request per rate window
(`x-ratelimit-*`). So subreddits are merged into as few multireddit feeds as
possible (`/r/a+b+c/hot.rss`; each entry carries its own subreddit, so mixing
categories is fine) and any extra request is paced by those headers. A request
made inside the window is throttled with 429 or 403 depending on Reddit's mood;
both are retried after the window resets.

The anonymous path is unreliable under real use (the throttle is stricter than
the headers suggest), so when REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET are set the
module talks to oauth.reddit.com instead (~100 req/min, no cooldown games).
OAuth listings carry score and comment counts; the Atom feed does not, so there
those fields are `None` (unknown), never 0.
"""
import logging
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Callable, Dict, List, Optional

import requests

from config import REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT

logger = logging.getLogger(__name__)

FEED_URL = "https://www.reddit.com/r/{subs}/{sort}.rss?limit={limit}"
FEED_LIMIT = 100
MAX_SUBS_PER_REQUEST = 100  # a merged feed avoids extra rate-limit windows
MAX_ATTEMPTS = 3  # 429: wait out the rate window and retry
MAX_403_ATTEMPTS = 2  # a 403 is a longer block: one retry, then back off
MAX_WAIT = 90  # seconds; never block a web request longer than this per wait
DEFAULT_RETRY_WAIT = 60

TOKEN_URL = "https://www.reddit.com/api/v1/access_token"
OAUTH_FEED_URL = "https://oauth.reddit.com/r/{subs}/{sort}?limit={limit}&raw_json=1"

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


# Parsing

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


def _epoch(value) -> Optional[str]:
    """Unix seconds -> naive UTC ISO, the same format the RSS path produces."""
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None).isoformat()


def parse_oauth_listing(payload, categories: Dict[str, str]) -> List[Dict]:
    """Turn an oauth.reddit.com Listing into story dicts.

    Unlike the Atom feed, this carries `score` and `num_comments`.
    """
    wanted = {name.lower(): cat for name, cat in categories.items()}
    try:
        children = payload["data"]["children"]
    except (KeyError, TypeError) as e:
        raise FetchError("respuesta no válida de Reddit (no es un listado)") from e
    posts = []
    for child in children:
        if not isinstance(child, dict) or child.get("kind") != "t3":
            continue
        data = child.get("data") or {}
        sub = data.get("subreddit") or ""
        category = wanted.get(str(sub).lower())
        permalink = data.get("permalink") or ""
        if not category or not permalink:
            continue
        posts.append({
            "title": (data.get("title") or "(sin título)")[:500],
            "content": (data.get("selftext") or "")[:10000],
            "author": (data.get("author") or "").strip() or "[eliminado]",
            "source": "reddit",
            "source_url": f"https://www.reddit.com{permalink}",
            "sub_source": sub,
            "score": data.get("score"),
            "comment_count": data.get("num_comments"),
            "category": category,
            "published_at": _epoch(data.get("created_utc")),
        })
    return posts


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


# Fetching

def group_subreddits(subreddits: Dict[str, str], size: int = MAX_SUBS_PER_REQUEST) -> List[List[str]]:
    """Batch subreddits for multireddit feeds: at most `size` per request, in config order.

    Categories are resolved per feed entry, so there is no need to keep them apart.
    """
    names = list(subreddits)
    return [names[i:i + size] for i in range(0, len(names), size)]


def _seconds(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _wait_time(headers, default: float = 0) -> float:
    reset = headers.get("x-ratelimit-reset") or headers.get("retry-after")
    return min(_seconds(reset, default) + 1, MAX_WAIT) if reset or default else 0


def _get_feed(session, url: str, sleep: Callable[[float], None]):
    """GET with retries for Reddit's throttling (429 or 403). Raises FetchError."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = session.get(url, timeout=20)
        except requests.RequestException as e:
            raise FetchError(str(e)) from e
        limit = MAX_403_ATTEMPTS if resp.status_code == 403 else MAX_ATTEMPTS
        if resp.status_code in (403, 429) and attempt < limit:
            wait = _wait_time(resp.headers, DEFAULT_RETRY_WAIT)
            logger.warning(
                "Reddit %d, waiting %.0fs (attempt %d) ratelimit remaining=%s reset=%s retry-after=%s body=%r",
                resp.status_code, wait, attempt,
                resp.headers.get("x-ratelimit-remaining"), resp.headers.get("x-ratelimit-reset"),
                resp.headers.get("retry-after"), resp.text[:200],
            )
            sleep(wait)
            continue
        if resp.status_code == 429:
            raise FetchError("Reddit limitó las peticiones (429). Espera un minuto y reintenta.")
        if resp.status_code == 403:
            raise FetchError("Reddit bloqueó la petición (403). Espera un minuto y reintenta.")
        try:
            resp.raise_for_status()
        except requests.RequestException as e:
            raise FetchError(str(e)) from e
        return resp
    raise FetchError("Reddit no respondió.")  # unreachable; keeps type checkers calm


def _group_label(names: List[str]) -> str:
    """'r/a, r/b, r/c y 10 más': a full merged-feed list would drown the error."""
    shown = ", ".join(f"r/{n}" for n in names[:3])
    return shown if len(names) <= 3 else f"{shown} y {len(names) - 3} más"


# OAuth (application-only)

_token = {"value": None, "expires_at": 0.0}


def oauth_configured() -> bool:
    """True when REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET are both set."""
    return bool(REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET)


def _oauth_token(session) -> str:
    """Application-only access token, cached until shortly before it expires."""
    now = time.time()
    if _token["value"] and now < _token["expires_at"]:
        return _token["value"]
    try:
        resp = session.post(
            TOKEN_URL,
            auth=(REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET),
            data={"grant_type": "client_credentials"},
            headers={"User-Agent": REDDIT_USER_AGENT},
            timeout=20,
        )
    except requests.RequestException as e:
        raise FetchError(str(e)) from e
    if resp.status_code != 200:
        raise FetchError(
            f"Reddit rechazó las credenciales OAuth ({resp.status_code}). "
            "Revisa REDDIT_CLIENT_ID y REDDIT_CLIENT_SECRET."
        )
    try:
        payload = resp.json()
        token = payload["access_token"]
        expires_in = float(payload.get("expires_in", 3600))
    except (ValueError, KeyError, TypeError) as e:
        raise FetchError("respuesta no válida del token OAuth de Reddit") from e
    _token["value"] = token
    _token["expires_at"] = now + max(expires_in - 300, 60)
    return token


def _fetch_oauth(subreddits: Dict[str, str], sort: str, session) -> FetchOutcome:
    """Fetch via oauth.reddit.com: reliable, includes score and comment counts."""
    outcome = FetchOutcome()
    seen = set()
    token = _oauth_token(session)
    for names in group_subreddits(subreddits):
        url = OAUTH_FEED_URL.format(subs="+".join(names), sort=sort, limit=FEED_LIMIT)
        try:
            resp = session.get(url, headers={"Authorization": f"bearer {token}"}, timeout=20)
            if resp.status_code == 401:
                _token["value"] = None  # token expired early; refresh once
                token = _oauth_token(session)
                resp = session.get(url, headers={"Authorization": f"bearer {token}"}, timeout=20)
            if resp.status_code != 200:
                raise FetchError(f"Reddit respondió {resp.status_code} a la API OAuth.")
            posts = parse_oauth_listing(resp.json(), subreddits)
        except (FetchError, ValueError, requests.RequestException) as e:
            label = _group_label(names)
            logger.error("Reddit %s: %s", label, e)
            outcome.errors.append(f"{label}: {e}")
            continue
        for post in posts:
            if post["source_url"] not in seen:
                seen.add(post["source_url"])
                outcome.posts.append(post)
        logger.info("Reddit %s: %d posts (oauth)", "+".join(names), len(posts))
    return outcome


# Anonymous Atom feeds (fallback)

def _fetch_rss(
    subreddits: Dict[str, str],
    sort: str,
    session,
    sleep: Callable[[float], None],
    max_subs: int,
) -> FetchOutcome:
    """Fetch the anonymous Atom feeds. One failing group never discards the others.

    `max_subs` caps how many subreddits share one request (tests lower it to
    exercise the pacing between groups).
    """
    outcome = FetchOutcome()
    seen = set()
    groups = group_subreddits(subreddits, max_subs)
    for i, names in enumerate(groups):
        url = FEED_URL.format(subs="+".join(names), sort=sort, limit=FEED_LIMIT)
        try:
            resp = _get_feed(session, url, sleep)
            posts = parse_feed(resp.content, subreddits)
        except FetchError as e:
            label = _group_label(names)
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


def fetch_reddit(
    subreddits: Dict[str, str],
    sort: str = "hot",
    session=None,
    sleep: Callable[[float], None] = time.sleep,
    max_subs: int = MAX_SUBS_PER_REQUEST,
) -> FetchOutcome:
    """Fetch every subreddit, via OAuth when credentials are configured.

    Anonymous RSS is throttled hard by Reddit (403/429), so the OAuth API is
    preferred whenever REDDIT_CLIENT_ID/REDDIT_CLIENT_SECRET exist.
    """
    if oauth_configured():
        return _fetch_oauth(subreddits, sort, session or SESSION)
    return _fetch_rss(subreddits, sort, session or SESSION, sleep, max_subs)
