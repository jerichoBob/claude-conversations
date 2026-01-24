"""Parse JSONL conversation files from Claude Code."""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional


@dataclass
class CodeBlock:
    """A code block extracted from content."""
    language: str
    code: str
    line_number: int


@dataclass
class Message:
    """A single message in a conversation."""
    role: str
    content: str
    timestamp: Optional[str] = None
    uuid: Optional[str] = None
    line_number: int = 0
    tool_use: list = field(default_factory=list)
    tool_results: list = field(default_factory=list)
    thinking: Optional[str] = None


@dataclass
class Session:
    """A complete conversation session."""
    session_id: str
    project: str
    slug: Optional[str]
    file_path: Path
    file_mtime: float
    messages: list[Message] = field(default_factory=list)
    start_time: Optional[str] = None
    end_time: Optional[str] = None

    @property
    def first_message(self) -> Optional[str]:
        """Get the first user message as a summary."""
        for msg in self.messages:
            if msg.role == "user" and msg.content:
                # Truncate to first line or 100 chars
                text = msg.content.strip()
                first_line = text.split('\n')[0]
                if len(first_line) > 100:
                    return first_line[:97] + "..."
                return first_line
        return None

    @property
    def message_count(self) -> int:
        return len(self.messages)


def get_project_name(project_path: Path) -> str:
    """Extract a readable project name from the path-encoded directory name.

    Converts '-Users-bseaton-Work-foo-bar' to 'foo-bar' (last component).
    """
    name = project_path.name
    # Split on path separator encoding and take the last meaningful part
    parts = name.split('-')

    # Find where the actual project name starts (after the path prefix)
    # Look for common path prefixes: Users, home, Work, Play, etc.
    skip_prefixes = {'Users', 'home', 'Work', 'Play', 'Projects', 'github', 'repos'}

    result_parts = []
    found_project = False

    for i, part in enumerate(parts):
        if not part:  # Skip empty parts
            continue
        if part in skip_prefixes and not found_project:
            continue
        # Skip username (usually follows 'Users')
        if i > 0 and parts[i-1] == 'Users' and not found_project:
            continue
        found_project = True
        result_parts.append(part)

    if result_parts:
        return '-'.join(result_parts)
    return name


def extract_text_content(message_data: dict) -> str:
    """Extract plain text content from a message.

    Handles both simple string content and complex content blocks.
    """
    content = message_data.get("content", "")

    # Simple string content
    if isinstance(content, str):
        return content

    # Array of content blocks
    if isinstance(content, list):
        text_parts = []
        for block in content:
            if isinstance(block, str):
                text_parts.append(block)
            elif isinstance(block, dict):
                block_type = block.get("type", "")
                if block_type == "text":
                    text_parts.append(block.get("text", ""))
                elif block_type == "tool_result":
                    # Include tool result content if it's text
                    result_content = block.get("content", "")
                    if isinstance(result_content, str):
                        text_parts.append(result_content)
                    elif isinstance(result_content, list):
                        for item in result_content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                text_parts.append(item.get("text", ""))
        return "\n".join(text_parts)

    return ""


def extract_thinking(message_data: dict) -> Optional[str]:
    """Extract thinking content from a message."""
    content = message_data.get("content", [])

    if not isinstance(content, list):
        return None

    thinking_parts = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            thinking_parts.append(block.get("thinking", ""))

    return "\n".join(thinking_parts) if thinking_parts else None


def extract_tool_use(message_data: dict) -> list[dict]:
    """Extract tool use blocks from a message."""
    content = message_data.get("content", [])

    if not isinstance(content, list):
        return []

    tools = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            tools.append({
                "id": block.get("id"),
                "name": block.get("name"),
                "input": block.get("input", {}),
            })

    return tools


def extract_tool_results(message_data: dict) -> list[dict]:
    """Extract tool result blocks from a message."""
    content = message_data.get("content", [])

    if not isinstance(content, list):
        return []

    results = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            result_content = block.get("content", "")
            if isinstance(result_content, list):
                # Flatten content array to string
                text_parts = []
                for item in result_content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        text_parts.append(item)
                result_content = "\n".join(text_parts)

            results.append({
                "tool_use_id": block.get("tool_use_id"),
                "content": result_content,
                "is_error": block.get("is_error", False),
            })

    return results


def extract_code_blocks(content: str) -> list[CodeBlock]:
    """Extract fenced code blocks from content."""
    pattern = r"```(\w*)\n(.*?)```"
    blocks = []

    for match in re.finditer(pattern, content, re.DOTALL):
        language = match.group(1) or "text"
        code = match.group(2).rstrip()
        # Approximate line number based on position
        line_num = content[:match.start()].count('\n') + 1
        blocks.append(CodeBlock(language=language, code=code, line_number=line_num))

    return blocks


def parse_session(jsonl_path: Path) -> Session:
    """Parse a JSONL session file into a Session object."""
    project_path = jsonl_path.parent
    session_id = jsonl_path.stem

    session = Session(
        session_id=session_id,
        project=get_project_name(project_path),
        slug=None,
        file_path=jsonl_path,
        file_mtime=jsonl_path.stat().st_mtime,
    )

    messages = []
    timestamps = []

    with open(jsonl_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Extract session-level metadata
            if "slug" in data and session.slug is None:
                session.slug = data["slug"]

            # Handle message entries
            # Format: {"type": "user"|"assistant", "message": {...}, "timestamp": "...", ...}
            entry_type = data.get("type")
            if entry_type in ("user", "assistant"):
                msg_data = data.get("message", {})
                role = msg_data.get("role", entry_type)  # Fall back to entry type

                msg = Message(
                    role=role,
                    content=extract_text_content(msg_data),
                    timestamp=data.get("timestamp"),
                    uuid=data.get("uuid"),
                    line_number=line_num,
                    tool_use=extract_tool_use(msg_data),
                    tool_results=extract_tool_results(msg_data),
                    thinking=extract_thinking(msg_data),
                )
                messages.append(msg)

                if msg.timestamp:
                    timestamps.append(msg.timestamp)

    session.messages = messages

    if timestamps:
        session.start_time = min(timestamps)
        session.end_time = max(timestamps)

    return session


def iter_sessions(projects_dir: Path) -> "Generator[Path, None, None]":
    """Iterate over all session JSONL files in the projects directory."""
    if not projects_dir.exists():
        return

    for project_dir in projects_dir.iterdir():
        if not project_dir.is_dir():
            continue

        for jsonl_file in project_dir.glob("*.jsonl"):
            yield jsonl_file


def get_projects_dir() -> Path:
    """Get the Claude projects directory."""
    return Path.home() / ".claude" / "projects"
