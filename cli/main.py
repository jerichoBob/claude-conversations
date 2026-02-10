"""CLI commands for claude-conversations."""

import sys

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from core import index, search
from core.parser import extract_code_blocks, get_projects_dir
from . import formatter


console = Console()


@click.group()
@click.version_option(package_name="claude-conversations")
def cli():
    """Search, explore, and extract from Claude Code conversation history."""
    pass


@cli.command()
@click.argument("query")
@click.option("--project", "-p", help="Filter by project name (supports * wildcards)")
@click.option("--role", "-r", type=click.Choice(["user", "assistant"]),
              help="Filter by message role")
@click.option("--limit", "-n", default=50, help="Maximum results to return")
def search_cmd(query, project, role, limit):
    """Search across all conversations.

    QUERY is the search term (supports FTS5 syntax like quotes for exact phrases).

    Examples:
        claude-conversations search "webhook"
        claude-conversations search "authentication" --project "*webapp*"
        claude-conversations search "how do I" --role user
    """
    try:
        results = search.search(query, project=project, role=role, limit=limit)
        formatter.format_search_results(results)
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


# Alias 'search' command
cli.add_command(search_cmd, name="search")


@cli.command()
@click.option("--stats", is_flag=True, help="Show detailed statistics")
def projects(stats):
    """List all projects with session counts.

    Examples:
        claude-conversations projects
        claude-conversations projects --stats
    """
    try:
        project_list = search.get_projects()
        formatter.format_projects_table(project_list, stats=stats)
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("project", required=False)
@click.option("--summary", "-s", is_flag=True, help="Show first message as summary")
@click.option("--limit", "-n", default=100, help="Maximum sessions to return")
def sessions(project, summary, limit):
    """List sessions for a project.

    PROJECT is the project name filter (supports * wildcards).
    If not specified, shows all sessions.

    Examples:
        claude-conversations sessions
        claude-conversations sessions my-webapp* --summary
        claude-conversations sessions "*annotation*" -n 20
    """
    try:
        session_list = search.get_sessions(project=project, limit=limit)
        formatter.format_sessions_table(session_list, summary=summary)
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("session_id")
@click.option("--no-tools", is_flag=True, help="Exclude tool calls and results")
@click.option("--format", "output_format", type=click.Choice(["text", "md", "json"]),
              default="text", help="Output format")
def read(session_id, no_tools, output_format):
    """Read a session transcript.

    SESSION_ID can be a full UUID or a prefix (first 8 characters).

    Examples:
        claude-conversations read abc12345
        claude-conversations read abc12345 --no-tools
        claude-conversations read abc12345 --format md > session.md
        claude-conversations read abc12345 | less
    """
    try:
        session = search.load_session(session_id)

        # If outputting to a pipe/file, use plain text format
        if not sys.stdout.isatty() or output_format != "text":
            output = formatter.format_transcript(session, no_tools=no_tools, output_format=output_format)
            print(output)
        else:
            formatter.print_transcript(session, no_tools=no_tools)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("session_id")
@click.option("--code", is_flag=True, help="Extract code blocks")
@click.option("--lang", "code_lang", metavar="LANG", help="Filter code blocks by language")
@click.option("--files", is_flag=True, help="Extract files that were written")
@click.option("--tools", "tool_name", metavar="NAME", help="Extract tool calls by name")
def extract(session_id, code, code_lang, files, tool_name):
    """Extract specific content from a session.

    SESSION_ID can be a full UUID or a prefix (first 8 characters).

    Examples:
        claude-conversations extract abc12345 --code
        claude-conversations extract abc12345 --code --lang python
        claude-conversations extract abc12345 --files
        claude-conversations extract abc12345 --tools Write
    """
    try:
        session = search.load_session(session_id)

        if code or code_lang:
            # Extract code blocks
            all_code = []
            for msg in session.messages:
                if msg.content:
                    blocks = extract_code_blocks(msg.content)
                    all_code.extend(blocks)

            formatter.format_code_blocks(all_code, language_filter=code_lang)

        elif files:
            formatter.format_extracted_files(session)

        elif tool_name:
            formatter.format_extracted_tools(session, tool_filter=tool_name)

        else:
            console.print("[yellow]Specify what to extract: --code, --files, or --tools[/yellow]")
            sys.exit(1)

    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.argument("session_id", required=False)
@click.option("--project", "-p", help="Analyze a project (supports * wildcards)")
def analyze(session_id, project):
    """Analyze a session or project for patterns and statistics.

    Provides tool usage statistics, file operations, and summary data
    to support interactive exploration with the c3po skill.

    SESSION_ID can be a full UUID or a prefix (first 8 characters).

    Examples:
        claude-conversations analyze abc12345
        claude-conversations analyze --project "*webapp*"
        claude-conversations analyze --project cvxr-card-flow
    """
    try:
        if session_id:
            _analyze_session(session_id)
        elif project:
            _analyze_project(project)
        else:
            console.print("[yellow]Specify a session ID or --project flag[/yellow]")
            sys.exit(1)
    except ValueError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


def _analyze_session(session_id: str) -> None:
    """Analyze a single session."""
    session = search.load_session(session_id)

    # Gather statistics
    tool_counts = {}
    files_written = []
    files_read = []
    commands_run = []

    for msg in session.messages:
        for tool in msg.tool_use:
            name = tool["name"]
            tool_counts[name] = tool_counts.get(name, 0) + 1

            # Track specific tool details
            input_data = tool.get("input", {})
            if name == "Write":
                files_written.append(input_data.get("file_path", "unknown"))
            elif name == "Edit":
                files_written.append(input_data.get("file_path", "unknown"))
            elif name == "Read":
                files_read.append(input_data.get("file_path", "unknown"))
            elif name == "Bash":
                cmd = input_data.get("command", "")
                if cmd:
                    commands_run.append(cmd[:80])

    # Print header
    console.print(f"\n[bold cyan]Session Analysis: {session.session_id[:8]}[/bold cyan]")
    console.print(f"[dim]Project:[/dim] {session.project}")
    console.print(f"[dim]Messages:[/dim] {session.message_count}")
    console.print(f"[dim]Date:[/dim] {formatter.format_timestamp(session.start_time)}")

    # Tool usage table
    if tool_counts:
        console.print("\n[bold]Tool Usage:[/bold]")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Tool", style="yellow")
        table.add_column("Count", justify="right")

        for name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
            table.add_row(name, str(count))

        console.print(table)

    # Files modified
    if files_written:
        console.print("\n[bold]Files Modified:[/bold]")
        unique_files = list(dict.fromkeys(files_written))  # Preserve order, remove dupes
        for f in unique_files[:10]:
            console.print(f"  [cyan]{f}[/cyan]")
        if len(unique_files) > 10:
            console.print(f"  [dim]... and {len(unique_files) - 10} more[/dim]")

    # Commands run
    if commands_run:
        console.print("\n[bold]Commands Run:[/bold]")
        unique_cmds = list(dict.fromkeys(commands_run))[:10]
        for cmd in unique_cmds:
            console.print(f"  [green]{cmd}[/green]")
        if len(commands_run) > 10:
            console.print(f"  [dim]... and {len(commands_run) - 10} more[/dim]")

    console.print()


def _analyze_project(project_filter: str) -> None:
    """Analyze all sessions in a project."""
    sessions = search.get_sessions(project=project_filter, limit=1000)

    if not sessions:
        console.print(f"[yellow]No sessions found matching '{project_filter}'[/yellow]")
        return

    # Aggregate statistics across all sessions
    total_messages = 0
    tool_counts = {}
    all_files_written = []

    for session_info in sessions:
        total_messages += session_info.message_count

        # Load each session for detailed analysis
        try:
            session = search.load_session(session_info.session_id)
            for msg in session.messages:
                for tool in msg.tool_use:
                    name = tool["name"]
                    tool_counts[name] = tool_counts.get(name, 0) + 1

                    if name in ("Write", "Edit"):
                        fp = tool.get("input", {}).get("file_path", "")
                        if fp:
                            all_files_written.append(fp)
        except (ValueError, RuntimeError):
            continue  # Skip sessions that can't be loaded

    # Get project name from first session
    project_name = sessions[0].project if sessions else project_filter

    # Print header
    console.print(f"\n[bold cyan]Project Analysis: {project_name}[/bold cyan]")
    console.print(f"[dim]Sessions:[/dim] {len(sessions)}")
    console.print(f"[dim]Total Messages:[/dim] {total_messages}")

    if sessions:
        console.print(f"[dim]Date Range:[/dim] {formatter.format_date(sessions[-1].start_time)} to {formatter.format_date(sessions[0].start_time)}")

    # Tool usage summary
    if tool_counts:
        console.print("\n[bold]Tool Usage Summary:[/bold]")
        table = Table(show_header=True, header_style="bold")
        table.add_column("Tool", style="yellow")
        table.add_column("Total Uses", justify="right")
        table.add_column("Avg per Session", justify="right")

        for name, count in sorted(tool_counts.items(), key=lambda x: -x[1])[:15]:
            avg = count / len(sessions) if sessions else 0
            table.add_row(name, str(count), f"{avg:.1f}")

        console.print(table)

    # Most modified files
    if all_files_written:
        console.print("\n[bold]Most Modified Files:[/bold]")
        file_counts = {}
        for f in all_files_written:
            file_counts[f] = file_counts.get(f, 0) + 1

        for f, count in sorted(file_counts.items(), key=lambda x: -x[1])[:10]:
            console.print(f"  [cyan]{f}[/cyan] ({count}x)")

    # Recent sessions preview
    console.print("\n[bold]Recent Sessions:[/bold]")
    for session_info in sessions[:5]:
        summary = formatter.truncate(session_info.first_message or "", 50)
        console.print(f"  [{session_info.session_id[:8]}] {formatter.format_date(session_info.start_time)} - {summary}")

    console.print()


@cli.command()
@click.option("-n", "count", default=10, help="Number of sessions to show")
@click.option("--summary", "-s", is_flag=True, help="Show first message as summary")
def recent(count, summary):
    """Show recent sessions across all projects.

    Examples:
        claude-conversations recent
        claude-conversations recent -n 20 --summary
    """
    try:
        session_list = search.get_recent(n=count)
        formatter.format_sessions_table(session_list, summary=summary)
    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.option("--project", "-p", help="Filter projects by pattern (glob, ~regex, or substring)")
def tui(project):
    """Launch interactive TUI for browsing conversations.

    Provides an interactive terminal interface with:
    - Arrow key navigation through projects and sessions
    - Preview pane showing session summaries
    - Search functionality (press / to focus search)
    - Keyboard shortcuts: q (quit), Tab (switch pane), Esc (back)

    Project filter supports:
    - Glob patterns: "BUILT-*", "*webapp*"
    - Regex (prefix with ~): "~^BUILT-git-repos"
    - Substring match: "webapp"

    Examples:
        claude-conversations tui
        claude-conversations tui -p "BUILT-*"
        claude-conversations tui --project "~^BUILT-git-repos"
    """
    from .tui import ConversationBrowser
    app = ConversationBrowser(project_filter=project)
    app.run()


@cli.command()
def stats():
    """Show usage statistics.

    Examples:
        claude-conversations stats
    """
    stats_data = index.get_stats()
    formatter.format_stats(stats_data)


@cli.command("rag-analyze")
@click.argument("query", required=False)
@click.option("--projects", "-p", help="Comma-separated project filters")
@click.option("--list", "list_analyses", is_flag=True, help="List saved analyses")
@click.option("--show", help="Show saved analysis by ID")
@click.option("--model", default="claude-sonnet-4-20250514", help="Model to use for analysis")
def rag_analyze(query, projects, list_analyses, show, model):
    """AI-powered analysis of conversation history.

    Uses a multi-agent system to search, analyze patterns, and compare
    conversations across your Claude Code history.

    QUERY is your analysis question (e.g., "How did I implement auth?").

    Examples:
        claude-conversations rag-analyze "Compare risk-analysis flow"
        claude-conversations rag-analyze "How did I implement auth?" -p "*webapp*"
        claude-conversations rag-analyze --list
        claude-conversations rag-analyze --show abc12345
    """
    from core import persistence
    from rich.markdown import Markdown

    # List saved analyses
    if list_analyses:
        analyses = persistence.list_analyses()
        if not analyses:
            console.print("[yellow]No saved analyses found.[/yellow]")
            return

        table = Table(show_header=True, header_style="bold")
        table.add_column("ID", style="cyan")
        table.add_column("Query")
        table.add_column("Projects")
        table.add_column("Date")

        for analysis in analyses:
            date = analysis.created_at[:10] if analysis.created_at else "N/A"
            projects_str = ", ".join(analysis.projects[:3]) if analysis.projects else "-"
            if len(analysis.projects) > 3:
                projects_str += f" (+{len(analysis.projects) - 3})"
            table.add_row(
                analysis.id[:8],
                formatter.truncate(analysis.query, 40),
                projects_str,
                date,
            )

        console.print(table)
        return

    # Show specific analysis
    if show:
        analysis = persistence.load_analysis(show)
        if not analysis:
            console.print(f"[red]Analysis not found:[/red] {show}")
            sys.exit(1)

        console.print(f"\n[bold cyan]Analysis: {analysis.id[:8]}[/bold cyan]")
        console.print(f"[dim]Query:[/dim] {analysis.query}")
        console.print(f"[dim]Date:[/dim] {analysis.created_at}")
        console.print(f"[dim]Projects:[/dim] {', '.join(analysis.projects) or 'all'}")
        console.print(f"[dim]Sessions:[/dim] {len(analysis.sessions)}")
        console.print()
        console.print(Markdown(analysis.result))
        return

    # Run new analysis
    if not query:
        console.print("[yellow]Provide a query or use --list/--show[/yellow]")
        sys.exit(1)

    try:
        from core.agents import run_analysis
        from rich.live import Live
        from rich.panel import Panel

        # Create a live display for progress
        progress_lines = []
        current_stage = ""

        def progress_callback(stage: str, detail: str):
            nonlocal current_stage, progress_lines
            # Add stage header if new stage
            stage_icons = {
                "starting": "[cyan]>[/cyan]",
                "decomposing": "[yellow]?[/yellow]",
                "searching": "[blue]@[/blue]",
                "chunking": "[magenta]#[/magenta]",
                "analyzing": "[green]*[/green]",
                "comparing": "[cyan]=[/cyan]",
                "complete": "[green]![/green]",
            }
            icon = stage_icons.get(stage, "[dim].[/dim]")
            progress_lines.append(f"{icon} {detail}")
            # Keep last 15 lines
            if len(progress_lines) > 15:
                progress_lines = progress_lines[-15:]

        console.print(f"\n[bold cyan]RAG Analysis[/bold cyan]: {query}")
        if projects:
            console.print(f"[dim]Projects: {projects}[/dim]")
        console.print()

        with Live(Panel("Starting analysis...", title="Progress"), console=console, refresh_per_second=4) as live:
            def update_display():
                content = "\n".join(progress_lines) if progress_lines else "Initializing..."
                live.update(Panel(content, title="Progress", border_style="blue"))

            # Wrap progress callback to update display
            def live_progress(stage: str, detail: str):
                progress_callback(stage, detail)
                update_display()

            result, session_ids, agents_log = run_analysis(
                query=query,
                project_filter=projects,
                model=model,
                progress=live_progress,
            )

        if not result:
            console.print("[yellow]No analysis results generated.[/yellow]")
            return

        # Extract project names from sessions
        analyzed_projects = list(set(
            s.project for s in [
                search.get_session_by_id(sid) for sid in session_ids
            ] if s
        )) if session_ids else (projects.split(",") if projects else [])

        # Save analysis
        analysis_result = persistence.AnalysisResult.create(
            query=query,
            projects=analyzed_projects,
            sessions=session_ids,
            result=result,
            agents_log=agents_log,
        )
        persistence.save_analysis(analysis_result)

        # Display results
        console.print()
        console.print(Markdown(result))
        console.print()
        console.print(f"[dim]Analysis saved: {analysis_result.id[:8]}[/dim]")
        console.print(f"[dim]Sessions analyzed: {len(session_ids)}[/dim]")

    except RuntimeError as e:
        console.print(f"[red]Error:[/red] {e}")
        sys.exit(1)


@cli.command()
@click.option("--force", "-f", is_flag=True, help="Force full reindex")
def reindex(force):
    """Build or update the search index.

    This scans all conversation files in ~/.claude/projects/ and builds
    a SQLite FTS5 index for fast searching.

    Examples:
        claude-conversations reindex
        claude-conversations reindex --force
    """
    projects_dir = get_projects_dir()

    if not projects_dir.exists():
        console.print(f"[red]Error:[/red] Projects directory not found: {projects_dir}")
        sys.exit(1)

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Indexing conversations...", total=None)

        def progress_callback(current, total, session_id):
            progress.update(task, description=f"Indexing {current}/{total}: {session_id[:8]}...")

        indexed, skipped = index.build_index(
            projects_dir=projects_dir,
            force=force,
            progress_callback=progress_callback
        )

    console.print()
    console.print(f"[green]Indexed:[/green] {indexed} sessions")
    console.print(f"[dim]Skipped (unchanged):[/dim] {skipped} sessions")
    console.print(f"[dim]Index location:[/dim] {index.get_db_path()}")


if __name__ == "__main__":
    cli()
