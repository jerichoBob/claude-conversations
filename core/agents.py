"""Multi-agent RAG analysis system for conversation history."""

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

from dotenv import load_dotenv

from . import search

# Load .env file from project root or current directory
_env_paths = [
    Path.cwd() / ".env",
    Path(__file__).parent.parent / ".env",
]
for _env_path in _env_paths:
    if _env_path.exists():
        load_dotenv(_env_path)
        break

from .chunking import (
    SessionChunk,
    chunk_multiple_sessions,
    MAX_TOKENS_PER_CHUNK,
)
from .parser import Message, Session
from .search import SearchResult, SessionInfo


DEFAULT_MODEL = "claude-sonnet-4-20250514"

# Type for progress callback: (stage: str, detail: str) -> None
ProgressCallback = Callable[[str, str], None]


@dataclass
class DecomposedQuery:
    """Result of query decomposition."""
    original_query: str
    search_queries: list[str]  # Multiple search terms to try
    analysis_prompt: str  # Enriched prompt for analysis
    comparison_needed: bool  # Whether to compare across sessions/projects


@dataclass
class AgentContext:
    """Context passed between agents."""
    sessions: list[Session] = field(default_factory=list)
    search_results: list[SearchResult] = field(default_factory=list)
    session_chunks: list[SessionChunk] = field(default_factory=list)
    analyses: dict[str, str] = field(default_factory=dict)


def get_api_key() -> Optional[str]:
    """Get the Anthropic API key from environment."""
    return os.environ.get("ANTHROPIC_API_KEY")


def check_api_key() -> None:
    """Check if API key is available, raise helpful error if not."""
    if not get_api_key():
        raise RuntimeError(
            "ANTHROPIC_API_KEY is not set.\n"
            "Option 1: Create a .env file with: ANTHROPIC_API_KEY=your-key-here\n"
            "Option 2: Export in shell: export ANTHROPIC_API_KEY=your-key-here\n"
            "Get an API key at: https://console.anthropic.com/"
        )


def _noop_progress(stage: str, detail: str) -> None:
    """No-op progress callback."""
    pass


class BaseAgent:
    """Base class for all agents."""

    def __init__(
        self,
        client: Any = None,
        model: str = DEFAULT_MODEL,
    ):
        self.model = model
        self._client = client

    @property
    def client(self):
        """Lazy-load the Anthropic client."""
        if self._client is None:
            check_api_key()
            from anthropic import Anthropic
            self._client = Anthropic()
        return self._client

    def _call_api(
        self,
        messages: list[dict],
        system: str,
        tools: Optional[list[dict]] = None,
        max_tokens: int = 4096,
    ) -> dict:
        """Make an API call to Claude."""
        kwargs = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if tools:
            kwargs["tools"] = tools

        response = self.client.messages.create(**kwargs)
        return response


class QueryDecomposer(BaseAgent):
    """Decomposes user queries into search terms and analysis prompts."""

    SYSTEM_PROMPT = """You are a query analyzer for a conversation history search system.

Given a user's question about their Claude Code conversation history, you must:
1. Generate 3-5 specific search queries that will find relevant conversations
2. Create an enriched analysis prompt that will guide the analysis
3. Determine if this requires comparing multiple sessions/projects

Output your response as JSON with this exact structure:
{
    "search_queries": ["query1", "query2", "query3"],
    "analysis_prompt": "Detailed prompt for analyzing the found conversations...",
    "comparison_needed": true/false
}

IMPORTANT:
- Search queries should be specific keywords/phrases, not full sentences
- Include synonyms and related terms
- If the user mentions specific projects, include project-specific terms
- The analysis_prompt should capture the user's intent in detail
- Set comparison_needed=true if comparing across projects or sessions"""

    def decompose(self, query: str, projects: list[str] = None) -> DecomposedQuery:
        """Decompose a user query into search terms and analysis prompt."""
        user_content = f"User question: {query}"
        if projects:
            user_content += f"\n\nSpecific projects to analyze: {', '.join(projects)}"

        messages = [{"role": "user", "content": user_content}]

        response = self._call_api(
            messages=messages,
            system=self.SYSTEM_PROMPT,
            max_tokens=1024,
        )

        # Extract text response
        text_blocks = [b for b in response.content if hasattr(b, 'text')]
        if not text_blocks:
            # Fallback
            return DecomposedQuery(
                original_query=query,
                search_queries=[query],
                analysis_prompt=query,
                comparison_needed=bool(projects and len(projects) > 1),
            )

        response_text = text_blocks[0].text

        # Parse JSON from response
        try:
            # Find JSON in response (might have extra text)
            json_start = response_text.find('{')
            json_end = response_text.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                parsed = json.loads(response_text[json_start:json_end])
                return DecomposedQuery(
                    original_query=query,
                    search_queries=parsed.get("search_queries", [query]),
                    analysis_prompt=parsed.get("analysis_prompt", query),
                    comparison_needed=parsed.get("comparison_needed", False),
                )
        except json.JSONDecodeError:
            pass

        # Fallback if parsing fails
        return DecomposedQuery(
            original_query=query,
            search_queries=[query],
            analysis_prompt=query,
            comparison_needed=bool(projects and len(projects) > 1),
        )


class AnalysisAgent(BaseAgent):
    """Analyzes conversation sessions to answer user questions."""

    SYSTEM_PROMPT = """You are an expert analyst of Claude Code conversation histories.

Your job is to analyze conversation sessions and provide insightful answers to user questions.

When analyzing conversations, look for:
- Tool usage patterns (Read, Write, Edit, Bash, etc.)
- Conversation phases (discovery, implementation, testing, debugging)
- Key decisions and their rationale
- Problems encountered and how they were solved
- Code and file patterns

Provide clear, structured responses with specific examples from the conversations."""

    def analyze(
        self,
        chunks: list[SessionChunk],
        analysis_prompt: str,
        progress: ProgressCallback = _noop_progress,
    ) -> str:
        """Analyze session chunks to answer the analysis prompt."""
        if not chunks:
            return "No sessions to analyze."

        progress("analyzing", f"Analyzing {len(chunks)} session chunks...")

        # Build context from chunks
        context_parts = []
        for i, chunk in enumerate(chunks):
            progress("analyzing", f"Processing chunk {i+1}/{len(chunks)}: {chunk.session_id[:8]}")
            context_parts.append(chunk.to_text())

        context_text = "\n\n---\n\n".join(context_parts)

        # Truncate if too long
        max_chars = MAX_TOKENS_PER_CHUNK * 3
        if len(context_text) > max_chars:
            context_text = context_text[:max_chars] + "\n\n[... content truncated for length ...]"

        messages = [{
            "role": "user",
            "content": f"""Based on the following conversation history, please answer this question:

**Question:** {analysis_prompt}

---

**Conversation History:**

{context_text}

---

Please provide a comprehensive answer based on the conversation history above."""
        }]

        progress("analyzing", "Generating analysis...")

        response = self._call_api(
            messages=messages,
            system=self.SYSTEM_PROMPT,
            max_tokens=4096,
        )

        text_blocks = [b for b in response.content if hasattr(b, 'text')]
        return text_blocks[0].text if text_blocks else "No analysis generated."


class ComparisonAgent(BaseAgent):
    """Compares analyses across sessions/projects."""

    SYSTEM_PROMPT = """You are an expert at comparing and contrasting Claude Code conversation patterns.

When comparing multiple sessions or projects, identify:
- Common patterns and approaches
- Key differences in methodology
- Unique insights from each
- Best practices that emerge
- Recommendations based on the comparison

Be specific and cite examples from the analyses."""

    def compare(
        self,
        analyses: dict[str, str],
        comparison_prompt: str,
        progress: ProgressCallback = _noop_progress,
    ) -> str:
        """Compare analyses across sessions/projects."""
        if len(analyses) < 2:
            return analyses.get(list(analyses.keys())[0], "No analysis to compare.") if analyses else "No analyses provided."

        progress("comparing", f"Comparing {len(analyses)} analyses...")

        # Build comparison context
        context_parts = []
        for session_id, analysis in analyses.items():
            context_parts.append(f"### Analysis of {session_id[:8]}\n\n{analysis}")

        comparison_context = "\n\n---\n\n".join(context_parts)

        messages = [{
            "role": "user",
            "content": f"""Compare the following analyses to answer:

**Question:** {comparison_prompt}

---

{comparison_context}

---

Please provide a comprehensive comparison that synthesizes insights from all analyses."""
        }]

        progress("comparing", "Generating comparison...")

        response = self._call_api(
            messages=messages,
            system=self.SYSTEM_PROMPT,
            max_tokens=4096,
        )

        text_blocks = [b for b in response.content if hasattr(b, 'text')]
        return text_blocks[0].text if text_blocks else "No comparison generated."


class RAGAnalyzer:
    """Main orchestrator for RAG analysis of conversation history."""

    def __init__(self, model: str = DEFAULT_MODEL, progress: ProgressCallback = None):
        self.model = model
        self.progress = progress or _noop_progress
        self._client = None

        # Agents
        self.decomposer = QueryDecomposer(model=model)
        self.analyzer = AnalysisAgent(model=model)
        self.comparator = ComparisonAgent(model=model)

        # State
        self.context = AgentContext()
        self.log: list[dict] = []

    def _log(self, stage: str, detail: str, data: dict = None):
        """Log a step in the analysis."""
        entry = {"stage": stage, "detail": detail}
        if data:
            entry["data"] = data
        self.log.append(entry)
        self.progress(stage, detail)

    def _search_sessions(
        self,
        queries: list[str],
        projects: list[str] = None,
    ) -> list[Session]:
        """Search for sessions matching the queries."""
        all_sessions = {}  # session_id -> Session (deduplicate)

        for query in queries:
            self._log("searching", f"Searching for: '{query}'")

            if projects:
                # Search each project separately
                for project in projects:
                    project_filter = f"*{project}*"
                    self._log("searching", f"  In project: {project}")
                    try:
                        results = search.search(query, project=project_filter, limit=20)
                        self.context.search_results.extend(results)

                        # Load unique sessions
                        for result in results:
                            if result.session_id not in all_sessions:
                                try:
                                    session = search.load_session(result.session_id)
                                    all_sessions[result.session_id] = session
                                    self._log("searching", f"    Found: {session.session_id[:8]} ({session.message_count} msgs)")
                                except ValueError:
                                    pass
                    except RuntimeError as e:
                        self._log("searching", f"    Search error: {e}")
            else:
                # Search all projects
                try:
                    results = search.search(query, limit=30)
                    self.context.search_results.extend(results)

                    for result in results[:10]:  # Limit sessions loaded per query
                        if result.session_id not in all_sessions:
                            try:
                                session = search.load_session(result.session_id)
                                all_sessions[result.session_id] = session
                                self._log("searching", f"  Found: {session.session_id[:8]} in {session.project}")
                            except ValueError:
                                pass
                except RuntimeError as e:
                    self._log("searching", f"Search error: {e}")

        sessions = list(all_sessions.values())
        self._log("searching", f"Total unique sessions found: {len(sessions)}")
        return sessions

    def analyze(
        self,
        query: str,
        projects: list[str] = None,
    ) -> tuple[str, list[str], list[dict]]:
        """Run the full RAG analysis workflow.

        Args:
            query: The user's question
            projects: Optional list of project names/patterns to filter

        Returns:
            Tuple of (analysis_result, session_ids, analysis_log)
        """
        # Reset state
        self.context = AgentContext()
        self.log = []

        self._log("starting", f"Analyzing: {query}")
        if projects:
            self._log("starting", f"Projects: {', '.join(projects)}")

        # Step 1: Decompose the query
        self._log("decomposing", "Breaking down your question...")
        decomposed = self.decomposer.decompose(query, projects)
        self._log("decomposing", f"Generated {len(decomposed.search_queries)} search queries")
        for sq in decomposed.search_queries:
            self._log("decomposing", f"  - {sq}")

        # Step 2: Search for relevant sessions
        self._log("searching", "Searching conversation history...")
        sessions = self._search_sessions(decomposed.search_queries, projects)
        self.context.sessions = sessions

        if not sessions:
            self._log("complete", "No relevant sessions found.")
            return "No relevant conversations found for your query.", [], self.log

        # Step 3: Chunk sessions for analysis
        self._log("chunking", f"Preparing {len(sessions)} sessions for analysis...")
        self.context.session_chunks = chunk_multiple_sessions(sessions, MAX_TOKENS_PER_CHUNK)
        self._log("chunking", f"Created {len(self.context.session_chunks)} chunks")

        # Step 4: Analyze sessions
        if decomposed.comparison_needed and len(sessions) > 1:
            # Analyze each session/project separately, then compare
            self._log("analyzing", "Analyzing sessions separately for comparison...")

            # Group chunks by session
            session_chunks: dict[str, list[SessionChunk]] = {}
            for chunk in self.context.session_chunks:
                if chunk.session_id not in session_chunks:
                    session_chunks[chunk.session_id] = []
                session_chunks[chunk.session_id].append(chunk)

            # Analyze each session
            for session_id, chunks in session_chunks.items():
                self._log("analyzing", f"Analyzing session {session_id[:8]}...")
                analysis = self.analyzer.analyze(
                    chunks,
                    decomposed.analysis_prompt,
                    progress=self.progress,
                )
                self.context.analyses[session_id] = analysis

            # Compare analyses
            self._log("comparing", "Comparing analyses across sessions...")
            final_result = self.comparator.compare(
                self.context.analyses,
                decomposed.analysis_prompt,
                progress=self.progress,
            )
        else:
            # Analyze all chunks together
            self._log("analyzing", "Analyzing all sessions together...")
            final_result = self.analyzer.analyze(
                self.context.session_chunks,
                decomposed.analysis_prompt,
                progress=self.progress,
            )

        self._log("complete", "Analysis complete!")

        session_ids = [s.session_id for s in sessions]
        return final_result, session_ids, self.log


def run_analysis(
    query: str,
    project_filter: Optional[str] = None,
    model: str = DEFAULT_MODEL,
    progress: ProgressCallback = None,
) -> tuple[str, list[str], list[dict]]:
    """Convenience function to run a full RAG analysis.

    Args:
        query: The analysis question
        project_filter: Optional comma-separated project filters
        model: Model to use for analysis
        progress: Optional callback for progress updates

    Returns:
        Tuple of (analysis_result, session_ids, analysis_log)
    """
    check_api_key()

    # Parse project filter
    projects = None
    if project_filter:
        projects = [p.strip() for p in project_filter.split(",")]

    analyzer = RAGAnalyzer(model=model, progress=progress)
    return analyzer.analyze(query, projects)
