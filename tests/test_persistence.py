"""Tests for core/persistence.py - analysis storage."""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from core.persistence import (
    AnalysisResult,
    delete_analysis,
    list_analyses,
    load_analysis,
    save_analysis,
)


@pytest.fixture
def temp_analyses_dir(tmp_path):
    """Create a temporary analyses directory."""
    analyses_dir = tmp_path / "analyses"
    analyses_dir.mkdir()
    with patch("core.persistence.ANALYSES_DIR", analyses_dir):
        yield analyses_dir


class TestAnalysisResult:
    """Tests for AnalysisResult dataclass."""

    def test_create_generates_id_and_timestamp(self):
        """create() should generate unique ID and ISO timestamp."""
        result = AnalysisResult.create(
            query="test query",
            projects=["proj1"],
            sessions=["sess1"],
            result="analysis result",
        )

        assert result.id  # Non-empty
        assert len(result.id) == 36  # UUID format
        assert result.created_at  # Non-empty
        assert "T" in result.created_at  # ISO format

    def test_create_with_agents_log(self):
        """create() should accept agents_log parameter."""
        log = [{"tool": "retrieval", "input": {}}]
        result = AnalysisResult.create(
            query="test",
            projects=[],
            sessions=[],
            result="result",
            agents_log=log,
        )

        assert result.agents_log == log

    def test_create_default_agents_log(self):
        """create() should default to empty agents_log."""
        result = AnalysisResult.create(
            query="test",
            projects=[],
            sessions=[],
            result="result",
        )

        assert result.agents_log == []


class TestSaveAndLoad:
    """Tests for save_analysis and load_analysis."""

    def test_save_creates_json_file(self, temp_analyses_dir):
        """save_analysis should create a JSON file."""
        result = AnalysisResult.create(
            query="test query",
            projects=["proj1"],
            sessions=["sess1"],
            result="analysis result",
        )

        path = save_analysis(result)

        assert path.exists()
        assert path.suffix == ".json"
        assert path.name == f"{result.id}.json"

    def test_save_and_load_roundtrip(self, temp_analyses_dir):
        """save then load should return equivalent result."""
        original = AnalysisResult.create(
            query="test query",
            projects=["proj1", "proj2"],
            sessions=["sess1", "sess2"],
            result="# Analysis\n\nSome markdown",
            agents_log=[{"tool": "retrieval"}],
        )

        save_analysis(original)
        loaded = load_analysis(original.id)

        assert loaded is not None
        assert loaded.id == original.id
        assert loaded.query == original.query
        assert loaded.projects == original.projects
        assert loaded.sessions == original.sessions
        assert loaded.result == original.result
        assert loaded.agents_log == original.agents_log
        assert loaded.created_at == original.created_at

    def test_load_partial_id_match(self, temp_analyses_dir):
        """load_analysis should match on partial ID prefix."""
        result = AnalysisResult.create(
            query="test",
            projects=[],
            sessions=[],
            result="result",
        )
        save_analysis(result)

        # Load with first 8 chars
        loaded = load_analysis(result.id[:8])

        assert loaded is not None
        assert loaded.id == result.id

    def test_load_nonexistent_returns_none(self, temp_analyses_dir):
        """load_analysis should return None for unknown ID."""
        loaded = load_analysis("nonexistent-id")

        assert loaded is None

    def test_load_empty_dir_returns_none(self, temp_analyses_dir):
        """load_analysis should return None when dir is empty."""
        loaded = load_analysis("any-id")

        assert loaded is None


class TestListAnalyses:
    """Tests for list_analyses."""

    def test_list_empty_dir(self, temp_analyses_dir):
        """list_analyses should return empty list for empty dir."""
        result = list_analyses()

        assert result == []

    def test_list_returns_all_analyses(self, temp_analyses_dir):
        """list_analyses should return all saved analyses."""
        # Save 3 analyses
        for i in range(3):
            result = AnalysisResult.create(
                query=f"query {i}",
                projects=[],
                sessions=[],
                result=f"result {i}",
            )
            save_analysis(result)

        analyses = list_analyses()

        assert len(analyses) == 3

    def test_list_sorted_by_created_at_desc(self, temp_analyses_dir):
        """list_analyses should return newest first."""
        # Create analyses with different timestamps
        import time

        results = []
        for i in range(3):
            result = AnalysisResult.create(
                query=f"query {i}",
                projects=[],
                sessions=[],
                result=f"result {i}",
            )
            save_analysis(result)
            results.append(result)
            time.sleep(0.01)  # Small delay for different timestamps

        analyses = list_analyses()

        # Should be in reverse order (newest first)
        assert analyses[0].query == "query 2"
        assert analyses[2].query == "query 0"

    def test_list_respects_limit(self, temp_analyses_dir):
        """list_analyses should respect limit parameter."""
        # Save 5 analyses
        for i in range(5):
            result = AnalysisResult.create(
                query=f"query {i}",
                projects=[],
                sessions=[],
                result=f"result {i}",
            )
            save_analysis(result)

        analyses = list_analyses(limit=3)

        assert len(analyses) == 3


class TestDeleteAnalysis:
    """Tests for delete_analysis."""

    def test_delete_existing(self, temp_analyses_dir):
        """delete_analysis should remove existing analysis."""
        result = AnalysisResult.create(
            query="test",
            projects=[],
            sessions=[],
            result="result",
        )
        path = save_analysis(result)

        assert path.exists()
        deleted = delete_analysis(result.id)

        assert deleted is True
        assert not path.exists()

    def test_delete_partial_id(self, temp_analyses_dir):
        """delete_analysis should work with partial ID."""
        result = AnalysisResult.create(
            query="test",
            projects=[],
            sessions=[],
            result="result",
        )
        path = save_analysis(result)

        deleted = delete_analysis(result.id[:8])

        assert deleted is True
        assert not path.exists()

    def test_delete_nonexistent(self, temp_analyses_dir):
        """delete_analysis should return False for unknown ID."""
        deleted = delete_analysis("nonexistent-id")

        assert deleted is False
