# claude-conversations

A terminal tool to search, explore, and extract from Claude Code conversation history.

## Problem

The `~/.claude/projects/` folder contains valuable conversation transcripts (JSONL files) from past Claude Code sessions. This includes:
- Solutions to problems you've solved before
- Code snippets and implementations
- Architectural decisions and rationale
- Commands and workflows that worked

Currently there's no easy way to search or explore this knowledge base.

## Goal

Build a CLI tool that makes past Claude conversations searchable and extractable.

## Data Structure

```
~/.claude/projects/
├── -Users-bseaton-Work-BUILT-git-repos-welo-data-annotation-platform/
│   ├── {session-uuid}.jsonl    # Conversation transcript
│   └── ...
├── -Users-bseaton-Play-github-repos-cvxr-card-flow/
│   └── ...
└── ...
```

Each `.jsonl` file contains conversation turns with:
- User messages
- Assistant responses
- Tool calls and results
- Timestamps

## Commands

### `claude-conversations search <query>`
Full-text search across all conversations.

```bash
# Search for mentions of "webhook"
claude-conversations search "webhook"

# Search with project filter
claude-conversations search "authentication" --project "*welo*"

# Search only user messages
claude-conversations search "how do I" --role user

# Search only assistant responses
claude-conversations search "import" --role assistant --type code
```

**Output:**
```
[welo-data-annotation-platform] 2024-01-15 session abc123
  Line 847: "For webhook authentication, you'll want to..."

[cvxr-card-flow] 2024-01-10 session def456
  Line 234: "The webhook endpoint should validate..."

Found 12 matches in 4 sessions
```

### `claude-conversations projects`
List all projects with session counts and date ranges.

```bash
claude-conversations projects

# With activity stats
claude-conversations projects --stats
```

**Output:**
```
Project                                    Sessions  Last Active
─────────────────────────────────────────────────────────────────
welo-data-annotation-platform              23        2024-01-20
hanger-cara-commercialization              5         2024-01-22
cvxr-card-flow                             18        2024-01-18
...
```

### `claude-conversations sessions <project>`
List sessions for a project.

```bash
claude-conversations sessions welo-data-annotation-platform

# With summaries (uses first user message as title)
claude-conversations sessions welo-data-annotation-platform --summary
```

### `claude-conversations read <session-id>`
Read a specific session transcript.

```bash
# Full transcript
claude-conversations read abc123

# Just the conversation (no tool calls)
claude-conversations read abc123 --no-tools

# Export as markdown
claude-conversations read abc123 --format md > session.md

# Page through with less
claude-conversations read abc123 | less
```

### `claude-conversations extract <session-id>`
Extract specific content from a session.

```bash
# Extract all code blocks
claude-conversations extract abc123 --code

# Extract code blocks for specific language
claude-conversations extract abc123 --code python

# Extract files that were written
claude-conversations extract abc123 --files

# Extract tool calls of a specific type
claude-conversations extract abc123 --tools Write
```

### `claude-conversations recent`
Show recent sessions across all projects.

```bash
# Last 10 sessions
claude-conversations recent

# Last 20 sessions with summaries
claude-conversations recent -n 20 --summary
```

### `claude-conversations stats`
Show usage statistics.

```bash
claude-conversations stats

# Output:
Total projects: 28
Total sessions: 156
Total messages: 12,847
Most active project: welo-data-annotation-platform (23 sessions)
Date range: 2024-01-01 to 2024-01-22
```

## Implementation Options

### Option A: Python CLI (Recommended)
- Use `click` or `typer` for CLI framework
- Use `rich` for terminal formatting
- SQLite index for fast searching (built on first run)
- Incremental index updates

### Option B: Claude Code Skill
- `/conversations search <query>`
- Runs within Claude Code context
- Can directly reference/load past solutions

### Option C: Both
- Python CLI for terminal use
- Skill that wraps the CLI for in-session use

## Technical Considerations

### JSONL Parsing
Each line in a session file is a JSON object. Need to handle:
- Message roles (user, assistant, system)
- Tool calls and results
- Content blocks (text, code, images)
- Timestamps and metadata

### Search Index
For fast searching:
1. Build SQLite FTS5 index on first run
2. Store: session_id, project, timestamp, role, content, line_number
3. Incremental updates for new sessions

### Privacy
- Tool only reads local files
- No data sent anywhere
- Index stored locally in `~/.claude-conversations/`

## File Structure

```
~/bin/claude-conversations/
├── SPEC.md                 # This file
├── claude-conversations    # Main CLI script
├── requirements.txt        # Python dependencies
├── src/
│   ├── __init__.py
│   ├── cli.py             # CLI commands
│   ├── parser.py          # JSONL parsing
│   ├── index.py           # SQLite indexing
│   ├── search.py          # Search functionality
│   └── formatter.py       # Output formatting
└── tests/
    └── ...
```

## Dependencies

```
click>=8.0
rich>=13.0
```

## Example Use Cases

1. **"How did I solve X before?"**
   ```bash
   claude-conversations search "rate limiting" --type code
   ```

2. **"What did we discuss in the welo project?"**
   ```bash
   claude-conversations sessions welo* --summary
   ```

3. **"Extract that auth middleware I wrote"**
   ```bash
   claude-conversations extract abc123 --code typescript
   ```

4. **"What projects have I worked on this week?"**
   ```bash
   claude-conversations recent -n 50 | grep "2024-01-2"
   ```

## Future Enhancements

- [ ] Semantic search (embeddings)
- [ ] Session summarization
- [ ] "Similar sessions" recommendation
- [ ] Export to searchable archive
- [ ] Integration with Claude Code memory
- [ ] Web UI for browsing

## Priority

P1 - This is a force multiplier for using Claude Code effectively. Past conversations are valuable context that's currently inaccessible.
