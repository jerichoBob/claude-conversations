"""Integration tests for RAG analyze CLI command."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from click.testing import CliRunner

from cli.main import cli, rag_analyze


@pytest.fixture
def runner():
    """Create a CLI runner."""
    return CliRunner()


@pytest.fixture
def temp_analyses_dir(tmp_path):
    """Create a temporary analyses directory."""
    analyses_dir = tmp_path / "analyses"
    analyses_dir.mkdir()
    with patch("core.persistence.ANALYSES_DIR", analyses_dir):
        yield analyses_dir


class TestRagAnalyzeListCommand:
    """Tests for rag-analyze --list command."""

    def test_list_empty(self, runner, temp_analyses_dir):
        """--list with no analyses should show message."""
        result = runner.invoke(cli, ["rag-analyze", "--list"])

        assert result.exit_code == 0
        assert "No saved analyses" in result.output

    def test_list_with_analyses(self, runner, temp_analyses_dir):
        """--list should show saved analyses."""
        from core.persistence import AnalysisResult, save_analysis

        # Create and save an analysis
        analysis = AnalysisResult.create(
            query="test query",
            projects=["proj1"],
            sessions=["sess1"],
            result="test result",
        )
        save_analysis(analysis)

        result = runner.invoke(cli, ["rag-analyze", "--list"])

        assert result.exit_code == 0
        assert "test query" in result.output
        assert analysis.id[:8] in result.output


class TestRagAnalyzeShowCommand:
    """Tests for rag-analyze --show command."""

    def test_show_nonexistent(self, runner, temp_analyses_dir):
        """--show with invalid ID should error."""
        result = runner.invoke(cli, ["rag-analyze", "--show", "nonexistent"])

        assert result.exit_code == 1
        assert "not found" in result.output

    def test_show_existing(self, runner, temp_analyses_dir):
        """--show should display saved analysis."""
        from core.persistence import AnalysisResult, save_analysis

        analysis = AnalysisResult.create(
            query="show test query",
            projects=["proj1", "proj2"],
            sessions=["sess1"],
            result="# Analysis Result\n\nSome markdown content.",
        )
        save_analysis(analysis)

        result = runner.invoke(cli, ["rag-analyze", "--show", analysis.id[:8]])

        assert result.exit_code == 0
        assert "show test query" in result.output
        assert "Analysis Result" in result.output


class TestRagAnalyzeQueryCommand:
    """Tests for rag-analyze with query."""

    def test_query_without_api_key(self, runner, temp_analyses_dir):
        """Query without API key should show helpful error."""
        with patch.dict("os.environ", {}, clear=True):
            import os
            key = os.environ.pop("ANTHROPIC_API_KEY", None)
            try:
                result = runner.invoke(cli, ["rag-analyze", "test query"])

                assert result.exit_code == 1
                assert "ANTHROPIC_API_KEY" in result.output
            finally:
                if key:
                    os.environ["ANTHROPIC_API_KEY"] = key

    def test_query_runs_analysis(self, runner, temp_analyses_dir):
        """Query should run analysis and save results."""
        mock_result = "# Analysis\n\nTest analysis result."

        with patch("core.agents.run_analysis") as mock_run:
            mock_run.return_value = (mock_result, ["sess1"], [{"tool": "test"}])

            result = runner.invoke(cli, ["rag-analyze", "test query"])

            assert result.exit_code == 0
            mock_run.assert_called_once()
            assert "Analysis" in result.output
            assert "saved" in result.output.lower()

    def test_query_with_project_filter(self, runner, temp_analyses_dir):
        """Query should pass project filter to analysis."""
        mock_result = "# Filtered Analysis"

        with patch("core.agents.run_analysis") as mock_run:
            mock_run.return_value = (mock_result, [], [])

            result = runner.invoke(
                cli,
                ["rag-analyze", "test query", "-p", "my-project"]
            )

            assert result.exit_code == 0
            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["project_filter"] == "my-project"

    def test_query_no_results(self, runner, temp_analyses_dir):
        """Query with no results should show warning."""
        with patch("core.agents.run_analysis") as mock_run:
            mock_run.return_value = ("", [], [])

            result = runner.invoke(cli, ["rag-analyze", "test query"])

            assert result.exit_code == 0
            assert "No analysis results" in result.output


class TestRagAnalyzeArguments:
    """Tests for rag-analyze argument parsing."""

    def test_no_arguments_shows_help(self, runner, temp_analyses_dir):
        """No arguments should prompt for query or flags."""
        result = runner.invoke(cli, ["rag-analyze"])

        assert result.exit_code == 1
        assert "query" in result.output.lower() or "list" in result.output.lower()

    def test_custom_model(self, runner, temp_analyses_dir):
        """--model should pass custom model to analysis."""
        with patch("core.agents.run_analysis") as mock_run:
            mock_run.return_value = ("result", [], [])

            result = runner.invoke(
                cli,
                ["rag-analyze", "query", "--model", "claude-3-opus"]
            )

            call_kwargs = mock_run.call_args.kwargs
            assert call_kwargs["model"] == "claude-3-opus"
