# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

A CLI tool for searching, exploring, and extracting content from Claude Code conversation history stored in `~/.claude/projects/`. Uses SQLite FTS5 for full-text search.

## Development Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
```

## Configuration

Optional `.env` file for AI analysis features:

```bash
ANTHROPIC_API_KEY=sk-ant-xxx  # Required for rag-analyze command
```

## Running the Tool

```bash
# Build index first (required)
./claude-conversations reindex

# Search conversations
./claude-conversations search "query"
./claude-conversations search "auth" --project "*my-webapp*"

# Browse
./claude-conversations projects
./claude-conversations sessions <project>
./claude-conversations recent

# Read/extract
./claude-conversations read <session-id>
./claude-conversations extract <session-id> --code python

# Interactive TUI
./claude-conversations tui
./claude-conversations tui -p "BUILT-*"

# Statistical analysis
./claude-conversations analyze abc123
./claude-conversations analyze --project "*webapp*"

# AI-powered RAG analysis
./claude-conversations rag-analyze "How did I implement auth?"
./claude-conversations rag-analyze --list
./claude-conversations rag-analyze --show abc12345
```

## Architecture

**Data flow**: `~/.claude/projects/` (JSONL files) → `core/` → SQLite FTS5 index → `cli/` or `api/`

**Core library** (`core/`) - no CLI/web dependencies:

- `parser.py` - Parses JSONL conversation files into `Session`/`Message`/`CodeBlock` dataclasses
- `index.py` - Builds and maintains SQLite FTS5 index at `~/.claude-conversations/index.db`
- `search.py` - Query interface with `SearchResult`, `SessionInfo`, `ProjectInfo` dataclasses
- `agents.py` - Multi-agent RAG analysis system with coordinator and specialist agents
- `chunking.py` - Token-aware session chunking for LLM context limits (50k tokens per chunk)
- `persistence.py` - Save/load analysis results to `~/.claude-conversations/analyses/`

**CLI** (`cli/`) - terminal interface:

- `main.py` - Click command handlers
- `formatter.py` - Rich terminal output (tables, syntax highlighting, markdown)
- `tui.py` - Textual-based interactive terminal UI

**API** (`api/`) - for web apps (Streamlit, Next.js, etc.)

**Entry point**: The `claude-conversations` bash script runs `python -m cli.main`

## Key Design Decisions

- Incremental indexing via file mtime comparison (avoids reprocessing unchanged files)
- Partial session ID matching (first 8 chars) for convenience
- Wildcard project filters (`*pattern*` → SQL LIKE)
- FTS5 with porter stemming and unicode61 tokenizer
- Token-aware chunking (50k tokens/chunk) for large sessions in RAG analysis
- Multi-agent system: coordinator decomposes queries, specialists analyze chunks
- Saved analyses persisted to `~/.claude-conversations/analyses/` as JSON
- TUI built with Textual framework for rich terminal interactions
