"""Textual TUI for browsing Claude conversations."""

import fnmatch
import re
from datetime import datetime
from enum import Enum, auto
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message as TextualMessage
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static

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
        # Fixed prefix: "abc12345  Jan 24  123  " = 8 + 2 + 6 + 2 + 3 + 2 = 23 chars
        prefix_len = 23
        summary_width = max(10, self._max_width - prefix_len)
        summary = truncate(self.session.first_message or "", summary_width)
        yield Label(
            f"{self.session.session_id[:8]}  {date}  {self.session.message_count:>3}  {summary}"
        )


class MessageItem(ListItem):
    """A message item in the messages list."""

    def __init__(self, message: Message, index: int, max_width: int = 60) -> None:
        super().__init__()
        self.message = message
        self.index = index
        self._max_width = max_width

    def compose(self) -> ComposeResult:
        role = "USER" if self.message.role == "user" else "ASST"
        # Count tool uses if any
        tool_count = len(self.message.tool_use)
        tool_str = f" [{tool_count} tools]" if tool_count else ""
        # Prefix: "123. ASST [99 tools]  " varies, estimate ~25 chars max
        prefix_len = 25
        content_width = max(10, self._max_width - prefix_len)
        content_preview = truncate(self.message.content or "", content_width)
        yield Label(f"{self.index:>3}. {role}{tool_str}  {content_preview}")


class SearchResultItem(ListItem):
    """A search result item."""

    def __init__(self, result: search.SearchResult, max_width: int = 60) -> None:
        super().__init__()
        self.result = result
        self._max_width = max_width

    def compose(self) -> ComposeResult:
        snippet = self.result.snippet.replace(">>>", "").replace("<<<", "")
        # Account for "[project] " prefix
        prefix_len = len(self.result.project) + 3
        content_width = max(10, self._max_width - prefix_len)
        yield Label(f"[{self.result.project}] {truncate(snippet, content_width)}")


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

    @property
    def view_state(self) -> ViewState:
        return self._view_state

    def _get_content_width(self) -> int:
        """Get the available width for content, accounting for borders and padding."""
        # self.size.width gives the widget width
        # Subtract 4 for borders (2) and padding (2)
        width = self.size.width - 4 if self.size.width > 10 else 60
        return max(20, width)

    def load_sessions(self, project: str) -> None:
        """Load sessions for a project."""
        if project == self._current_project and self._view_state == ViewState.SESSIONS:
            return
        self._current_project = project
        self._current_session = None
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
        dock: bottom;
    }

    #search-input {
        width: 1fr;
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
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("/", "focus_search", "Search"),
        Binding("escape", "go_back", "Back"),
        Binding("tab", "switch_pane", "Switch Pane"),
        Binding("r", "reindex", "Reindex"),
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


def main(project_filter: Optional[str] = None) -> None:
    """Run the TUI application."""
    app = ConversationBrowser(project_filter=project_filter)
    app.run()


if __name__ == "__main__":
    main()
