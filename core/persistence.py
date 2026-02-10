"""Persistence layer for RAG analysis results."""

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


ANALYSES_DIR = Path.home() / ".claude-conversations" / "analyses"


@dataclass
class AnalysisResult:
    """A saved RAG analysis result."""
    id: str                           # UUID
    query: str                        # Original user query
    projects: list[str]               # Projects analyzed
    sessions: list[str]               # Session IDs used
    result: str                       # Markdown analysis output
    agents_log: list[dict]            # Coordinator + specialist interactions
    created_at: str                   # ISO timestamp

    @classmethod
    def create(
        cls,
        query: str,
        projects: list[str],
        sessions: list[str],
        result: str,
        agents_log: list[dict] = None,
    ) -> "AnalysisResult":
        """Create a new analysis result with generated ID and timestamp."""
        return cls(
            id=str(uuid.uuid4()),
            query=query,
            projects=projects,
            sessions=sessions,
            result=result,
            agents_log=agents_log or [],
            created_at=datetime.now().isoformat(),
        )


def ensure_analyses_dir() -> Path:
    """Ensure the analyses directory exists."""
    ANALYSES_DIR.mkdir(parents=True, exist_ok=True)
    return ANALYSES_DIR


def save_analysis(result: AnalysisResult) -> Path:
    """Save analysis to JSON file.

    Args:
        result: The analysis result to save

    Returns:
        Path to the saved file
    """
    dir_path = ensure_analyses_dir()
    file_path = dir_path / f"{result.id}.json"

    with open(file_path, 'w', encoding='utf-8') as f:
        json.dump(asdict(result), f, indent=2)

    return file_path


def list_analyses(limit: int = 50) -> list[AnalysisResult]:
    """List all saved analyses, newest first.

    Args:
        limit: Maximum number of results to return

    Returns:
        List of AnalysisResult objects sorted by created_at descending
    """
    if not ANALYSES_DIR.exists():
        return []

    analyses = []
    for file_path in ANALYSES_DIR.glob("*.json"):
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                analyses.append(AnalysisResult(**data))
        except (json.JSONDecodeError, TypeError, KeyError):
            continue  # Skip invalid files

    # Sort by created_at descending
    analyses.sort(key=lambda a: a.created_at, reverse=True)
    return analyses[:limit]


def load_analysis(analysis_id: str) -> Optional[AnalysisResult]:
    """Load analysis by ID (supports partial ID matching).

    Args:
        analysis_id: Full or partial analysis ID

    Returns:
        AnalysisResult if found, None otherwise
    """
    if not ANALYSES_DIR.exists():
        return None

    # Try exact match first
    exact_path = ANALYSES_DIR / f"{analysis_id}.json"
    if exact_path.exists():
        with open(exact_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return AnalysisResult(**data)

    # Try prefix match
    for file_path in ANALYSES_DIR.glob("*.json"):
        if file_path.stem.startswith(analysis_id):
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return AnalysisResult(**data)

    return None


def delete_analysis(analysis_id: str) -> bool:
    """Delete an analysis by ID.

    Args:
        analysis_id: Full or partial analysis ID

    Returns:
        True if deleted, False if not found
    """
    if not ANALYSES_DIR.exists():
        return False

    # Try exact match first
    exact_path = ANALYSES_DIR / f"{analysis_id}.json"
    if exact_path.exists():
        exact_path.unlink()
        return True

    # Try prefix match
    for file_path in ANALYSES_DIR.glob("*.json"):
        if file_path.stem.startswith(analysis_id):
            file_path.unlink()
            return True

    return False
