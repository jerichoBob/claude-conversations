"""SQLite FTS5 index for fast full-text search."""

import sqlite3
from pathlib import Path
from typing import Optional

from .parser import Session, iter_sessions, parse_session, get_projects_dir


def get_db_path() -> Path:
    """Get the default database path."""
    db_dir = Path.home() / ".claude-conversations"
    db_dir.mkdir(parents=True, exist_ok=True)
    return db_dir / "index.db"


def init_db(db_path: Path) -> sqlite3.Connection:
    """Initialize the database with required tables."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Create sessions table
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            project TEXT NOT NULL,
            slug TEXT,
            first_message TEXT,
            start_time TEXT,
            end_time TEXT,
            message_count INTEGER,
            file_path TEXT NOT NULL,
            file_mtime REAL NOT NULL
        )
    """)

    # Create FTS5 virtual table for message content
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS messages USING fts5(
            session_id,
            project,
            timestamp,
            role,
            content,
            line_number UNINDEXED,
            tokenize='porter unicode61'
        )
    """)

    # Create index for faster session lookups
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_project
        ON sessions(project)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_sessions_start_time
        ON sessions(start_time DESC)
    """)

    conn.commit()
    return conn


def needs_reindex(conn: sqlite3.Connection, file_path: Path) -> bool:
    """Check if a session file needs to be (re)indexed."""
    cursor = conn.execute(
        "SELECT file_mtime FROM sessions WHERE file_path = ?",
        (str(file_path),)
    )
    row = cursor.fetchone()

    if row is None:
        return True

    return file_path.stat().st_mtime > row["file_mtime"]


def index_session(conn: sqlite3.Connection, session: Session) -> None:
    """Index a session and its messages."""
    # Remove old data for this session
    conn.execute(
        "DELETE FROM sessions WHERE session_id = ?",
        (session.session_id,)
    )
    conn.execute(
        "DELETE FROM messages WHERE session_id = ?",
        (session.session_id,)
    )

    # Insert session metadata
    conn.execute("""
        INSERT INTO sessions (
            session_id, project, slug, first_message,
            start_time, end_time, message_count, file_path, file_mtime
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        session.session_id,
        session.project,
        session.slug,
        session.first_message,
        session.start_time,
        session.end_time,
        session.message_count,
        str(session.file_path),
        session.file_mtime,
    ))

    # Insert messages for full-text search
    for msg in session.messages:
        if msg.content:  # Only index messages with content
            conn.execute("""
                INSERT INTO messages (
                    session_id, project, timestamp, role, content, line_number
                ) VALUES (?, ?, ?, ?, ?, ?)
            """, (
                session.session_id,
                session.project,
                msg.timestamp,
                msg.role,
                msg.content,
                msg.line_number,
            ))

    conn.commit()


def build_index(
    projects_dir: Optional[Path] = None,
    db_path: Optional[Path] = None,
    force: bool = False,
    progress_callback=None
) -> tuple[int, int]:
    """Build or update the search index.

    Args:
        projects_dir: Directory containing project folders
        db_path: Path to the SQLite database
        force: If True, reindex all sessions regardless of mtime
        progress_callback: Optional callback(current, total, session_id) for progress

    Returns:
        Tuple of (sessions_indexed, sessions_skipped)
    """
    if projects_dir is None:
        projects_dir = get_projects_dir()

    if db_path is None:
        db_path = get_db_path()

    conn = init_db(db_path)

    # Collect all session files
    session_files = list(iter_sessions(projects_dir))
    total = len(session_files)

    indexed = 0
    skipped = 0

    for i, jsonl_path in enumerate(session_files):
        session_id = jsonl_path.stem

        if progress_callback:
            progress_callback(i + 1, total, session_id)

        if not force and not needs_reindex(conn, jsonl_path):
            skipped += 1
            continue

        try:
            session = parse_session(jsonl_path)
            index_session(conn, session)
            indexed += 1
        except Exception as e:
            # Log error but continue with other sessions
            if progress_callback:
                progress_callback(i + 1, total, f"ERROR: {jsonl_path.name}: {e}")

    conn.close()
    return indexed, skipped


def get_stats(db_path: Optional[Path] = None) -> dict:
    """Get statistics from the index."""
    if db_path is None:
        db_path = get_db_path()

    if not db_path.exists():
        return {
            "indexed": False,
            "projects": 0,
            "sessions": 0,
            "messages": 0,
        }

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    stats = {"indexed": True}

    # Count projects
    cursor = conn.execute("SELECT COUNT(DISTINCT project) as count FROM sessions")
    stats["projects"] = cursor.fetchone()["count"]

    # Count sessions
    cursor = conn.execute("SELECT COUNT(*) as count FROM sessions")
    stats["sessions"] = cursor.fetchone()["count"]

    # Count messages
    cursor = conn.execute("SELECT COUNT(*) as count FROM messages")
    stats["messages"] = cursor.fetchone()["count"]

    # Date range
    cursor = conn.execute("""
        SELECT MIN(start_time) as earliest, MAX(end_time) as latest
        FROM sessions WHERE start_time IS NOT NULL
    """)
    row = cursor.fetchone()
    stats["earliest"] = row["earliest"]
    stats["latest"] = row["latest"]

    # Most active project
    cursor = conn.execute("""
        SELECT project, COUNT(*) as count
        FROM sessions
        GROUP BY project
        ORDER BY count DESC
        LIMIT 1
    """)
    row = cursor.fetchone()
    if row:
        stats["most_active_project"] = row["project"]
        stats["most_active_count"] = row["count"]

    conn.close()
    return stats


def clear_index(db_path: Optional[Path] = None) -> None:
    """Clear all data from the index."""
    if db_path is None:
        db_path = get_db_path()

    if db_path.exists():
        conn = sqlite3.connect(db_path)
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM sessions")
        conn.commit()
        conn.close()
