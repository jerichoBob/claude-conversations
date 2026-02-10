"""Tests for core/agents.py - multi-agent RAG system."""

from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

from core.agents import (
    AgentContext,
    AnalysisAgent,
    BaseAgent,
    ComparisonAgent,
    DecomposedQuery,
    QueryDecomposer,
    RAGAnalyzer,
    check_api_key,
    get_api_key,
)
from core.chunking import SessionChunk
from core.parser import Message, Session
from core.search import SearchResult


class TestDecomposedQuery:
    """Tests for DecomposedQuery dataclass."""

    def test_basic_query(self):
        """DecomposedQuery should store all fields."""
        dq = DecomposedQuery(
            original_query="test query",
            search_queries=["term1", "term2"],
            analysis_prompt="Analyze this",
            comparison_needed=True,
        )
        assert dq.original_query == "test query"
        assert dq.search_queries == ["term1", "term2"]
        assert dq.analysis_prompt == "Analyze this"
        assert dq.comparison_needed is True


class TestAgentContext:
    """Tests for AgentContext dataclass."""

    def test_default_empty(self):
        """AgentContext should default to empty lists."""
        ctx = AgentContext()
        assert ctx.sessions == []
        assert ctx.search_results == []
        assert ctx.session_chunks == []
        assert ctx.analyses == {}


class TestGetApiKey:
    """Tests for API key functions."""

    def test_get_api_key_from_env(self):
        """get_api_key should return env var value."""
        with patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"}):
            assert get_api_key() == "test-key"

    def test_get_api_key_missing(self):
        """get_api_key should return None if not set."""
        with patch.dict("os.environ", {}, clear=True):
            import os
            key = os.environ.pop("ANTHROPIC_API_KEY", None)
            try:
                assert get_api_key() is None
            finally:
                if key:
                    os.environ["ANTHROPIC_API_KEY"] = key

    def test_check_api_key_raises(self):
        """check_api_key should raise RuntimeError if not set."""
        with patch("core.agents.get_api_key", return_value=None):
            with pytest.raises(RuntimeError) as exc_info:
                check_api_key()
            assert "ANTHROPIC_API_KEY" in str(exc_info.value)


class TestBaseAgent:
    """Tests for BaseAgent class."""

    def test_init_with_client(self):
        """BaseAgent should accept external client."""
        mock_client = MagicMock()
        agent = BaseAgent(client=mock_client)
        assert agent._client is mock_client

    def test_init_default_model(self):
        """BaseAgent should use default model."""
        agent = BaseAgent()
        assert agent.model == "claude-sonnet-4-20250514"

    def test_init_custom_model(self):
        """BaseAgent should accept custom model."""
        agent = BaseAgent(model="claude-3-opus")
        assert agent.model == "claude-3-opus"


class TestQueryDecomposer:
    """Tests for QueryDecomposer."""

    def test_decompose_returns_decomposed_query(self):
        """decompose should return DecomposedQuery."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text='{"search_queries": ["test"], "analysis_prompt": "analyze", "comparison_needed": false}')]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        decomposer = QueryDecomposer(client=mock_client)
        result = decomposer.decompose("test query")

        assert isinstance(result, DecomposedQuery)
        assert result.original_query == "test query"
        assert "test" in result.search_queries

    def test_decompose_fallback_on_parse_error(self):
        """decompose should fallback gracefully on parse errors."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="invalid json")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        decomposer = QueryDecomposer(client=mock_client)
        result = decomposer.decompose("test query")

        # Should fallback to using original query
        assert result.original_query == "test query"
        assert "test query" in result.search_queries


class TestAnalysisAgent:
    """Tests for AnalysisAgent."""

    def create_test_chunk(self) -> SessionChunk:
        """Create a test SessionChunk."""
        return SessionChunk(
            session_id="test-session",
            session_project="test-project",
            chunk_index=0,
            total_chunks=1,
            messages=[
                Message(role="user", content="test query", line_number=1),
                Message(role="assistant", content="test response", line_number=2),
            ],
            is_complete=True,
        )

    def test_analyze_calls_api(self):
        """analyze should call API with context."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Analysis result")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        agent = AnalysisAgent(client=mock_client)
        chunks = [self.create_test_chunk()]

        result = agent.analyze(chunks, "test query")

        mock_client.messages.create.assert_called_once()
        assert result == "Analysis result"

    def test_analyze_empty_chunks(self):
        """analyze should handle empty chunks list."""
        agent = AnalysisAgent()
        result = agent.analyze([], "test query")
        assert "No sessions" in result


class TestComparisonAgent:
    """Tests for ComparisonAgent."""

    def test_compare_requires_two_analyses(self):
        """compare should handle single analysis gracefully."""
        mock_client = MagicMock()
        agent = ComparisonAgent(client=mock_client)

        result = agent.compare({"sess1": "analysis1"}, "query")

        # Should return the single analysis
        assert "analysis1" in result

    def test_compare_calls_api_with_multiple(self):
        """compare should call API with multiple analyses."""
        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="Comparison result")]

        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_response

        agent = ComparisonAgent(client=mock_client)
        analyses = {
            "sess1": "Analysis of session 1",
            "sess2": "Analysis of session 2",
        }

        result = agent.compare(analyses, "compare query")

        mock_client.messages.create.assert_called_once()
        assert result == "Comparison result"


class TestRAGAnalyzer:
    """Tests for RAGAnalyzer orchestrator."""

    def test_init_creates_agents(self):
        """RAGAnalyzer should create all agent instances."""
        analyzer = RAGAnalyzer()

        assert analyzer.decomposer is not None
        assert analyzer.analyzer is not None
        assert analyzer.comparator is not None

    def test_progress_callback(self):
        """RAGAnalyzer should call progress callback."""
        progress_calls = []

        def capture_progress(stage, detail):
            progress_calls.append((stage, detail))

        analyzer = RAGAnalyzer(progress=capture_progress)
        analyzer._log("test_stage", "test detail")

        assert len(progress_calls) == 1
        assert progress_calls[0] == ("test_stage", "test detail")

    def test_log_entries(self):
        """RAGAnalyzer should maintain log entries."""
        analyzer = RAGAnalyzer()
        analyzer._log("stage1", "detail1")
        analyzer._log("stage2", "detail2")

        assert len(analyzer.log) == 2
        assert analyzer.log[0]["stage"] == "stage1"
        assert analyzer.log[1]["detail"] == "detail2"
