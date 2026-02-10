"""Search functionality using the FTS5 index."""

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .index import get_db_path, init_db
from .parser import parse_session, Session


def sanitize_fts_query(query: str) -> str:
    """Sanitize a query string for FTS5.

    Removes or escapes special FTS5 syntax characters that could cause errors.
    Preserves quoted phrases and basic word matching.
    """
    # Remove problematic characters that aren't valid FTS5 operators
    # but could cause syntax errors
    # Keep: alphanumeric, spaces, quotes (for phrases), * (for prefix), - (for NOT)
    # Remove: ? ^ ~ and other invalid syntax
    query = re.sub(r'[?^~]', ' ', query)

    # Collapse multiple spaces
    query = re.sub(r'\s+', ' ', query).strip()

    return query


@dataclass
class SearchResult:
    """A single search result."""
    session_id: str
    project: str
    timestamp: Optional[str]
    role: str
    content: str
    line_number: int
    snippet: str  # Highlighted snippet from FTS5


@dataclass
class SessionInfo:
    """Summary information about a session."""
    session_id: str
    project: str
    slug: Optional[str]
    first_message: Optional[str]
    start_time: Optional[str]
    end_time: Optional[str]
    message_count: int
    file_path: str


@dataclass
class ProjectInfo:
    """Summary information about a project."""
    name: str
    session_count: int
    message_count: int
    last_active: Optional[str]
    first_active: Optional[str]


def ensure_index(db_path: Optional[Path] = None) -> sqlite3.Connection:
    """Ensure the index exists and return a connection."""
    if db_path is None:
        db_path = get_db_path()

    if not db_path.exists():
        raise RuntimeError(
            "Search index not found. Run 'claude-conversations reindex' first."
        )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def search(
    query: str,
    project: Optional[str] = None,
    role: Optional[str] = None,
    limit: int = 50,
    db_path: Optional[Path] = None,
) -> list[SearchResult]:
    """Search messages using FTS5.

    Args:
        query: Search query (supports FTS5 syntax)
        project: Optional project filter (supports wildcards with *)
        role: Optional role filter ('user' or 'assistant')
        limit: Maximum results to return
        db_path: Optional database path

    Returns:
        List of SearchResult objects
    """
    conn = ensure_index(db_path)

    # Sanitize the query to prevent FTS5 syntax errors
    safe_query = sanitize_fts_query(query)
    if not safe_query:
        return []

    # Build the query
    # Use snippet() to get highlighted excerpts
    sql = """
        SELECT
            session_id,
            project,
            timestamp,
            role,
            content,
            line_number,
            snippet(messages, 4, '>>>', '<<<', '...', 50) as snippet
        FROM messages
        WHERE messages MATCH ?
    """
    params = [safe_query]

    if project:
        # Convert glob pattern to SQL LIKE
        project_pattern = project.replace('*', '%')
        sql += " AND project LIKE ?"
        params.append(project_pattern)

    if role:
        sql += " AND role = ?"
        params.append(role)

    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)

    results = []
    try:
        cursor = conn.execute(sql, params)
        for row in cursor:
            results.append(SearchResult(
                session_id=row["session_id"],
                project=row["project"],
                timestamp=row["timestamp"],
                role=row["role"],
                content=row["content"],
                line_number=row["line_number"],
                snippet=row["snippet"],
            ))
    except sqlite3.OperationalError as e:
        error_msg = str(e)
        if "no such table" in error_msg:
            raise RuntimeError(
                "Search index not found. Run 'claude-conversations reindex' first."
            )
        if "fts5: syntax error" in error_msg or "malformed MATCH" in error_msg:
            raise RuntimeError(
                f"Invalid search query. Special characters like ? * \" may need escaping. Error: {error_msg}"
            )
        raise RuntimeError(f"Search error: {error_msg}")

    conn.close()
    return results


def get_sessions(
    project: Optional[str] = None,
    limit: int = 100,
    db_path: Optional[Path] = None,
) -> list[SessionInfo]:
    """Get sessions, optionally filtered by project.

    Args:
        project: Optional project filter (supports wildcards with *)
        limit: Maximum results to return
        db_path: Optional database path

    Returns:
        List of SessionInfo objects, sorted by start_time descending
    """
    conn = ensure_index(db_path)

    sql = """
        SELECT
            session_id, project, slug, first_message,
            start_time, end_time, message_count, file_path
        FROM sessions
    """
    params = []

    if project:
        project_pattern = project.replace('*', '%')
        sql += " WHERE project LIKE ?"
        params.append(project_pattern)

    sql += " ORDER BY start_time DESC LIMIT ?"
    params.append(limit)

    results = []
    cursor = conn.execute(sql, params)
    for row in cursor:
        results.append(SessionInfo(
            session_id=row["session_id"],
            project=row["project"],
            slug=row["slug"],
            first_message=row["first_message"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            message_count=row["message_count"],
            file_path=row["file_path"],
        ))

    conn.close()
    return results


def get_recent(
    n: int = 10,
    db_path: Optional[Path] = None,
) -> list[SessionInfo]:
    """Get the N most recent sessions.

    Args:
        n: Number of sessions to return
        db_path: Optional database path

    Returns:
        List of SessionInfo objects, sorted by start_time descending
    """
    return get_sessions(limit=n, db_path=db_path)


def get_projects(
    db_path: Optional[Path] = None,
) -> list[ProjectInfo]:
    """Get all projects with statistics.

    Args:
        db_path: Optional database path

    Returns:
        List of ProjectInfo objects, sorted by last_active descending
    """
    conn = ensure_index(db_path)

    sql = """
        SELECT
            s.project as name,
            COUNT(DISTINCT s.session_id) as session_count,
            SUM(s.message_count) as message_count,
            MAX(s.end_time) as last_active,
            MIN(s.start_time) as first_active
        FROM sessions s
        GROUP BY s.project
        ORDER BY last_active DESC
    """

    results = []
    cursor = conn.execute(sql)
    for row in cursor:
        results.append(ProjectInfo(
            name=row["name"],
            session_count=row["session_count"],
            message_count=row["message_count"] or 0,
            last_active=row["last_active"],
            first_active=row["first_active"],
        ))

    conn.close()
    return results


def get_session_by_id(
    session_id: str,
    db_path: Optional[Path] = None,
) -> Optional[SessionInfo]:
    """Get a session by its ID (can be partial).

    Args:
        session_id: Full or partial session ID
        db_path: Optional database path

    Returns:
        SessionInfo if found, None otherwise
    """
    conn = ensure_index(db_path)

    # Try exact match first
    cursor = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?",
        (session_id,)
    )
    row = cursor.fetchone()

    # Try prefix match if exact match fails
    if row is None:
        cursor = conn.execute(
            "SELECT * FROM sessions WHERE session_id LIKE ? LIMIT 1",
            (f"{session_id}%",)
        )
        row = cursor.fetchone()

    conn.close()

    if row is None:
        return None

    return SessionInfo(
        session_id=row["session_id"],
        project=row["project"],
        slug=row["slug"],
        first_message=row["first_message"],
        start_time=row["start_time"],
        end_time=row["end_time"],
        message_count=row["message_count"],
        file_path=row["file_path"],
    )


def load_session(session_id: str, db_path: Optional[Path] = None) -> Session:
    """Load a full session from disk.

    Args:
        session_id: Full or partial session ID
        db_path: Optional database path

    Returns:
        Session object with all messages

    Raises:
        ValueError: If session not found
    """
    info = get_session_by_id(session_id, db_path)
    if info is None:
        raise ValueError(f"Session not found: {session_id}")

    file_path = Path(info.file_path)
    if not file_path.exists():
        raise ValueError(f"Session file not found: {file_path}")

    return parse_session(file_path)
