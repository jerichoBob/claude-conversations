"""Token estimation and session chunking for large conversations."""

from dataclasses import dataclass, field
from typing import Optional

from .parser import Message, Session


# Leave room for system prompt + response
MAX_TOKENS_PER_CHUNK = 50000


@dataclass
class SessionChunk:
    """A chunk of a session, potentially split for token limits."""
    session_id: str
    session_project: str
    chunk_index: int
    total_chunks: int
    messages: list[Message] = field(default_factory=list)
    is_complete: bool = True  # True if this is the entire session

    @property
    def message_count(self) -> int:
        return len(self.messages)

    def to_text(self) -> str:
        """Convert chunk to plain text representation."""
        lines = []
        lines.append(f"Session: {self.session_id[:8]} (Project: {self.session_project})")
        if not self.is_complete:
            lines.append(f"Chunk {self.chunk_index + 1} of {self.total_chunks}")
        lines.append("-" * 60)

        for i, msg in enumerate(self.messages, 1):
            role = "USER" if msg.role == "user" else "ASSISTANT"
            lines.append(f"\n[{i}. {role}]")
            if msg.content:
                lines.append(msg.content[:5000])  # Truncate very long messages
            if msg.tool_use:
                tool_names = [t.get("name", "unknown") for t in msg.tool_use]
                lines.append(f"  Tools: {', '.join(tool_names)}")

        return "\n".join(lines)


def estimate_tokens(text: str) -> int:
    """Rough token estimate (chars / 4).

    This is a simple approximation. Actual tokenization varies by model,
    but chars/4 is a reasonable estimate for English text.
    """
    return len(text) // 4


def estimate_message_tokens(msg: Message) -> int:
    """Estimate tokens for a single message."""
    total = 0

    # Content
    if msg.content:
        total += estimate_tokens(msg.content)

    # Tool use (approximate as JSON)
    for tool in msg.tool_use:
        tool_text = str(tool.get("input", {}))
        total += estimate_tokens(tool_text) + 50  # Add overhead for tool structure

    # Tool results
    for result in msg.tool_results:
        result_text = str(result.get("content", ""))
        total += estimate_tokens(result_text)

    # Thinking
    if msg.thinking:
        total += estimate_tokens(msg.thinking)

    return total


def estimate_session_tokens(session: Session) -> int:
    """Estimate total tokens for a session."""
    return sum(estimate_message_tokens(msg) for msg in session.messages)


def chunk_session(
    session: Session,
    max_tokens: int = MAX_TOKENS_PER_CHUNK,
) -> list[SessionChunk]:
    """Split session into message-boundary-aligned chunks.

    Args:
        session: The session to chunk
        max_tokens: Maximum tokens per chunk

    Returns:
        List of SessionChunk objects
    """
    total_tokens = estimate_session_tokens(session)

    # If small enough, return as single chunk
    if total_tokens <= max_tokens:
        return [SessionChunk(
            session_id=session.session_id,
            session_project=session.project,
            chunk_index=0,
            total_chunks=1,
            messages=session.messages,
            is_complete=True,
        )]

    # Need to split into multiple chunks
    chunks = []
    current_messages = []
    current_tokens = 0

    for msg in session.messages:
        msg_tokens = estimate_message_tokens(msg)

        # If single message exceeds limit, include it anyway (will be truncated later)
        if msg_tokens > max_tokens and not current_messages:
            current_messages.append(msg)
            current_tokens = msg_tokens
            continue

        # Check if adding this message would exceed limit
        if current_tokens + msg_tokens > max_tokens and current_messages:
            # Save current chunk and start new one
            chunks.append(current_messages)
            current_messages = [msg]
            current_tokens = msg_tokens
        else:
            current_messages.append(msg)
            current_tokens += msg_tokens

    # Don't forget the last chunk
    if current_messages:
        chunks.append(current_messages)

    # Convert to SessionChunk objects
    total_chunks = len(chunks)
    return [
        SessionChunk(
            session_id=session.session_id,
            session_project=session.project,
            chunk_index=i,
            total_chunks=total_chunks,
            messages=messages,
            is_complete=(total_chunks == 1),
        )
        for i, messages in enumerate(chunks)
    ]


def chunk_multiple_sessions(
    sessions: list[Session],
    max_tokens: int = MAX_TOKENS_PER_CHUNK,
) -> list[SessionChunk]:
    """Chunk multiple sessions, preserving session boundaries where possible.

    Sessions that fit within the limit are kept whole. Large sessions are
    chunked individually.

    Args:
        sessions: List of sessions to chunk
        max_tokens: Maximum tokens per chunk

    Returns:
        List of SessionChunk objects
    """
    all_chunks = []

    for session in sessions:
        session_chunks = chunk_session(session, max_tokens)
        all_chunks.extend(session_chunks)

    return all_chunks


def combine_chunk_analyses(analyses: list[str], query: str) -> str:
    """Combine multiple chunk analyses into a coherent summary.

    This is a simple concatenation; the coordinator agent will typically
    handle synthesis with an LLM.

    Args:
        analyses: List of analysis results from individual chunks
        query: Original user query

    Returns:
        Combined analysis text
    """
    lines = [f"## Combined Analysis for: {query}\n"]

    for i, analysis in enumerate(analyses, 1):
        lines.append(f"### Chunk {i} Analysis")
        lines.append(analysis)
        lines.append("")

    return "\n".join(lines)
