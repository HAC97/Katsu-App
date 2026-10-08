"""Periodic eval: does the Reddit scraper still work against the real Reddit?

Not a gate test (network, seconds in the happy path: every subreddit goes in a
single multireddit request; allow ~1 min if a 429 retry kicks in). Run before
shipping and nightly:

    python evals/eval_reddit_live.py

Exit code 0 only if every threshold passes. Regressions this catches: Reddit
changing the feed format, blocking the User-Agent (403), tightening rate limits
(429), or the HTML-to-text conversion leaking markup.
"""
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from config import SUBREDDITS  # noqa: E402
from reddit_fetcher import fetch_reddit, group_subreddits  # noqa: E402

MIN_POSTS = 30
MIN_WITH_TEXT = 0.5
MIN_CATEGORIES = 3
MAX_SECONDS = 90  # one request; margin for a rate-limit retry


def main() -> int:
    started = time.time()
    outcome = fetch_reddit(SUBREDDITS)
    elapsed = time.time() - started
    posts = outcome.posts
    with_text = [p for p in posts if p["content"]]
    leaked = [p for p in with_text if re.search(r"</?(p|div|a|br)\b|&#\d+;|&amp;|SC_OFF", p["content"])]
    bad_dates = []
    for p in posts:
        try:
            datetime.fromisoformat(p["published_at"])
        except (TypeError, ValueError):
            bad_dates.append(p["source_url"])
    unknown_subs = {p["sub_source"].lower() for p in posts} - {s.lower() for s in SUBREDDITS}
    checks = [
        ("no group errors", not outcome.errors, outcome.errors),
        (f">= {MIN_POSTS} posts", len(posts) >= MIN_POSTS, len(posts)),
        (f">= {MIN_WITH_TEXT:.0%} posts with text", posts and len(with_text) / len(posts) >= MIN_WITH_TEXT,
         f"{len(with_text)}/{len(posts)}"),
        (f">= {MIN_CATEGORIES} categories", len({p['category'] for p in posts}) >= MIN_CATEGORIES,
         sorted({p["category"] for p in posts})),
        ("no markup leaked into text", not leaked, [p["source_url"] for p in leaked[:3]]),
        ("all dates parse", not bad_dates, bad_dates[:3]),
        ("only configured subreddits", not unknown_subs, sorted(unknown_subs)),
        (f"finishes in < {MAX_SECONDS}s", elapsed < MAX_SECONDS, f"{elapsed:.0f}s"),
    ]
    print(f"{len(group_subreddits(SUBREDDITS))} requests, {len(posts)} posts, {elapsed:.0f}s")
    failed = 0
    for name, ok, detail in checks:
        print(f"{'PASS' if ok else 'FAIL'}  {name}  [{detail}]")
        failed += not ok
    print("RESULT:", "PASS" if not failed else f"FAIL ({failed} checks)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
