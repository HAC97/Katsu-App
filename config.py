import os

REDDIT_USER_AGENT = os.environ.get(
    "REDDIT_USER_AGENT",
    "Mozilla/5.0 (compatible; ConspiracyHub/1.0; +https://github.com/conspiracy-hub)"
)

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
