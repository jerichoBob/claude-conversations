"""Tests for core/chunking.py - token estimation and session chunking."""

import pytest

from core.chunking import (
    SessionChunk,
    chunk_multiple_sessions,
    chunk_session,
    estimate_message_tokens,
    estimate_session_tokens,
    estimate_tokens,
    MAX_TOKENS_PER_CHUNK,
)
from core.parser import Message, Session


class TestEstimateTokens:
    """Tests for token estimation functions."""

    def test_estimate_tokens_basic(self):
        """estimate_tokens should return chars / 4."""
        text = "a" * 100
        result = estimate_tokens(text)
        assert result == 25

    def test_estimate_tokens_empty(self):
        """estimate_tokens should handle empty string."""
        result = estimate_tokens("")
        assert result == 0

    def test_estimate_message_tokens_content_only(self):
        """estimate_message_tokens should count content."""
        msg = Message(
            role="user",
            content="a" * 400,  # 100 tokens
        )
        result = estimate_message_tokens(msg)
        assert result == 100

    def test_estimate_message_tokens_with_tool_use(self):
        """estimate_message_tokens should count tool_use."""
        msg = Message(
            role="assistant",
            content="short",
            tool_use=[{"name": "Read", "input": {"file_path": "/test.py"}}],
        )
        result = estimate_message_tokens(msg)
        # Should include content + tool overhead
        assert result > estimate_tokens("short")

    def test_estimate_message_tokens_with_tool_results(self):
        """estimate_message_tokens should count tool_results."""
        msg = Message(
            role="user",
            content="",
            tool_results=[{"tool_use_id": "123", "content": "x" * 400}],
        )
        result = estimate_message_tokens(msg)
        assert result >= 100  # At least the tool result content

    def test_estimate_message_tokens_with_thinking(self):
        """estimate_message_tokens should count thinking."""
        msg = Message(
            role="assistant",
            content="response",
            thinking="y" * 400,  # 100 tokens
        )
        result = estimate_message_tokens(msg)
        thinking_tokens = estimate_tokens("y" * 400)
        content_tokens = estimate_tokens("response")
        assert result >= thinking_tokens + content_tokens


class TestSessionChunk:
    """Tests for SessionChunk dataclass."""

    def test_message_count(self):
        """message_count should return number of messages."""
        msg1 = Message(role="user", content="hi")
        msg2 = Message(role="assistant", content="hello")
        chunk = SessionChunk(
            session_id="test-id",
            session_project="test-project",
            chunk_index=0,
            total_chunks=1,
            messages=[msg1, msg2],
        )
        assert chunk.message_count == 2

    def test_to_text_complete_session(self):
        """to_text should format complete session."""
        msg = Message(role="user", content="test message")
        chunk = SessionChunk(
            session_id="abc12345-full-id",
            session_project="my-project",
            chunk_index=0,
            total_chunks=1,
            messages=[msg],
            is_complete=True,
        )

        text = chunk.to_text()

        assert "abc12345" in text
        assert "my-project" in text
        assert "USER" in text
        assert "test message" in text

    def test_to_text_chunked_session(self):
        """to_text should show chunk info for incomplete session."""
        msg = Message(role="user", content="test")
        chunk = SessionChunk(
            session_id="test-id",
            session_project="proj",
            chunk_index=1,
            total_chunks=3,
            messages=[msg],
            is_complete=False,
        )

        text = chunk.to_text()

        assert "Chunk 2 of 3" in text


class TestChunkSession:
    """Tests for chunk_session function."""

    def create_session(self, message_count: int, content_size: int = 100) -> Session:
        """Helper to create test sessions."""
        from pathlib import Path

        messages = []
        for i in range(message_count):
            role = "user" if i % 2 == 0 else "assistant"
            messages.append(Message(
                role=role,
                content="x" * content_size,
                line_number=i + 1,
            ))

        return Session(
            session_id="test-session-id",
            project="test-project",
            slug=None,
            file_path=Path("/test/path.jsonl"),
            file_mtime=12345.0,
            messages=messages,
        )

    def test_small_session_single_chunk(self):
        """Small session should return single chunk."""
        session = self.create_session(5, content_size=100)

        chunks = chunk_session(session, max_tokens=10000)

        assert len(chunks) == 1
        assert chunks[0].is_complete is True
        assert chunks[0].total_chunks == 1
        assert chunks[0].message_count == 5

    def test_large_session_multiple_chunks(self):
        """Large session should be split into multiple chunks."""
        # Create session that exceeds max_tokens
        session = self.create_session(20, content_size=1000)

        # Use small max_tokens to force chunking
        chunks = chunk_session(session, max_tokens=1000)

        assert len(chunks) > 1
        for chunk in chunks:
            assert chunk.is_complete is False
            assert chunk.total_chunks == len(chunks)

        # All messages should be present across chunks
        total_messages = sum(c.message_count for c in chunks)
        assert total_messages == 20

    def test_chunk_preserves_session_metadata(self):
        """Chunks should preserve session ID and project."""
        session = self.create_session(3, content_size=100)

        chunks = chunk_session(session, max_tokens=10000)

        assert chunks[0].session_id == "test-session-id"
        assert chunks[0].session_project == "test-project"

    def test_chunk_indices_sequential(self):
        """Chunk indices should be sequential starting from 0."""
        session = self.create_session(10, content_size=1000)
        chunks = chunk_session(session, max_tokens=500)

        indices = [c.chunk_index for c in chunks]
        assert indices == list(range(len(chunks)))


class TestChunkMultipleSessions:
    """Tests for chunk_multiple_sessions function."""

    def create_session(self, session_id: str, message_count: int) -> Session:
        """Helper to create test sessions."""
        from pathlib import Path

        messages = [
            Message(role="user", content="x" * 100, line_number=i)
            for i in range(message_count)
        ]

        return Session(
            session_id=session_id,
            project="test-project",
            slug=None,
            file_path=Path("/test/path.jsonl"),
            file_mtime=12345.0,
            messages=messages,
        )

    def test_multiple_small_sessions(self):
        """Multiple small sessions should each be a single chunk."""
        sessions = [
            self.create_session("session-1", 3),
            self.create_session("session-2", 3),
            self.create_session("session-3", 3),
        ]

        chunks = chunk_multiple_sessions(sessions, max_tokens=10000)

        assert len(chunks) == 3
        session_ids = [c.session_id for c in chunks]
        assert "session-1" in session_ids
        assert "session-2" in session_ids
        assert "session-3" in session_ids

    def test_mixed_session_sizes(self):
        """Mix of small and large sessions should be handled correctly."""
        from pathlib import Path

        # Small session
        small_session = self.create_session("small", 2)

        # Large session (will be chunked)
        large_messages = [
            Message(role="user", content="x" * 1000, line_number=i)
            for i in range(20)
        ]
        large_session = Session(
            session_id="large",
            project="proj",
            slug=None,
            file_path=Path("/test.jsonl"),
            file_mtime=12345.0,
            messages=large_messages,
        )

        chunks = chunk_multiple_sessions([small_session, large_session], max_tokens=1000)

        # Should have 1 chunk for small + multiple for large
        small_chunks = [c for c in chunks if c.session_id == "small"]
        large_chunks = [c for c in chunks if c.session_id == "large"]

        assert len(small_chunks) == 1
        assert len(large_chunks) > 1
        assert small_chunks[0].is_complete is True
        assert all(c.is_complete is False for c in large_chunks)
