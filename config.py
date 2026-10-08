import os

REDDIT_USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT",
    "Mozilla/5.0 (compatible; ConspiracyHub/1.0; +https://github.com/conspiracy-hub)"
)

# Application-only OAuth (https://www.reddit.com/prefs/apps, app type "script").
# When both are set the scraper uses oauth.reddit.com (~100 req/min) instead of
# the rate-limited anonymous RSS feeds.
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")

DATABASE_PATH = os.environ.get("DATABASE_PATH", "stories.db")
DEFAULT_FETCH_LIMIT = 20

SUBREDDITS = {
    "conspiracy": "conspiracy",
    "nosleep": "horror",
    "Paranormal": "paranormal",
    "HighStrangeness": "paranormal",
    "Thetruthishere": "paranormal",
    "shortscarystories": "horror",
    "Glitch_in_the_Matrix": "paranormal",
    "UFOs": "conspiracy",
    "skinwalkers": "paranormal",
    "Ghosts": "paranormal",
    "LetsNotMeet": "horror",
    "UnsolvedMysteries": "conspiracy",
    "DarkTales": "horror",
}
CATEGORIES = [
    ("all", "Todas"),
    ("conspiracy", "Conspiraciones"),
    ("horror", "Historias de Terror"),
    ("paranormal", "Paranormal"),
]
