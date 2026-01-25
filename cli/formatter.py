"""Rich terminal output formatting."""

import json
import re
from datetime import datetime
from typing import Optional

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.syntax import Syntax

from core.parser import Session, CodeBlock, extract_code_blocks
from core.search import SearchResult, SessionInfo, ProjectInfo


console = Console()


def format_timestamp(ts: Optional[str]) -> str:
    """Format an ISO timestamp for display."""
    if not ts:
        return "N/A"
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return ts[:16] if len(ts) > 16 else ts


def format_date(ts: Optional[str]) -> str:
    """Format an ISO timestamp as just the date."""
    if not ts:
        return "N/A"
    try:
        dt = datetime.fromisoformat(ts.replace('Z', '+00:00'))
        return dt.strftime("%Y-%m-%d")
    except (ValueError, AttributeError):
        return ts[:10] if len(ts) > 10 else ts


def truncate(text: str, max_len: int = 80) -> str:
    """Truncate text to max length with ellipsis."""
    if not text:
        return ""
    text = text.replace('\n', ' ').strip()
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


def format_search_results(results: list[SearchResult]) -> None:
    """Print search results with highlighted snippets."""
    if not results:
        console.print("[yellow]No results found[/yellow]")
        return

    for result in results:
        # Format the header
        header = Text()
        header.append(f"[{result.project}]", style="cyan")
        header.append(f" {format_timestamp(result.timestamp)}", style="dim")
        header.append(f" session ", style="dim")
        header.append(result.session_id[:8], style="blue")
        console.print(header)

        # Format the snippet with highlights
        snippet = result.snippet
        # Convert FTS5 markers to rich markup
        snippet = snippet.replace(">>>", "[bold yellow]").replace("<<<", "[/bold yellow]")
        console.print(f"  Line {result.line_number}: {snippet}")
        console.print()

    console.print(f"[dim]Found {len(results)} matches[/dim]")


def format_projects_table(projects: list[ProjectInfo], stats: bool = False) -> None:
    """Print a table of projects."""
    if not projects:
        console.print("[yellow]No projects found[/yellow]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Project", style="cyan", no_wrap=True)
    table.add_column("Sessions", justify="right")
    table.add_column("Last Active", style="dim")

    if stats:
        table.add_column("Messages", justify="right")
        table.add_column("First Active", style="dim")

    for project in projects:
        row = [
            project.name,
            str(project.session_count),
            format_date(project.last_active),
        ]
        if stats:
            row.extend([
                str(project.message_count),
                format_date(project.first_active),
            ])
        table.add_row(*row)

    console.print(table)
    console.print(f"\n[dim]{len(projects)} projects[/dim]")


def format_sessions_table(sessions: list[SessionInfo], summary: bool = False) -> None:
    """Print a table of sessions."""
    if not sessions:
        console.print("[yellow]No sessions found[/yellow]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("Session ID", style="blue", no_wrap=True)
    table.add_column("Project", style="cyan")
    table.add_column("Date", style="dim")
    table.add_column("Messages", justify="right")

    if summary:
        table.add_column("First Message", max_width=50)

    for session in sessions:
        row = [
            session.session_id[:8],
            session.project,
            format_date(session.start_time),
            str(session.message_count),
        ]
        if summary:
            row.append(truncate(session.first_message or "", 50))
        table.add_row(*row)

    console.print(table)
    console.print(f"\n[dim]{len(sessions)} sessions[/dim]")


def format_transcript(
    session: Session,
    no_tools: bool = False,
    output_format: str = "text"
) -> str:
    """Format a session transcript.

    Args:
        session: Session to format
        no_tools: If True, exclude tool calls and results
        output_format: 'text', 'md', or 'json'

    Returns:
        Formatted string
    """
    if output_format == "json":
        return _format_transcript_json(session, no_tools)
    elif output_format == "md":
        return _format_transcript_markdown(session, no_tools)
    else:
        return _format_transcript_text(session, no_tools)


def _format_transcript_text(session: Session, no_tools: bool) -> str:
    """Format transcript as plain text."""
    lines = []
    lines.append(f"Session: {session.session_id}")
    lines.append(f"Project: {session.project}")
    lines.append(f"Date: {format_timestamp(session.start_time)}")
    lines.append(f"Messages: {session.message_count}")
    lines.append("=" * 60)
    lines.append("")

    for msg in session.messages:
        # Role header
        role_display = "USER" if msg.role == "user" else "ASSISTANT"
        lines.append(f"[{role_display}] {format_timestamp(msg.timestamp)}")
        lines.append("-" * 40)

        # Content
        if msg.content:
            lines.append(msg.content)

        # Tool calls (if not suppressed)
        if not no_tools and msg.tool_use:
            for tool in msg.tool_use:
                lines.append(f"\n<tool_use name=\"{tool['name']}\">")
                if tool.get("input"):
                    lines.append(json.dumps(tool["input"], indent=2))
                lines.append("</tool_use>")

        # Tool results (if not suppressed)
        if not no_tools and msg.tool_results:
            for result in msg.tool_results:
                status = "error" if result.get("is_error") else "success"
                lines.append(f"\n<tool_result status=\"{status}\">")
                content = result.get("content", "")
                if len(content) > 500:
                    lines.append(content[:500] + "...")
                else:
                    lines.append(content)
                lines.append("</tool_result>")

        lines.append("")

    return "\n".join(lines)


def _format_transcript_markdown(session: Session, no_tools: bool) -> str:
    """Format transcript as Markdown."""
    lines = []
    lines.append(f"# Session {session.session_id[:8]}")
    lines.append("")
    lines.append(f"- **Project:** {session.project}")
    lines.append(f"- **Date:** {format_timestamp(session.start_time)}")
    lines.append(f"- **Messages:** {session.message_count}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for msg in session.messages:
        # Role header
        if msg.role == "user":
            lines.append("## User")
        else:
            lines.append("## Assistant")

        lines.append("")

        # Content
        if msg.content:
            lines.append(msg.content)
            lines.append("")

        # Tool calls (if not suppressed)
        if not no_tools and msg.tool_use:
            for tool in msg.tool_use:
                lines.append(f"### Tool: {tool['name']}")
                lines.append("")
                lines.append("```json")
                lines.append(json.dumps(tool.get("input", {}), indent=2))
                lines.append("```")
                lines.append("")

        # Tool results (if not suppressed)
        if not no_tools and msg.tool_results:
            for result in msg.tool_results:
                status = "Error" if result.get("is_error") else "Result"
                lines.append(f"### Tool {status}")
                lines.append("")
                lines.append("```")
                content = result.get("content", "")
                if len(content) > 1000:
                    lines.append(content[:1000] + "\n...")
                else:
                    lines.append(content)
                lines.append("```")
                lines.append("")

        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def _format_transcript_json(session: Session, no_tools: bool) -> str:
    """Format transcript as JSON."""
    data = {
        "session_id": session.session_id,
        "project": session.project,
        "slug": session.slug,
        "start_time": session.start_time,
        "end_time": session.end_time,
        "message_count": session.message_count,
        "messages": []
    }

    for msg in session.messages:
        msg_data = {
            "role": msg.role,
            "content": msg.content,
            "timestamp": msg.timestamp,
            "line_number": msg.line_number,
        }

        if not no_tools:
            if msg.tool_use:
                msg_data["tool_use"] = msg.tool_use
            if msg.tool_results:
                msg_data["tool_results"] = msg.tool_results

        data["messages"].append(msg_data)

    return json.dumps(data, indent=2)


def print_transcript(session: Session, no_tools: bool = False) -> None:
    """Print a session transcript with rich formatting."""
    # Header panel
    header = f"[bold]Session:[/bold] {session.session_id}\n"
    header += f"[bold]Project:[/bold] {session.project}\n"
    header += f"[bold]Date:[/bold] {format_timestamp(session.start_time)}\n"
    header += f"[bold]Messages:[/bold] {session.message_count}"

    console.print(Panel(header, title="Session Info", border_style="blue"))
    console.print()

    for msg in session.messages:
        # Role styling
        if msg.role == "user":
            style = "green"
            role_display = "USER"
        else:
            style = "blue"
            role_display = "ASSISTANT"

        console.print(f"[bold {style}]{role_display}[/bold {style}] [dim]{format_timestamp(msg.timestamp)}[/dim]")

        # Content
        if msg.content:
            # Try to render as markdown for assistant messages
            if msg.role == "assistant":
                try:
                    console.print(Markdown(msg.content))
                except Exception:
                    console.print(msg.content)
            else:
                console.print(msg.content)

        # Tool calls (if not suppressed)
        if not no_tools and msg.tool_use:
            for tool in msg.tool_use:
                console.print(f"\n[yellow]Tool: {tool['name']}[/yellow]")
                if tool.get("input"):
                    input_str = json.dumps(tool["input"], indent=2)
                    if len(input_str) > 500:
                        input_str = input_str[:500] + "\n..."
                    console.print(Syntax(input_str, "json", theme="monokai"))

        # Tool results (if not suppressed)
        if not no_tools and msg.tool_results:
            for result in msg.tool_results:
                if result.get("is_error"):
                    console.print("\n[red]Tool Error:[/red]")
                else:
                    console.print("\n[dim]Tool Result:[/dim]")
                content = result.get("content", "")
                if len(content) > 500:
                    console.print(content[:500] + "...")
                else:
                    console.print(content)

        console.print()


def format_code_blocks(blocks: list[CodeBlock], language_filter: Optional[str] = None) -> None:
    """Print extracted code blocks."""
    filtered = blocks
    if language_filter:
        filtered = [b for b in blocks if b.language.lower() == language_filter.lower()]

    if not filtered:
        console.print("[yellow]No code blocks found[/yellow]")
        return

    for i, block in enumerate(filtered, 1):
        console.print(f"\n[bold]Code Block {i}[/bold] [dim]({block.language}, line {block.line_number})[/dim]")
        console.print(Syntax(block.code, block.language or "text", theme="monokai", line_numbers=True))

    console.print(f"\n[dim]{len(filtered)} code blocks[/dim]")


def format_stats(stats: dict) -> None:
    """Print index statistics."""
    if not stats.get("indexed"):
        console.print("[yellow]Index not found. Run 'claude-conversations reindex' first.[/yellow]")
        return

    console.print(Panel.fit(
        f"[bold]Total projects:[/bold] {stats['projects']}\n"
        f"[bold]Total sessions:[/bold] {stats['sessions']}\n"
        f"[bold]Total messages:[/bold] {stats['messages']}\n"
        f"[bold]Most active project:[/bold] {stats.get('most_active_project', 'N/A')} "
        f"({stats.get('most_active_count', 0)} sessions)\n"
        f"[bold]Date range:[/bold] {format_date(stats.get('earliest'))} to {format_date(stats.get('latest'))}",
        title="Claude Conversations Statistics",
        border_style="blue"
    ))


def format_extracted_files(session: Session) -> None:
    """Print files that were written in a session."""
    written_files = []

    for msg in session.messages:
        for tool in msg.tool_use:
            if tool["name"] in ("Write", "Edit"):
                file_path = tool.get("input", {}).get("file_path", "")
                if file_path:
                    written_files.append({
                        "path": file_path,
                        "tool": tool["name"],
                        "timestamp": msg.timestamp,
                    })

    if not written_files:
        console.print("[yellow]No files written in this session[/yellow]")
        return

    table = Table(show_header=True, header_style="bold")
    table.add_column("File", style="cyan")
    table.add_column("Action")
    table.add_column("Time", style="dim")

    for f in written_files:
        table.add_row(f["path"], f["tool"], format_timestamp(f["timestamp"]))

    console.print(table)
    console.print(f"\n[dim]{len(written_files)} files[/dim]")


def format_extracted_tools(session: Session, tool_filter: Optional[str] = None) -> None:
    """Print tool calls from a session."""
    tools = []

    for msg in session.messages:
        for tool in msg.tool_use:
            if tool_filter and tool["name"] != tool_filter:
                continue
            tools.append({
                "name": tool["name"],
                "input": tool.get("input", {}),
                "timestamp": msg.timestamp,
            })

    if not tools:
        console.print("[yellow]No matching tool calls found[/yellow]")
        return

    for i, tool in enumerate(tools, 1):
        console.print(f"\n[bold yellow]{i}. {tool['name']}[/bold yellow] [dim]{format_timestamp(tool['timestamp'])}[/dim]")
        if tool["input"]:
            input_str = json.dumps(tool["input"], indent=2)
            if len(input_str) > 1000:
                input_str = input_str[:1000] + "\n..."
            console.print(Syntax(input_str, "json", theme="monokai"))

    console.print(f"\n[dim]{len(tools)} tool calls[/dim]")
