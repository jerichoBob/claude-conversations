# claude-conversations

Search and explore your Claude Code conversation history.

## Why This Exists

Every Claude Code session gets saved to `~/.claude/projects/` as JSONL files. Over time, this becomes a goldmine of:

- **Solutions you've already figured out** - that auth middleware, that tricky regex, that deployment script
- **Architectural decisions** - why you chose X over Y, with full context
- **Working commands** - the exact docker/k8s/git incantations that actually worked
- **Code patterns** - implementations you've refined through iteration

The problem? There's no way to search it. You know you solved something similar three weeks ago, but good luck finding it.

This tool fixes that.

## Use Cases

**"I solved this exact problem before..."**
```bash
claude-conversations search "rate limiting"
```

**"What was that code I wrote for my webapp?"**
```bash
claude-conversations sessions my-webapp* --summary
claude-conversations extract abc123 --code python
```

**"Show me everything from this week"**
```bash
claude-conversations recent -n 20
```

**"Export that session as markdown for documentation"**
```bash
claude-conversations read abc123 --format md > session.md
```

## Quick Start

```bash
# Install
python3 -m venv .venv
.venv/bin/pip install -e .

# Build the search index (one-time, then incremental)
./claude-conversations reindex

# Search
./claude-conversations search "webhook"
```

## Commands

| Command | Description |
|---------|-------------|
| `search <query>` | Full-text search across all conversations |
| `projects` | List all projects with session counts |
| `sessions <project>` | List sessions for a project |
| `read <session-id>` | Display full transcript |
| `extract <session-id>` | Extract code blocks, files, or tool calls |
| `recent` | Show recent sessions |
| `stats` | Usage statistics |
| `reindex` | Rebuild the search index |

See [SPEC.md](SPEC.md) for detailed command documentation.

## How It Works

Builds a SQLite FTS5 index of your conversation history for fast full-text search. Index lives at `~/.claude-conversations/index.db`. Everything stays local - no data leaves your machine.

## Changelog

### 0.1.2
- Expand README with use cases and human-friendly descriptions

### 0.1.1
- Add CLAUDE.md with project guidance for Claude Code
