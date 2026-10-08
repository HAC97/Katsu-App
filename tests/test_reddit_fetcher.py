"""Gate tests for the Reddit scraper.

Reddit answers 403 to anonymous `.json` and allows one anonymous request per rate
window on `.rss`, so the fetcher batches subreddits into multireddit feeds and
paces itself with `x-ratelimit-*`. The fixture is a real feed captured on 2026-10-01.
"""
from pathlib import Path

import pytest
import requests

import reddit_fetcher as rf

FIXTURE = (Path(__file__).parent / "fixtures" / "reddit_hot.xml").read_bytes()
CATS = {"nosleep": "horror", "conspiracy": "conspiracy"}


class FakeResp:
    def __init__(self, status=200, content=b"", headers=None, payload=None):
        self.status_code = status
        self.content = content
        self.text = content.decode("utf-8", "replace")
        self.headers = headers or {}
        self._payload = payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get(self, url, timeout=None):
        self.urls.append(url)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class FakeOAuthSession:
    """POST returns a token; GET replays the given feed statuses, then a listing."""

    def __init__(self, token=None, listing=None, feed_statuses=(200,)):
        self.token_resp = FakeResp(200 if token is not None else 401,
                                   payload=token if token is not None else {"error": 401})
        self.listing = listing or {"kind": "Listing", "data": {"children": []}}
        self.feed_statuses = list(feed_statuses)
        self.token_requests = []
        self.feed_requests = []

    def post(self, url, auth=None, data=None, headers=None, timeout=None):
        self.token_requests.append((url, auth, data))
        return self.token_resp

    def get(self, url, headers=None, timeout=None):
        self.feed_requests.append((url, headers))
        status = self.feed_statuses.pop(0) if self.feed_statuses else 200
        return FakeResp(status, payload=self.listing if status == 200 else None)


def _token_payload():
    return {"access_token": "tok-123", "token_type": "bearer", "expires_in": 3600}


def _t3(**over):
    data = {
        "subreddit": "nosleep",
        "title": "Historia de prueba",
        "selftext": "Primer párrafo.",
        "author": "autora",
        "permalink": "/r/nosleep/comments/abc123/historia/",
        "score": 42,
        "num_comments": 7,
        "created_utc": 1759300000.0,
    }
    data.update(over)
    return {"kind": "t3", "data": data}


def _listing(*children):
    return {"kind": "Listing", "data": {"children": list(children)}}


@pytest.fixture(autouse=True)
def _anonymous_by_default(monkeypatch):
    """Never depend on the machine having OAuth credentials configured."""
    monkeypatch.setattr(rf, "REDDIT_CLIENT_ID", "")
    monkeypatch.setattr(rf, "REDDIT_CLIENT_SECRET", "")
    rf._token.update(value=None, expires_at=0.0)
    yield


# --- parsing ---------------------------------------------------------------

def test_parse_feed_extracts_self_post_text():
    posts = rf.parse_feed(FIXTURE, CATS)
    scam = next(p for p in posts if p["title"].startswith("I Scam People"))
    assert scam["category"] == "horror"
    assert scam["sub_source"] == "nosleep"
    assert scam["source"] == "reddit"
    assert scam["author"] == "Diligent-Border-3329"
    assert scam["source_url"].startswith("https://www.reddit.com/r/nosleep/comments/1wuzprj/")
    assert scam["published_at"] == "2026-10-01T13:40:04"
    assert scam["content"].startswith("I'm the guy who calls from the sketchy number.")
    assert "&#39;" not in scam["content"] and "<p>" not in scam["content"]
    assert "submitted by" not in scam["content"] and "[comments]" not in scam["content"]
    assert "\n" in scam["content"], "paragraphs must survive as newlines"


def test_parse_feed_link_posts_have_empty_content():
    posts = rf.parse_feed(FIXTURE, CATS)
    link_post = next(p for p in posts if p["title"] == "Tired of A.I.?")
    assert link_post["content"] == ""


def test_parse_feed_marks_score_and_comments_unknown():
    # The RSS feed carries neither; 0 would be a lie shown as "0 pts".
    for p in rf.parse_feed(FIXTURE, CATS):
        assert p["score"] is None and p["comment_count"] is None


def test_parse_feed_skips_unknown_subreddits_and_matches_case_insensitively():
    assert rf.parse_feed(FIXTURE, {"someother": "paranormal"}) == []
    assert len(rf.parse_feed(FIXTURE, {"NoSleep": "horror"})) == 3


def test_parse_feed_garbage_raises_fetch_error():
    with pytest.raises(rf.FetchError):
        rf.parse_feed(b"<html>blocked</html>", CATS)


def test_html_to_text_blocks_and_entities():
    html = ('<!-- SC_OFF --><div class="md"><p>Uno &amp; dos</p> <p>Tres<br/>cuatro</p>'
            '<ul><li>a</li><li>b</li></ul></div><!-- SC_ON --> submitted by x')
    assert rf.html_to_text(html) == "Uno & dos\nTres\ncuatro\na\nb"


# --- grouping --------------------------------------------------------------

def test_group_subreddits_chunks_by_size_in_config_order():
    subs = {f"a{i}": "horror" for i in range(7)}
    subs.update({"c1": "conspiracy", "c2": "conspiracy"})
    groups = rf.group_subreddits(subs, size=5)
    assert [len(g) for g in groups] == [5, 4]
    assert [s for g in groups for s in g] == list(subs)


def test_default_config_fits_in_one_request():
    from config import SUBREDDITS
    groups = rf.group_subreddits(SUBREDDITS)
    assert len(groups) == 1
    assert sorted(groups[0]) == sorted(SUBREDDITS)


# --- fetching, pacing, errors ---------------------------------------------

def _ok(**h):
    return FakeResp(200, FIXTURE, {"x-ratelimit-remaining": "0.0", "x-ratelimit-reset": "30", **h})


def test_fetch_reddit_uses_one_multireddit_request_per_group():
    sess = FakeSession([_ok()])
    out = rf.fetch_reddit({"nosleep": "horror", "NoSleepx": "horror"}, session=sess, sleep=lambda s: None)
    assert len(sess.urls) == 1
    assert "/r/nosleep+NoSleepx/hot.rss?limit=100" in sess.urls[0]
    assert len(out.posts) == 3 and out.errors == []


def test_fetch_reddit_merges_mixed_categories_into_one_request():
    sess = FakeSession([_ok()])
    out = rf.fetch_reddit({"nosleep": "horror", "conspiracy": "conspiracy"},
                          session=sess, sleep=lambda s: None)
    assert len(sess.urls) == 1
    assert "/r/nosleep+conspiracy/hot.rss" in sess.urls[0]
    assert out.errors == []


def test_fetch_reddit_waits_for_rate_window_between_groups():
    sleeps = []
    sess = FakeSession([_ok(), _ok()])
    rf.fetch_reddit({"nosleep": "horror", "conspiracy": "conspiracy"},
                    session=sess, sleep=sleeps.append, max_subs=1)
    assert len(sess.urls) == 2
    assert sleeps == [31], "must wait x-ratelimit-reset + 1 before the second request"


def test_fetch_reddit_retries_429_then_succeeds():
    sleeps = []
    sess = FakeSession([FakeResp(429, b"", {"x-ratelimit-reset": "12"}), _ok()])
    out = rf.fetch_reddit({"nosleep": "horror"}, session=sess, sleep=sleeps.append)
    assert len(out.posts) == 3 and out.errors == []
    assert sleeps == [13]


def test_fetch_reddit_reports_persistent_429():
    sess = FakeSession([FakeResp(429, b"", {"x-ratelimit-reset": "5"})] * 5)
    out = rf.fetch_reddit({"nosleep": "horror"}, session=sess, sleep=lambda s: None)
    assert out.posts == []
    assert len(out.errors) == 1 and "429" in out.errors[0] and "nosleep" in out.errors[0]


def test_fetch_reddit_retries_403_then_succeeds():
    sleeps = []
    sess = FakeSession([FakeResp(403, b"", {"x-ratelimit-reset": "12"}), _ok()])
    out = rf.fetch_reddit({"nosleep": "horror"}, session=sess, sleep=sleeps.append)
    assert len(out.posts) == 3 and out.errors == []
    assert sleeps == [13]


def test_throttle_log_keeps_evidence_for_diagnosis(caplog):
    """A 403 must leave status, rate-limit headers and body in the log: they are
    the only way to tell a rate window from an IP block after the fact."""
    sess = FakeSession([FakeResp(403, b"blocked by policy", {"x-ratelimit-reset": "12"}), _ok()])
    with caplog.at_level("WARNING", logger="reddit_fetcher"):
        rf.fetch_reddit({"nosleep": "horror"}, session=sess, sleep=lambda s: None)
    msg = caplog.text
    assert "403" in msg and "reset=12" in msg and "blocked by policy" in msg


def test_fetch_reddit_reports_persistent_403():
    sess = FakeSession([FakeResp(403, b"", {"x-ratelimit-reset": "5"})] * 5)
    out = rf.fetch_reddit({"nosleep": "horror"}, session=sess, sleep=lambda s: None)
    assert len(sess.urls) == rf.MAX_403_ATTEMPTS, "a 403 gets one retry, not a full burst"
    assert out.posts == [] and "403" in out.errors[0]


def test_fetch_reddit_one_failing_group_keeps_the_others():
    sess = FakeSession([requests.ConnectionError("boom"), _ok()])
    out = rf.fetch_reddit({"nosleep": "horror", "conspiracy": "conspiracy"},
                          session=sess, sleep=lambda s: None, max_subs=1)
    assert len(out.posts) == 3
    assert len(out.errors) == 1 and "boom" in out.errors[0]


def test_wait_is_capped():
    sleeps = []
    sess = FakeSession([_ok(**{"x-ratelimit-reset": "9999"}), _ok()])
    rf.fetch_reddit({"nosleep": "horror", "conspiracy": "conspiracy"},
                    session=sess, sleep=sleeps.append, max_subs=1)
    assert sleeps == [rf.MAX_WAIT]


# --- OAuth (application-only) ----------------------------------------------

def _with_credentials(monkeypatch):
    monkeypatch.setattr(rf, "REDDIT_CLIENT_ID", "client-id")
    monkeypatch.setattr(rf, "REDDIT_CLIENT_SECRET", "client-secret")


def test_parse_oauth_listing_maps_fields_and_skips_unknown_subs():
    posts = rf.parse_oauth_listing(_listing(_t3(), _t3(subreddit="otro", title="Fuera")), CATS)
    assert len(posts) == 1
    post = posts[0]
    assert post["category"] == "horror"
    assert post["sub_source"] == "nosleep"
    assert post["source"] == "reddit"
    assert post["title"] == "Historia de prueba"
    assert post["content"] == "Primer párrafo."
    assert post["author"] == "autora"
    assert post["source_url"] == "https://www.reddit.com/r/nosleep/comments/abc123/historia/"
    assert post["score"] == 42 and post["comment_count"] == 7
    assert post["published_at"] == "2025-10-01T06:26:40"


def test_parse_oauth_listing_garbage_raises_fetch_error():
    with pytest.raises(rf.FetchError):
        rf.parse_oauth_listing({"error": 403}, CATS)


def test_fetch_reddit_uses_oauth_when_configured(monkeypatch):
    _with_credentials(monkeypatch)
    sess = FakeOAuthSession(token=_token_payload(), listing=_listing(_t3()))
    out = rf.fetch_reddit({"nosleep": "horror"}, session=sess)
    assert out.errors == []
    assert len(out.posts) == 1 and out.posts[0]["score"] == 42
    assert sess.token_requests[0][0] == rf.TOKEN_URL
    assert sess.token_requests[0][1] == ("client-id", "client-secret")
    url, headers = sess.feed_requests[0]
    assert url.startswith("https://oauth.reddit.com/r/nosleep/hot")
    assert headers["Authorization"] == "bearer tok-123"


def test_oauth_token_is_cached(monkeypatch):
    _with_credentials(monkeypatch)
    sess = FakeOAuthSession(token=_token_payload())
    assert rf._oauth_token(sess) == "tok-123"
    assert rf._oauth_token(sess) == "tok-123"
    assert len(sess.token_requests) == 1


def test_fetch_reddit_oauth_refreshes_token_once_on_401(monkeypatch):
    _with_credentials(monkeypatch)
    sess = FakeOAuthSession(token=_token_payload(), listing=_listing(_t3()),
                            feed_statuses=(401, 200))
    out = rf.fetch_reddit({"nosleep": "horror"}, session=sess)
    assert len(out.posts) == 1 and out.errors == []
    assert len(sess.token_requests) == 2, "401 must trigger one token refresh"
    assert len(sess.feed_requests) == 2


def test_fetch_reddit_reports_oauth_feed_error(monkeypatch):
    _with_credentials(monkeypatch)
    sess = FakeOAuthSession(token=_token_payload(), feed_statuses=(403,))
    out = rf.fetch_reddit({"nosleep": "horror"}, session=sess)
    assert out.posts == []
    assert len(out.errors) == 1 and "403" in out.errors[0]


def test_oauth_token_failure_is_a_fetch_error(monkeypatch):
    _with_credentials(monkeypatch)
    sess = FakeOAuthSession(token=None)  # the token POST answers 401
    with pytest.raises(rf.FetchError):
        rf.fetch_reddit({"nosleep": "horror"}, session=sess)
