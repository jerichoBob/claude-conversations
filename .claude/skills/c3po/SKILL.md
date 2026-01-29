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

## Interactive Mode

When user runs `/c3po --interactive` or asks to "browse", "explore", or "navigate" conversations interactively:

### Navigation Flow

The interactive mode follows this state machine:

```
START → PROJECTS → SESSIONS → SESSION_DETAIL
             ↑         ↑              ↓
             └─────────┴──── BACK ────┘
```

### Step 1: Show Projects

Display all projects using:
```bash
./claude-conversations projects
```

Then use AskUserQuestion with these options:
- **Select a project** (show top 4 by session count as options)
- **Search across all** - "I want to search for something specific"
- **Show recent** - "Show my most recent sessions"
- **Analyze patterns** - "Analyze patterns across all my conversations"

### Step 2: On Project Select - Show Sessions

Display sessions for the selected project:
```bash
./claude-conversations sessions "<project-name>" --summary
```

Then use AskUserQuestion with these options:
- **Select a session** (show top 4 by date as options)
- **Search within project** - "Search within this project"
- **Go back** - "Go back to projects"
- **Analyze project** - "Analyze this project's patterns"

### Step 3: On Session Select - Show Detail

Display the session transcript:
```bash
./claude-conversations read <session-id>
```

Then use AskUserQuestion with these options:
- **Extract code** - "Extract code from this session"
- **Analyze session** - "Analyze what was accomplished"
- **Search within** - "Search for something in this session"
- **Go back** - "Go back to sessions list"

### Step 4: Continue the Loop

Keep navigating until the user:
- Says "done", "exit", or "quit"
- Selects an "Exit" option
- Asks a different unrelated question

### Analysis Capabilities

When the user asks to "analyze" at any level, use your LLM capabilities to provide insights:

**Session analysis:**
- Summarize what was accomplished in the session
- Identify key decisions made
- List tools and commands used
- Extract reusable patterns or solutions
- Note any problems encountered and how they were solved

**Project analysis:**
- Common themes across sessions in the project
- Recurring problems and how they were solved
- Tool usage patterns (most used tools, workflows)
- Technology stack insights
- Workflow and process observations

**Cross-project analysis:**
- Most active projects and their focus areas
- Technology patterns across projects
- Common request types the user makes
- Evolution of coding practices over time

### Handling User Queries During Navigation

At any point, if the user asks a natural language question instead of selecting an option:

- **"How did I solve X before?"** → Search for X, read top results, summarize approaches
- **"Show me all Python code for Y"** → Search with project filter, extract code blocks
- **"What patterns do you see?"** → Analyze current context (session/project/all)
- **"Find where I implemented Z"** → Search, show matching sessions

### Example Interactive Session

```
User: /c3po --interactive

Claude: Here are your projects:
[displays projects table]

Which would you like to explore?
[AskUserQuestion with project options + Search/Recent/Analyze]

User: [selects "cvxr-card-flow"]

Claude: Here are the sessions in cvxr-card-flow:
[displays sessions table with summaries]

What would you like to do?
[AskUserQuestion with session options + Search/Back/Analyze]

User: "How did I handle authentication in this project?"

Claude: [searches for "authentication" within project]
[reads relevant sessions]
[provides analysis of auth approaches used]

Would you like to:
[AskUserQuestion: Dive into a specific session / Continue exploring / Go back]
```

### Analysis Command Support

For structured analysis output, use:
```bash
./claude-conversations analyze <session-id>
./claude-conversations analyze --project "*pattern*"
```

This provides tool usage statistics, file operations, and summary data that you can then interpret and explain to the user.
