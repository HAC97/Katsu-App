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
    def __init__(self, status=200, content=b"", headers=None):
        self.status_code = status
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


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

def test_group_subreddits_chunks_by_category_and_size():
    subs = {f"a{i}": "horror" for i in range(7)}
    subs.update({"c1": "conspiracy", "c2": "conspiracy"})
    groups = rf.group_subreddits(subs, size=5)
    assert [len(g) for g in groups] == [5, 2, 2]
    assert sorted(s for g in groups for s in g) == sorted(subs)
    for g in groups:
        assert len({subs[s] for s in g}) == 1


def test_default_config_needs_few_requests():
    from config import SUBREDDITS
    assert len(rf.group_subreddits(SUBREDDITS)) <= 4


# --- fetching, pacing, errors ---------------------------------------------

def _ok(**h):
    return FakeResp(200, FIXTURE, {"x-ratelimit-remaining": "0.0", "x-ratelimit-reset": "30", **h})


def test_fetch_reddit_uses_one_multireddit_request_per_group():
    sess = FakeSession([_ok()])
    out = rf.fetch_reddit({"nosleep": "horror", "NoSleepx": "horror"}, session=sess, sleep=lambda s: None)
    assert len(sess.urls) == 1
    assert "/r/nosleep+NoSleepx/hot.rss?limit=100" in sess.urls[0]
    assert len(out.posts) == 3 and out.errors == []


def test_fetch_reddit_waits_for_rate_window_between_groups():
    sleeps = []
    sess = FakeSession([_ok(), _ok()])
    rf.fetch_reddit({"nosleep": "horror", "conspiracy": "conspiracy"},
                    session=sess, sleep=sleeps.append)
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


def test_fetch_reddit_reports_403_without_retrying():
    sess = FakeSession([FakeResp(403)])
    out = rf.fetch_reddit({"nosleep": "horror"}, session=sess, sleep=lambda s: None)
    assert len(sess.urls) == 1
    assert out.posts == [] and "403" in out.errors[0]


def test_fetch_reddit_one_failing_group_keeps_the_others():
    sess = FakeSession([requests.ConnectionError("boom"), _ok()])
    out = rf.fetch_reddit({"nosleep": "horror", "conspiracy": "conspiracy"},
                          session=sess, sleep=lambda s: None)
    assert len(out.posts) == 3
    assert len(out.errors) == 1 and "boom" in out.errors[0]


def test_wait_is_capped():
    sleeps = []
    sess = FakeSession([_ok(**{"x-ratelimit-reset": "9999"}), _ok()])
    rf.fetch_reddit({"nosleep": "horror", "conspiracy": "conspiracy"},
                    session=sess, sleep=sleeps.append)
    assert sleeps == [rf.MAX_WAIT]
