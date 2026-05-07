"""
SQLite persistence layer for events, contacts, and email drafts.
"""

import json
import sqlite3
import struct
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent.parent / "data" / "events.db"


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # rows behave like dicts
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Safe to call on every startup."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                title             TEXT NOT NULL,
                date              TEXT,
                venue             TEXT,
                city              TEXT,
                genre             TEXT,
                promoter          TEXT,
                description       TEXT,
                contact_email     TEXT,
                contact_website   TEXT,
                contact_instagram TEXT,
                ra_url            TEXT UNIQUE,
                ra_promoter_url   TEXT,
                embedding         BLOB,
                created_at        TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS drafts (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id   INTEGER REFERENCES events(id),
                body       TEXT NOT NULL,
                tone       TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS outreach_log (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                promoter_name    TEXT NOT NULL,
                ra_promoter_url  TEXT,
                event_id         INTEGER REFERENCES events(id),
                draft_id         INTEGER REFERENCES drafts(id),
                sent_at          TEXT DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS batches (
                id           TEXT PRIMARY KEY,
                submitted_at TEXT DEFAULT (datetime('now')),
                status       TEXT DEFAULT 'pending',
                event_map    TEXT NOT NULL
            );
        """)


def save_event(event) -> Optional[int]:
    """
    Insert event, skip if ra_url already exists (idempotent).
    Returns the new row id, or None if it was a duplicate.
    """
    with get_connection() as conn:
        try:
            cursor = conn.execute(
                """INSERT INTO events
                   (title, date, venue, city, genre, promoter, description,
                    contact_email, contact_website, contact_instagram, ra_url, ra_promoter_url)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event.title, event.date, event.venue, event.city, event.genre,
                 event.promoter, event.description, event.contact_email,
                 event.contact_website, event.contact_instagram,
                 event.ra_url, event.ra_promoter_url),
            )
            return cursor.lastrowid
        except sqlite3.IntegrityError:
            return None  # duplicate


def get_event(event_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def save_draft(event_id: int, body: str, tone: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO drafts (event_id, body, tone) VALUES (?, ?, ?)",
            (event_id, body, tone),
        )
        return cursor.lastrowid


def list_events(limit: int = 50) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()


def save_embedding(event_id: int, vector: list[float]) -> None:
    """Serialize a float list to bytes and store in the embedding column."""
    blob = struct.pack(f"{len(vector)}f", *vector)
    with get_connection() as conn:
        conn.execute("UPDATE events SET embedding = ? WHERE id = ?", (blob, event_id))


def load_embedding(event_id: int) -> Optional[list[float]]:
    """Load and deserialize an embedding vector, or None if not yet embedded."""
    with get_connection() as conn:
        row = conn.execute("SELECT embedding FROM events WHERE id = ?", (event_id,)).fetchone()
    if row and row["embedding"]:
        n = len(row["embedding"]) // 4  # 4 bytes per float
        return list(struct.unpack(f"{n}f", row["embedding"]))
    return None


def log_outreach(promoter_name: str, ra_promoter_url: Optional[str], event_id: int, draft_id: int) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO outreach_log (promoter_name, ra_promoter_url, event_id, draft_id)
               VALUES (?, ?, ?, ?)""",
            (promoter_name, ra_promoter_url, event_id, draft_id),
        )
        return cursor.lastrowid


def get_cooldown_status(ra_promoter_url: Optional[str], promoter_name: str, cooldown_days: int) -> Optional[sqlite3.Row]:
    """
    Returns the most recent outreach log entry for this promoter if they're
    still within the cooldown window, otherwise None.
    Matches on ra_promoter_url if available, falls back to promoter_name.
    """
    with get_connection() as conn:
        if ra_promoter_url:
            row = conn.execute(
                """SELECT *, julianday('now') - julianday(sent_at) AS days_ago
                   FROM outreach_log
                   WHERE ra_promoter_url = ?
                   ORDER BY sent_at DESC LIMIT 1""",
                (ra_promoter_url,),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT *, julianday('now') - julianday(sent_at) AS days_ago
                   FROM outreach_log
                   WHERE promoter_name = ?
                   ORDER BY sent_at DESC LIMIT 1""",
                (promoter_name,),
            ).fetchone()

        if row and row["days_ago"] <= cooldown_days:
            return row
        return None


def list_outreach_log() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("""
            SELECT o.id, o.promoter_name, o.sent_at, o.draft_id,
                   e.title, e.venue,
                   julianday('now') - julianday(o.sent_at) AS days_ago
            FROM outreach_log o
            JOIN events e ON e.id = o.event_id
            ORDER BY o.sent_at DESC
        """).fetchall()


def save_batch(batch_id: str, event_map: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO batches (id, event_map) VALUES (?, ?)",
            (batch_id, json.dumps(event_map)),
        )


def get_batch(batch_id: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM batches WHERE id = ?", (batch_id,)).fetchone()


def update_batch_status(batch_id: str, status: str) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE batches SET status = ? WHERE id = ?", (status, batch_id))


def list_batches() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM batches ORDER BY submitted_at DESC"
        ).fetchall()


def get_event_by_url(ra_url: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM events WHERE ra_url = ?", (ra_url,)).fetchone()


def list_drafts() -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("""
            SELECT d.id, d.event_id, d.tone, d.created_at, d.body,
                   e.title, e.venue
            FROM drafts d
            JOIN events e ON e.id = d.event_id
            ORDER BY d.created_at DESC
        """).fetchall()
