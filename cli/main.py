"""CLI commands for claude-conversations."""

import sys

import click
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn

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
def stats():
    """Show usage statistics.

    Examples:
        claude-conversations stats
    """
    stats_data = index.get_stats()
    formatter.format_stats(stats_data)


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
