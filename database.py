import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Optional

from config import DATABASE_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    content TEXT DEFAULT '',
    author TEXT DEFAULT '',
    source TEXT NOT NULL,
    source_url TEXT NOT NULL,
    sub_source TEXT DEFAULT '',
    score INTEGER DEFAULT 0,
    comment_count INTEGER DEFAULT 0,
    category TEXT DEFAULT 'paranormal',
    published_at TIMESTAMP,
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_favorite INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fetch_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    stories_found INTEGER DEFAULT 0,
    stories_new INTEGER DEFAULT 0,
    status TEXT DEFAULT 'ok',
    error TEXT DEFAULT '',
    fetched_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_stories_url ON stories(source_url);
CREATE INDEX IF NOT EXISTS idx_stories_category ON stories(category);
CREATE INDEX IF NOT EXISTS idx_stories_source ON stories(source);
CREATE INDEX IF NOT EXISTS idx_stories_fetched ON stories(fetched_at);
CREATE INDEX IF NOT EXISTS idx_stories_favorite ON stories(is_favorite);
"""


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()


def insert_story(data: dict) -> Optional[int]:
    conn = get_db()
    try:
        cursor = conn.execute(
            """INSERT OR IGNORE INTO stories
               (title, content, author, source, source_url, sub_source,
                score, comment_count, category, published_at)
               VALUES (:title, :content, :author, :source, :source_url,
                       :sub_source, :score, :comment_count, :category, :published_at)""",
            data,
        )
        story_id = cursor.lastrowid if cursor.rowcount > 0 else None
        conn.commit()
        return story_id
    finally:
        conn.close()


def get_stories(
    category: str = "all",
    source: str = "all",
    search: str = "",
    favorite_only: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple:
    conn = get_db()
    try:
        query = "SELECT * FROM stories WHERE 1=1"
        params = []

        if category != "all":
            query += " AND category = ?"
            params.append(category)
        if source != "all":
            query += " AND source = ?"
            params.append(source)
        if favorite_only:
            query += " AND is_favorite = 1"
        if search:
            query += " AND (title LIKE ? OR content LIKE ?)"
            search_term = f"%{search}%"
            params.extend([search_term, search_term])

        count_query = query.replace("SELECT *", "SELECT COUNT(*)")
        total = conn.execute(count_query, params).fetchone()[0]

        query += " ORDER BY fetched_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = conn.execute(query, params).fetchall()
        stories = [dict(row) for row in rows]
        return stories, total
    finally:
        conn.close()


def get_story(story_id: int) -> Optional[dict]:
    conn = get_db()
    try:
        row = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def toggle_favorite(story_id: int) -> bool:
    conn = get_db()
    try:
        conn.execute(
            """UPDATE stories SET is_favorite = CASE WHEN is_favorite = 1
               THEN 0 ELSE 1 END WHERE id = ?""",
            (story_id,),
        )
        conn.commit()
        row = conn.execute(
            "SELECT is_favorite FROM stories WHERE id = ?", (story_id,)
        ).fetchone()
        return bool(row["is_favorite"]) if row else False
    finally:
        conn.close()


def get_stats() -> dict:
    conn = get_db()
    try:
        total = conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
        favorites = conn.execute(
            "SELECT COUNT(*) FROM stories WHERE is_favorite = 1"
        ).fetchone()[0]
        by_source = {
            row["source"]: row["count"]
            for row in conn.execute(
                "SELECT source, COUNT(*) as count FROM stories GROUP BY source"
            ).fetchall()
        }
        by_category = {
            row["category"]: row["count"]
            for row in conn.execute(
                "SELECT category, COUNT(*) as count FROM stories GROUP BY category"
            ).fetchall()
        }
        last_fetch = conn.execute(
            "SELECT * FROM fetch_log ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()

        return {
            "total": total,
            "favorites": favorites,
            "by_source": by_source,
            "by_category": by_category,
            "last_fetch": dict(last_fetch) if last_fetch else None,
        }
    finally:
        conn.close()


def log_fetch(source: str, found: int, new: int, status: str = "ok", error: str = ""):
    conn = get_db()
    try:
        conn.execute(
            """INSERT INTO fetch_log (source, stories_found, stories_new, status, error)
               VALUES (?, ?, ?, ?, ?)""",
            (source, found, new, status, error),
        )
        conn.commit()
    finally:
        conn.close()


def delete_all_stories():
    conn = get_db()
    try:
        conn.execute("DELETE FROM stories")
        conn.commit()
        return conn.total_changes
    finally:
        conn.close()
