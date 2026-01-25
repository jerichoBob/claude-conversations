---
name: c3po
description: Search and extract from Claude Code conversation history. Use when the user asks about past solutions, code they wrote before, previous discussions, or wants to find something from earlier sessions.
allowed-tools: Bash, Read, Grep
---

# Claude Conversations Skill

Search, explore, and extract from your Claude Code conversation history to find past solutions, code patterns, and architectural decisions.

## Commands

```bash
# Search for keywords across all conversations
./claude-conversations search "rate limiting"
./claude-conversations search "authentication" --project "*webapp*"

# Browse projects and sessions
./claude-conversations projects
./claude-conversations sessions my-project*
./claude-conversations recent -n 20

# Read a full session transcript
./claude-conversations read abc123
./claude-conversations read abc123 --format md

# Extract specific content
./claude-conversations extract abc123 --code
./claude-conversations extract abc123 --code --lang python
./claude-conversations extract abc123 --files
./claude-conversations extract abc123 --tools Write
```

## Instructions

1. **Understand the request** - What is the user looking for? A past solution, code pattern, command, or discussion?

2. **Search first** - Start with a broad search using relevant keywords:

   ```bash
   ./claude-conversations search "keyword"
   ```

3. **Narrow down** - If too many results, filter by project or role:

   ```bash
   ./claude-conversations search "keyword" --project "*project-name*"
   ./claude-conversations search "keyword" --role assistant
   ```

4. **Drill into sessions** - Once you find a promising session, read it or extract from it:

   ```bash
   ./claude-conversations read abc123
   ./claude-conversations extract abc123 --code --lang python
   ```

5. **Present findings** - Share the relevant code, solution, or context with the user. Adapt it to their current needs if necessary.

## Tips

- Session IDs can be abbreviated to first 8 characters
- Project filters support wildcards: `*webapp*`, `data-*`
- Use `--format md` when exporting for documentation
- The index at `~/.claude-conversations/index.db` updates incrementally; run `reindex` if conversations seem missing
