"""Textual TUI for browsing Claude conversations."""

import fnmatch
import re
from datetime import datetime
from enum import Enum, auto
from typing import Optional

from rich.text import Text

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message as TextualMessage
from textual.screen import ModalScreen
from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static

from core import index, search
from core.parser import Message, Session, get_projects_dir
from core.search import ProjectInfo, SessionInfo


class ViewState(Enum):
    """Current view state in the navigation hierarchy."""
    PROJECTS = auto()
    SESSIONS = auto()
    MESSAGES = auto()


def format_date(ts: Optional[str]) -> str:
    """Format an ISO timestamp as just the date."""
    if not ts:
        return "N/A"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%b %d")
    except (ValueError, AttributeError):
        return ts[:10] if len(ts) > 10 else ts


def format_timestamp(ts: Optional[str]) -> str:
    """Format an ISO timestamp for display."""
    if not ts:
        return "N/A"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except (ValueError, AttributeError):
        return ts[:16] if len(ts) > 16 else ts


def truncate(text: str, max_len: int = 80) -> str:
    """Truncate text to max length with ellipsis."""
    if not text:
        return ""
    text = text.replace("\n", " ").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def matches_filter(name: str, pattern: str) -> bool:
    """Check if name matches the filter pattern.

    Supports:
    - Glob patterns with * (e.g., "BUILT-*", "*webapp*")
    - Regex patterns starting with ~ (e.g., "~^BUILT-git-repos")
    - Plain substring match otherwise
    """
    if not pattern:
        return True

    if pattern.startswith("~"):
        # Regex pattern
        try:
            return bool(re.search(pattern[1:], name))
        except re.error:
            return False
    elif "*" in pattern or "?" in pattern:
        # Glob pattern
        return fnmatch.fnmatch(name, pattern)
    else:
        # Substring match
        return pattern.lower() in name.lower()


class AnalysisInputScreen(ModalScreen[str]):
    """Modal screen for entering RAG analysis query."""

    CSS = """
    AnalysisInputScreen {
        align: center middle;
    }

    #analysis-dialog {
        width: 70;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #analysis-dialog Label {
        width: 100%;
        margin-bottom: 1;
    }

    #analysis-query {
        width: 100%;
        margin-bottom: 1;
    }

    #analysis-buttons {
        width: 100%;
        height: 3;
        align: center middle;
    }

    #analysis-buttons Button {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="analysis-dialog"):
            yield Label("RAG Analysis Query")
            yield Input(
                placeholder="e.g., Compare auth implementations across projects",
                id="analysis-query",
            )
            yield Label("Enter a question about your conversation history", classes="dim")
            with Horizontal(id="analysis-buttons"):
                yield Button("Analyze", variant="primary", id="analyze-btn")
                yield Button("Cancel", variant="default", id="cancel-btn")

    def on_mount(self) -> None:
        self.query_one("#analysis-query", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "analyze-btn":
            query = self.query_one("#analysis-query", Input).value.strip()
            if query:
                self.dismiss(query)
            else:
                self.notify("Please enter a query", severity="warning")
        else:
            self.dismiss("")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        query = event.value.strip()
        if query:
            self.dismiss(query)

    def action_cancel(self) -> None:
        self.dismiss("")


class AnalysisProgressScreen(ModalScreen):
    """Modal screen showing analysis progress."""

    CSS = """
    AnalysisProgressScreen {
        align: center middle;
    }

    #progress-dialog {
        width: 80;
        height: 20;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #progress-title {
        text-style: bold;
        margin-bottom: 1;
    }

    #progress-log {
        height: 1fr;
        border: solid $primary;
        padding: 0 1;
        overflow-y: auto;
    }

    #progress-status {
        height: 1;
        margin-top: 1;
        text-style: italic;
        color: $text-muted;
    }
    """

    def __init__(self, query: str, project_filter: str = None) -> None:
        super().__init__()
        self._query = query
        self._project_filter = project_filter
        self._progress_lines: list[str] = []
        self._result: str = ""
        self._session_ids: list[str] = []
        self._analysis_id: str = ""
        self._error: str = ""

    def compose(self) -> ComposeResult:
        with Vertical(id="progress-dialog"):
            yield Label(f"Analyzing: {truncate(self._query, 60)}", id="progress-title")
            with VerticalScroll(id="progress-log"):
                yield Static("Starting analysis...", id="progress-content")
            yield Label("Initializing...", id="progress-status")

    def on_mount(self) -> None:
        self.run_worker(self._run_analysis, exclusive=True)

    def _update_progress(self, stage: str, detail: str) -> None:
        """Update progress display (called from worker thread)."""
        stage_icons = {
            "starting": ">",
            "decomposing": "?",
            "searching": "@",
            "chunking": "#",
            "analyzing": "*",
            "comparing": "=",
            "complete": "!",
        }
        icon = stage_icons.get(stage, ".")
        self._progress_lines.append(f"[{icon}] {detail}")
        # Keep last 20 lines
        if len(self._progress_lines) > 20:
            self._progress_lines = self._progress_lines[-20:]

        # Update UI from main thread
        self.call_from_thread(self._refresh_progress, stage, detail)

    def _refresh_progress(self, stage: str, detail: str) -> None:
        """Refresh the progress display (main thread)."""
        try:
            content = self.query_one("#progress-content", Static)
            status = self.query_one("#progress-status", Label)
            content.update("\n".join(self._progress_lines))
            status.update(detail)
        except Exception:
            pass

    async def _run_analysis(self) -> None:
        """Worker to run the analysis."""
        try:
            from core.agents import run_analysis
            from core import persistence

            result, session_ids, agents_log = run_analysis(
                query=self._query,
                project_filter=self._project_filter,
                progress=self._update_progress,
            )

            self._result = result
            self._session_ids = session_ids

            if result:
                # Get project names from sessions
                analyzed_projects = []
                for sid in session_ids:
                    try:
                        info = search.get_session_by_id(sid)
                        if info and info.project not in analyzed_projects:
                            analyzed_projects.append(info.project)
                    except Exception:
                        pass

                # Save analysis
                analysis_result = persistence.AnalysisResult.create(
                    query=self._query,
                    projects=analyzed_projects,
                    sessions=session_ids,
                    result=result,
                    agents_log=agents_log,
                )
                persistence.save_analysis(analysis_result)
                self._analysis_id = analysis_result.id

            # Transition to results
            self.call_from_thread(self._show_results)

        except Exception as e:
            self._error = str(e)
            self.call_from_thread(self._show_error)

    def _show_results(self) -> None:
        """Show results screen."""
        if self._result:
            self.app.pop_screen()
            self.app.push_screen(
                AnalysisResultScreen(self._query, self._result, self._analysis_id)
            )
        else:
            self.app.pop_screen()
            self.app.notify("No results found", severity="warning")

    def _show_error(self) -> None:
        """Show error and close."""
        self.app.pop_screen()
        self.app.notify(f"Analysis error: {self._error}", severity="error")


class AnalysisResultScreen(ModalScreen):
    """Modal screen for displaying RAG analysis results."""

    CSS = """
    AnalysisResultScreen {
        align: center middle;
    }

    #result-dialog {
        width: 90%;
        height: 90%;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }

    #result-header {
        height: 3;
        width: 100%;
        margin-bottom: 1;
    }

    #result-header Label {
        width: 1fr;
    }

    #result-content {
        height: 1fr;
        width: 100%;
        border: solid $primary;
        padding: 1;
        overflow-y: auto;
    }

    #result-footer {
        height: 3;
        width: 100%;
        align: center middle;
        margin-top: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "close", "Close"),
        Binding("q", "close", "Close"),
    ]

    def __init__(self, query: str, result: str, analysis_id: str) -> None:
        super().__init__()
        self._query = query
        self._result = result
        self._analysis_id = analysis_id

    def compose(self) -> ComposeResult:
        with Vertical(id="result-dialog"):
            with Horizontal(id="result-header"):
                yield Label(f"Analysis: {truncate(self._query, 50)}")
                yield Label(f"ID: {self._analysis_id[:8]}", classes="dim")
            with VerticalScroll(id="result-content"):
                yield Static(self._result, id="result-text")
            with Horizontal(id="result-footer"):
                yield Button("Close", variant="primary", id="close-btn")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.dismiss()

    def action_close(self) -> None:
        self.dismiss()


class ProjectItem(ListItem):
    """A project item in the projects list."""

    def __init__(self, project: ProjectInfo) -> None:
        super().__init__()
        self.project = project

    def compose(self) -> ComposeResult:
        yield Label(f"{self.project.name} ({self.project.session_count})")


class SessionItem(ListItem):
    """A session item in the sessions list."""

    def __init__(self, session: SessionInfo, max_width: int = 60) -> None:
        super().__init__()
        self.session = session
        self._max_width = max_width

    def compose(self) -> ComposeResult:
        date = format_date(self.session.start_time)
        # Build with Rich Text, let CSS handle overflow
        text = Text()
        text.append(f"{self.session.session_id[:8]}  {date}  {self.session.message_count:>3}  ")
        summary = (self.session.first_message or "").replace("\n", " ").strip()
        text.append(summary)
        yield Label(text)


class MessageItem(ListItem):
    """A message item in the messages list."""

    def __init__(self, message: Message, index: int, max_width: int = 60) -> None:
        super().__init__()
        self.message = message
        self.index = index
        self._max_width = max_width

    def compose(self) -> ComposeResult:
        # Build text with Rich Text for proper styling
        text = Text()
        text.append(f"{self.index:>3}. ")

        # Color-coded roles
        if self.message.role == "user":
            text.append("USER", style="cyan")
        else:
            text.append("ASST", style="green")

        # Tool count
        tool_count = len(self.message.tool_use)
        if tool_count:
            text.append(f" [{tool_count} tools]", style="dim")

        text.append("  ")

        # Content - just clean it up, let CSS handle overflow
        content = (self.message.content or "").replace("\n", " ").strip()
        text.append(content)

        yield Label(text)


class SearchResultItem(ListItem):
    """A search result item."""

    def __init__(self, result: search.SearchResult, max_width: int = 60) -> None:
        super().__init__()
        self.result = result
        self._max_width = max_width

    def compose(self) -> ComposeResult:
        snippet = self.result.snippet.replace(">>>", "").replace("<<<", "")
        snippet = snippet.replace("\n", " ").strip()
        # Build with Rich Text, let CSS handle overflow
        text = Text()
        text.append(f"[{self.result.project}] ")
        text.append(snippet)
        yield Label(text)


class ProjectsPane(ListView):
    """Pane showing all projects."""

    class ProjectHighlighted(TextualMessage):
        """Sent when a project is highlighted."""

        def __init__(self, project: ProjectInfo) -> None:
            super().__init__()
            self.project = project

    class ProjectSelected(TextualMessage):
        """Sent when a project is selected (Enter)."""

        def __init__(self, project: ProjectInfo) -> None:
            super().__init__()
            self.project = project

    def __init__(self, project_filter: Optional[str] = None) -> None:
        super().__init__(id="projects-pane")
        self._projects: list[ProjectInfo] = []
        self._project_filter = project_filter

    def on_mount(self) -> None:
        self.load_projects()

    def load_projects(self) -> None:
        """Load projects from the search index."""
        try:
            all_projects = search.get_projects()
            # Apply filter if specified
            if self._project_filter:
                self._projects = [
                    p for p in all_projects
                    if matches_filter(p.name, self._project_filter)
                ]
            else:
                self._projects = all_projects

            self.clear()
            for project in self._projects:
                self.append(ProjectItem(project))

            # Update title to show filter
            if self._project_filter:
                self.border_title = f"Projects ({self._project_filter})"
        except RuntimeError:
            self.clear()
            self.append(ListItem(Label("Index not found. Run: claude-conversations reindex")))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item and isinstance(event.item, ProjectItem):
            self.post_message(self.ProjectHighlighted(event.item.project))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item and isinstance(event.item, ProjectItem):
            self.post_message(self.ProjectSelected(event.item.project))


class ContentPane(ListView):
    """Pane showing sessions or messages depending on view state."""

    class SessionHighlighted(TextualMessage):
        """Sent when a session is highlighted."""

        def __init__(self, session: SessionInfo) -> None:
            super().__init__()
            self.session = session

    class SessionSelected(TextualMessage):
        """Sent when a session is selected (Enter)."""

        def __init__(self, session: SessionInfo) -> None:
            super().__init__()
            self.session = session

    class MessageHighlighted(TextualMessage):
        """Sent when a message is highlighted."""

        def __init__(self, message: Message, session: Session) -> None:
            super().__init__()
            self.message = message
            self.session = session

    class MessageSelected(TextualMessage):
        """Sent when a message is selected (Enter)."""

        def __init__(self, message: Message, session: Session) -> None:
            super().__init__()
            self.message = message
            self.session = session

    def __init__(self) -> None:
        super().__init__(id="content-pane")
        self._sessions: list[SessionInfo] = []
        self._current_project: Optional[str] = None
        self._current_session: Optional[Session] = None
        self._view_state = ViewState.SESSIONS
        self._last_width: int = 0
        self._search_results: list[search.SearchResult] = []

    @property
    def view_state(self) -> ViewState:
        return self._view_state

    def _get_content_width(self) -> int:
        """Get the available width for content, accounting for borders and padding."""
        # Try scrollable_content_region first (actual viewport width)
        try:
            region_width = self.scrollable_content_region.width
            if region_width > 10:
                # This is the actual usable width, no subtraction needed
                return max(20, region_width)
        except Exception:
            pass

        # Fallback: size.width - border(2) - scrollbar(2) = 4
        # Item padding is handled by CSS, not our text length
        if self.size.width > 10:
            return max(20, self.size.width - 4)
        return 60

    def on_resize(self, event) -> None:
        """Rebuild list items when pane is resized."""
        new_width = self._get_content_width()
        # Only rebuild if width changed significantly (more than 5 chars)
        if abs(new_width - self._last_width) > 5:
            self._last_width = new_width
            self._rebuild_items()

    def _rebuild_items(self) -> None:
        """Rebuild current list items with new width."""
        width = self._get_content_width()
        # Remember current index
        current_index = self.index

        if self._view_state == ViewState.MESSAGES and self._current_session:
            self.clear()
            for i, msg in enumerate(self._current_session.messages, 1):
                self.append(MessageItem(msg, i, max_width=width))
        elif self._search_results:
            self.clear()
            for result in self._search_results:
                self.append(SearchResultItem(result, max_width=width))
        elif self._sessions:
            self.clear()
            for session in self._sessions:
                self.append(SessionItem(session, max_width=width))

        # Restore selection if possible
        if current_index is not None and current_index < len(self.children):
            self.index = current_index

    def load_sessions(self, project: str) -> None:
        """Load sessions for a project."""
        if project == self._current_project and self._view_state == ViewState.SESSIONS:
            return
        self._current_project = project
        self._current_session = None
        self._search_results = []
        self._view_state = ViewState.SESSIONS
        try:
            self._sessions = search.get_sessions(project=project, limit=200)
            self.clear()
            width = self._get_content_width()
            for session in self._sessions:
                self.append(SessionItem(session, max_width=width))
            self.border_title = f"Sessions ({project})"
        except RuntimeError:
            self.clear()

    def load_messages(self, session_info: SessionInfo) -> None:
        """Load messages for a session."""
        self._view_state = ViewState.MESSAGES
        try:
            self._current_session = search.load_session(session_info.session_id)
            self.clear()
            width = self._get_content_width()
            for i, msg in enumerate(self._current_session.messages, 1):
                self.append(MessageItem(msg, i, max_width=width))
            self.border_title = f"Messages ({session_info.session_id[:8]}) - {len(self._current_session.messages)} msgs"
        except (RuntimeError, ValueError) as e:
            self.clear()
            self.append(ListItem(Label(f"Error loading session: {e}")))

    def go_back_to_sessions(self) -> bool:
        """Go back to sessions view. Returns True if we were in messages view."""
        if self._view_state == ViewState.MESSAGES and self._current_project:
            self._view_state = ViewState.SESSIONS
            self._current_session = None
            self.clear()
            width = self._get_content_width()
            for session in self._sessions:
                self.append(SessionItem(session, max_width=width))
            self.border_title = f"Sessions ({self._current_project})"
            return True
        return False

    def load_search_results(self, results: list[search.SearchResult]) -> None:
        """Load search results instead of sessions."""
        self._current_project = None
        self._current_session = None
        self._sessions = []
        self._search_results = results
        self._view_state = ViewState.SESSIONS
        self.clear()
        width = self._get_content_width()
        for result in results:
            self.append(SearchResultItem(result, max_width=width))
        self.border_title = f"Search Results ({len(results)})"

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item and isinstance(event.item, SessionItem):
            self.post_message(self.SessionHighlighted(event.item.session))
        elif event.item and isinstance(event.item, MessageItem):
            if self._current_session:
                self.post_message(self.MessageHighlighted(event.item.message, self._current_session))
        elif event.item and isinstance(event.item, SearchResultItem):
            try:
                session_info = search.get_session_by_id(event.item.result.session_id)
                if session_info:
                    self.post_message(self.SessionHighlighted(session_info))
            except (RuntimeError, ValueError):
                pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item and isinstance(event.item, SessionItem):
            self.post_message(self.SessionSelected(event.item.session))
        elif event.item and isinstance(event.item, MessageItem):
            if self._current_session:
                self.post_message(self.MessageSelected(event.item.message, self._current_session))
        elif event.item and isinstance(event.item, SearchResultItem):
            try:
                session_info = search.get_session_by_id(event.item.result.session_id)
                if session_info:
                    self.post_message(self.SessionSelected(session_info))
            except (RuntimeError, ValueError):
                pass


class PreviewPane(VerticalScroll):
    """Pane showing preview of selected item with scrolling."""

    def __init__(self) -> None:
        super().__init__(id="preview-pane")
        self._content = Static("Select an item to preview", id="preview-content")

    def compose(self) -> ComposeResult:
        yield self._content

    def show_session(self, session: SessionInfo) -> None:
        """Show session preview."""
        # Build tool usage summary
        tool_summary = ""
        try:
            full_session = search.load_session(session.session_id)
            tool_counts: dict[str, int] = {}
            for msg in full_session.messages:
                for tool in msg.tool_use:
                    name = tool["name"]
                    tool_counts[name] = tool_counts.get(name, 0) + 1
            if tool_counts:
                top_tools = sorted(tool_counts.items(), key=lambda x: -x[1])[:4]
                tool_summary = " | Tools: " + ", ".join(
                    f"{name}({count})" for name, count in top_tools
                )
        except (RuntimeError, ValueError):
            pass

        preview_text = f"""Session {session.session_id[:8]} - {session.project} - {format_timestamp(session.start_time)}
Messages: {session.message_count}{tool_summary}

First message: "{truncate(session.first_message or '', 80)}"

Press Enter to view messages"""
        self._content.update(preview_text)

    def show_message(self, message: Message, session: Session) -> None:
        """Show message preview with full content."""
        role = "USER" if message.role == "user" else "ASSISTANT"

        lines = []
        lines.append(f"[{role}] {format_timestamp(message.timestamp)}")
        lines.append(f"Session: {session.session_id[:8]} - {session.project}")
        lines.append("-" * 60)

        # Show content
        if message.content:
            # Limit content length for preview
            content = message.content
            if len(content) > 3000:
                content = content[:3000] + "\n\n... (truncated, press Enter to copy full message)"
            lines.append(content)

        # Show tool usage summary
        if message.tool_use:
            lines.append("")
            lines.append(f"--- Tool Calls ({len(message.tool_use)}) ---")
            for tool in message.tool_use[:5]:  # Show first 5 tools
                tool_name = tool.get("name", "unknown")
                lines.append(f"  - {tool_name}")
            if len(message.tool_use) > 5:
                lines.append(f"  ... and {len(message.tool_use) - 5} more")

        self._content.update("\n".join(lines))

    def clear_preview(self) -> None:
        """Clear the preview."""
        self._content.update("Select an item to preview")


class ConversationBrowser(App):
    """Main TUI application for browsing Claude conversations."""

    TITLE = "Claude Conversations"

    CSS = """
    #main {
        height: 1fr;
    }

    #projects-pane {
        width: 30%;
        border: solid $primary;
        border-title-color: $primary;
    }

    #content-pane {
        width: 1fr;
        border: solid $secondary;
        border-title-color: $secondary;
    }

    #preview-pane {
        height: 12;
        border: solid $accent;
        border-title-color: $accent;
        padding: 0 1;
    }

    #preview-content {
        width: 100%;
    }

    #search-container {
        height: 3;
        padding: 0 1;
    }

    #search-input {
        width: 100%;
    }

    ProjectItem {
        padding: 0 1;
    }

    SessionItem {
        padding: 0 1;
    }

    MessageItem {
        padding: 0 1;
    }

    SearchResultItem {
        padding: 0 1;
    }

    SessionItem Label, MessageItem Label, SearchResultItem Label, ProjectItem Label {
        width: 100%;
        overflow: hidden;
    }

    SessionItem, MessageItem, SearchResultItem {
        height: 1;
        overflow: hidden;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
        Binding("escape", "go_back", "Back"),
        Binding("tab", "switch_pane", "Switch Pane"),
        Binding("r", "reindex", "Reindex"),
        Binding("ctrl+a", "rag_analyze", "RAG Analyze"),
    ]

    def __init__(self, project_filter: Optional[str] = None) -> None:
        super().__init__()
        self._project_filter = project_filter
        self._current_project: Optional[ProjectInfo] = None
        self._view_state = ViewState.PROJECTS

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            projects_pane = ProjectsPane(project_filter=self._project_filter)
            projects_pane.border_title = "Projects"
            yield projects_pane
            content_pane = ContentPane()
            content_pane.border_title = "Sessions"
            yield content_pane
        preview_pane = PreviewPane()
        preview_pane.border_title = "Preview"
        yield preview_pane
        with Horizontal(id="search-container"):
            yield Input(placeholder="Search conversations... (press / to focus)", id="search-input")
        yield Footer()

    def on_projects_pane_project_highlighted(
        self, event: ProjectsPane.ProjectHighlighted
    ) -> None:
        """When a project is highlighted, load its sessions."""
        self._current_project = event.project
        content_pane = self.query_one("#content-pane", ContentPane)
        content_pane.load_sessions(event.project.name)
        self._view_state = ViewState.SESSIONS

    def on_projects_pane_project_selected(
        self, event: ProjectsPane.ProjectSelected
    ) -> None:
        """When a project is selected, focus the content pane."""
        content_pane = self.query_one("#content-pane", ContentPane)
        content_pane.focus()

    def on_content_pane_session_highlighted(
        self, event: ContentPane.SessionHighlighted
    ) -> None:
        """When a session is highlighted, show preview."""
        preview_pane = self.query_one("#preview-pane", PreviewPane)
        preview_pane.show_session(event.session)

    def on_content_pane_session_selected(
        self, event: ContentPane.SessionSelected
    ) -> None:
        """When a session is selected, load its messages."""
        content_pane = self.query_one("#content-pane", ContentPane)
        content_pane.load_messages(event.session)
        self._view_state = ViewState.MESSAGES

    def on_content_pane_message_highlighted(
        self, event: ContentPane.MessageHighlighted
    ) -> None:
        """When a message is highlighted, show its content."""
        preview_pane = self.query_one("#preview-pane", PreviewPane)
        preview_pane.show_message(event.message, event.session)

    def on_content_pane_message_selected(
        self, event: ContentPane.MessageSelected
    ) -> None:
        """When a message is selected, show notification with details."""
        role = "User" if event.message.role == "user" else "Assistant"
        tool_count = len(event.message.tool_use)
        tool_str = f", {tool_count} tool calls" if tool_count else ""
        self.notify(f"{role} message{tool_str}")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle search input."""
        query = event.value.strip()
        if not query:
            return

        try:
            results = search.search(query, limit=50)
            content_pane = self.query_one("#content-pane", ContentPane)
            content_pane.load_search_results(results)
            self._view_state = ViewState.SESSIONS
            if results:
                content_pane.focus()
                self.notify(f"Found {len(results)} results")
            else:
                self.notify("No results found", severity="warning")
        except RuntimeError as e:
            self.notify(f"Search error: {e}", severity="error")

        # Clear the input
        event.input.value = ""

    def action_focus_search(self) -> None:
        """Focus the search input."""
        search_input = self.query_one("#search-input", Input)
        search_input.focus()

    def action_go_back(self) -> None:
        """Go back in navigation."""
        content_pane = self.query_one("#content-pane", ContentPane)

        # If in messages view, go back to sessions
        if content_pane.go_back_to_sessions():
            self._view_state = ViewState.SESSIONS
            return

        # If in sessions view, focus projects pane
        projects_pane = self.query_one("#projects-pane", ProjectsPane)
        projects_pane.focus()
        self._view_state = ViewState.PROJECTS

    def action_switch_pane(self) -> None:
        """Switch focus between projects and content panes."""
        projects_pane = self.query_one("#projects-pane", ProjectsPane)
        content_pane = self.query_one("#content-pane", ContentPane)

        if projects_pane.has_focus:
            content_pane.focus()
        else:
            projects_pane.focus()

    def action_reindex(self) -> None:
        """Reindex conversations and reload projects."""
        self.notify("Reindexing conversations...")
        self.run_worker(self._do_reindex, exclusive=True)

    async def _do_reindex(self) -> None:
        """Worker to perform reindex in background."""
        try:
            projects_dir = get_projects_dir()
            indexed, skipped = index.build_index(projects_dir=projects_dir, force=False)

            # Reload projects pane
            projects_pane = self.query_one("#projects-pane", ProjectsPane)
            projects_pane.load_projects()

            self.notify(f"Reindexed {indexed} sessions ({skipped} unchanged)")
        except Exception as e:
            self.notify(f"Reindex failed: {e}", severity="error")

    def action_rag_analyze(self) -> None:
        """Open RAG analysis modal."""
        self.push_screen(AnalysisInputScreen(), self._handle_analysis_query)

    def _handle_analysis_query(self, query: str) -> None:
        """Handle the query from the analysis input modal."""
        if not query:
            return

        # Get current project filter if any
        project_filter = None
        if self._current_project:
            project_filter = self._current_project.name

        # Show progress screen which will run the analysis
        self.push_screen(AnalysisProgressScreen(query, project_filter))


def main(project_filter: Optional[str] = None) -> None:
    """Run the TUI application."""
    app = ConversationBrowser(project_filter=project_filter)
    app.run()


if __name__ == "__main__":
    main()
