"""Textual TUI for browsing Claude conversations."""

from datetime import datetime
from typing import Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, Static
from textual.css.query import NoMatches

from core import search
from core.search import ProjectInfo, SessionInfo


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


class ProjectItem(ListItem):
    """A project item in the projects list."""

    def __init__(self, project: ProjectInfo) -> None:
        super().__init__()
        self.project = project

    def compose(self) -> ComposeResult:
        yield Label(f"{self.project.name} ({self.project.session_count})")


class SessionItem(ListItem):
    """A session item in the sessions list."""

    def __init__(self, session: SessionInfo) -> None:
        super().__init__()
        self.session = session

    def compose(self) -> ComposeResult:
        date = format_date(self.session.start_time)
        summary = truncate(self.session.first_message or "", 40)
        yield Label(
            f"{self.session.session_id[:8]}  {date}  {self.session.message_count:>3}  {summary}"
        )


class SearchResultItem(ListItem):
    """A search result item."""

    def __init__(self, result: search.SearchResult) -> None:
        super().__init__()
        self.result = result

    def compose(self) -> ComposeResult:
        snippet = self.result.snippet.replace(">>>", "").replace("<<<", "")
        yield Label(f"[{self.result.project}] {truncate(snippet, 60)}")


class ProjectsPane(ListView):
    """Pane showing all projects."""

    class ProjectHighlighted(Message):
        """Sent when a project is highlighted."""

        def __init__(self, project: ProjectInfo) -> None:
            super().__init__()
            self.project = project

    class ProjectSelected(Message):
        """Sent when a project is selected (Enter)."""

        def __init__(self, project: ProjectInfo) -> None:
            super().__init__()
            self.project = project

    def __init__(self) -> None:
        super().__init__(id="projects-pane")
        self._projects: list[ProjectInfo] = []

    def on_mount(self) -> None:
        self.load_projects()

    def load_projects(self) -> None:
        """Load projects from the search index."""
        try:
            self._projects = search.get_projects()
            self.clear()
            for project in self._projects:
                self.append(ProjectItem(project))
        except RuntimeError:
            self.clear()
            self.append(ListItem(Label("Index not found. Run: claude-conversations reindex")))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item and isinstance(event.item, ProjectItem):
            self.post_message(self.ProjectHighlighted(event.item.project))

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item and isinstance(event.item, ProjectItem):
            self.post_message(self.ProjectSelected(event.item.project))


class SessionsPane(ListView):
    """Pane showing sessions for selected project."""

    class SessionHighlighted(Message):
        """Sent when a session is highlighted."""

        def __init__(self, session: SessionInfo) -> None:
            super().__init__()
            self.session = session

    class SessionSelected(Message):
        """Sent when a session is selected (Enter)."""

        def __init__(self, session: SessionInfo) -> None:
            super().__init__()
            self.session = session

    def __init__(self) -> None:
        super().__init__(id="sessions-pane")
        self._sessions: list[SessionInfo] = []
        self._current_project: Optional[str] = None

    def load_sessions(self, project: str) -> None:
        """Load sessions for a project."""
        if project == self._current_project:
            return
        self._current_project = project
        try:
            self._sessions = search.get_sessions(project=project, limit=200)
            self.clear()
            for session in self._sessions:
                self.append(SessionItem(session))
            # Update the border title
            self.border_title = f"Sessions ({project})"
        except RuntimeError:
            self.clear()

    def load_search_results(self, results: list[search.SearchResult]) -> None:
        """Load search results instead of sessions."""
        self._current_project = None
        self._sessions = []
        self.clear()
        for result in results:
            self.append(SearchResultItem(result))
        self.border_title = f"Search Results ({len(results)})"

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item and isinstance(event.item, SessionItem):
            self.post_message(self.SessionHighlighted(event.item.session))
        elif event.item and isinstance(event.item, SearchResultItem):
            # Try to load session info for search results
            try:
                session_info = search.get_session_by_id(event.item.result.session_id)
                if session_info:
                    self.post_message(self.SessionHighlighted(session_info))
            except (RuntimeError, ValueError):
                pass

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if event.item and isinstance(event.item, SessionItem):
            self.post_message(self.SessionSelected(event.item.session))
        elif event.item and isinstance(event.item, SearchResultItem):
            try:
                session_info = search.get_session_by_id(event.item.result.session_id)
                if session_info:
                    self.post_message(self.SessionSelected(session_info))
            except (RuntimeError, ValueError):
                pass


class PreviewPane(Static):
    """Pane showing preview of selected session."""

    def __init__(self) -> None:
        super().__init__(id="preview-pane")
        self._session: Optional[SessionInfo] = None

    def show_session(self, session: SessionInfo) -> None:
        """Show session preview."""
        self._session = session

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

First message: "{truncate(session.first_message or '', 80)}" """
        self.update(preview_text)

    def clear_preview(self) -> None:
        """Clear the preview."""
        self._session = None
        self.update("Select a session to preview")


class SessionDetailView(Static):
    """Full session detail view."""

    BINDINGS = [
        Binding("escape", "close", "Back"),
    ]

    def __init__(self, session: SessionInfo) -> None:
        super().__init__(id="session-detail")
        self.session_info = session

    def compose(self) -> ComposeResult:
        try:
            full_session = search.load_session(self.session_info.session_id)
            lines = []
            lines.append(
                f"Session: {full_session.session_id} | Project: {full_session.project}"
            )
            lines.append(
                f"Date: {format_timestamp(full_session.start_time)} | Messages: {full_session.message_count}"
            )
            lines.append("=" * 80)
            lines.append("")

            for msg in full_session.messages:
                role = "USER" if msg.role == "user" else "ASSISTANT"
                lines.append(f"[{role}] {format_timestamp(msg.timestamp)}")
                lines.append("-" * 40)
                if msg.content:
                    # Truncate very long messages
                    content = msg.content
                    if len(content) > 2000:
                        content = content[:2000] + "\n... (truncated)"
                    lines.append(content)
                lines.append("")

            yield Label("\n".join(lines))
        except (RuntimeError, ValueError) as e:
            yield Label(f"Error loading session: {e}")

    def action_close(self) -> None:
        self.remove()


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

    #sessions-pane {
        width: 1fr;
        border: solid $secondary;
        border-title-color: $secondary;
    }

    #preview-pane {
        height: 6;
        border: solid $accent;
        border-title-color: $accent;
        padding: 0 1;
    }

    #search-container {
        height: 3;
        dock: bottom;
    }

    #search-input {
        width: 1fr;
    }

    #session-detail {
        width: 100%;
        height: 100%;
        background: $surface;
        padding: 1;
        overflow-y: auto;
    }

    ProjectItem {
        padding: 0 1;
    }

    SessionItem {
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
    ]

    def __init__(self) -> None:
        super().__init__()
        self._current_project: Optional[ProjectInfo] = None
        self._in_detail_view = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Horizontal(id="main"):
            projects_pane = ProjectsPane()
            projects_pane.border_title = "Projects"
            yield projects_pane
            sessions_pane = SessionsPane()
            sessions_pane.border_title = "Sessions"
            yield sessions_pane
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
        sessions_pane = self.query_one("#sessions-pane", SessionsPane)
        sessions_pane.load_sessions(event.project.name)

    def on_projects_pane_project_selected(
        self, event: ProjectsPane.ProjectSelected
    ) -> None:
        """When a project is selected, focus the sessions pane."""
        sessions_pane = self.query_one("#sessions-pane", SessionsPane)
        sessions_pane.focus()

    def on_sessions_pane_session_highlighted(
        self, event: SessionsPane.SessionHighlighted
    ) -> None:
        """When a session is highlighted, show preview."""
        preview_pane = self.query_one("#preview-pane", PreviewPane)
        preview_pane.show_session(event.session)

    def on_sessions_pane_session_selected(
        self, event: SessionsPane.SessionSelected
    ) -> None:
        """When a session is selected, show full detail view."""
        # For now, just show more in the preview
        # A full detail view could be added later with scrolling
        self.notify(
            f"Session {event.session.session_id[:8]} - {event.session.message_count} messages"
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle search input."""
        query = event.value.strip()
        if not query:
            return

        try:
            results = search.search(query, limit=50)
            sessions_pane = self.query_one("#sessions-pane", SessionsPane)
            sessions_pane.load_search_results(results)
            if results:
                sessions_pane.focus()
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
        # If detail view is shown, close it
        try:
            detail = self.query_one("#session-detail", SessionDetailView)
            detail.remove()
            self._in_detail_view = False
            return
        except NoMatches:
            pass

        # Otherwise focus projects pane
        projects_pane = self.query_one("#projects-pane", ProjectsPane)
        projects_pane.focus()

    def action_switch_pane(self) -> None:
        """Switch focus between projects and sessions panes."""
        projects_pane = self.query_one("#projects-pane", ProjectsPane)
        sessions_pane = self.query_one("#sessions-pane", SessionsPane)

        if projects_pane.has_focus:
            sessions_pane.focus()
        else:
            projects_pane.focus()


def main() -> None:
    """Run the TUI application."""
    app = ConversationBrowser()
    app.run()


if __name__ == "__main__":
    main()
