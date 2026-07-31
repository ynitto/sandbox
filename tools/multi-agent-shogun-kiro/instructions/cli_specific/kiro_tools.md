# Kiro CLI Tools

This section describes AWS Kiro CLI-specific tools and features.

## Overview

Kiro CLI (`kiro-cli`) is a terminal AI coding agent with workspace steering, local custom agents, MCP integration, and directory-scoped session persistence.

- **Launch**: `kiro-cli` or `kiro-cli chat --agent <name>`
- **Install**: `curl -fsSL https://cli.kiro.dev/install | bash`
- **Auth**: `kiro-cli login`
- **Default model**: Workspace default unless `--model` is specified
- **Session persistence**: Stored per directory under `~/.kiro/`

## Tool Usage

Kiro CLI provides an agentic tool set comparable to other coding CLIs:

### File and Search Tools
- Read files from the workspace
- Create and edit files
- Search by path or content

### Command Execution
- Run shell commands in the current workspace
- Respect workspace context and custom agent permissions

### MCP Tools
- Load MCP servers from `~/.kiro/settings/mcp.json` and `.kiro/settings/mcp.json`
- Expose MCP tools to custom agents when `includeMcpJson` is enabled

## Permission Model

Kiro CLI supports explicit tool permissions and a trust-all-tools mode for unattended execution.

| Mode | Behavior | Flag |
|------|----------|------|
| Interactive | Prompts before sensitive operations | (default) |
| Trust all tools | Auto-approve tool use | `--trust-all-tools` |

**Shogun system usage**: workers run with `--trust-all-tools` so inbox-driven execution can continue without manual confirmations.

## Local Custom Agents

Shogun integrates Kiro through workspace-local custom agents in `.kiro/agents/`.

- `shogun.json`
- `karo.json`
- `ashigaru.json`
- `gunshi.json`

Each custom agent uses the corresponding generated prompt file:

- `instructions/generated/kiro-shogun.md`
- `instructions/generated/kiro-karo.md`
- `instructions/generated/kiro-ashigaru.md`
- `instructions/generated/kiro-gunshi.md`

Launch examples:

```bash
kiro-cli chat --agent shogun
kiro-cli chat --agent ashigaru --trust-all-tools
kiro-cli chat --agent gunshi --trust-all-tools --model anthropic.claude-3-7-sonnet-20250219-v1:0
```

## Steering and Context

Kiro supports workspace steering files under `.kiro/steering/` and global steering under `~/.kiro/steering/`.

- Steering files are automatically loaded in normal chat sessions
- Custom agents do **not** auto-load steering unless it is referenced in `resources`
- `AGENTS.md` may also be read by Kiro, but Shogun relies on custom-agent prompt files for role separation

## Session Management

Kiro stores sessions per directory in `~/.kiro/`.

- Resume last session: `kiro-cli chat --resume`
- Pick a previous session: `kiro-cli chat --resume-picker`
- Start a fresh in-session conversation: `/chat new`
- Save a session to JSON: `/chat save <path>`

For the Shogun system, stale task context is reset with `/chat new` instead of `/clear`.

## Command Line Reference

| Flag | Purpose |
|------|---------|
| `chat --agent <name>` | Select a local or global custom agent |
| `--model <id>` | Override the default model |
| `--trust-all-tools` | Auto-approve tool use |
| `chat --resume` | Resume the latest session in the current directory |
| `chat --resume-picker` | Select a previous session interactively |
| `chat --list-sessions` | List saved sessions |

## Limitations (vs Claude Code)

| Feature | Claude Code | Kiro CLI | Impact |
|---------|-------------|----------|--------|
| Auto-load role file | `CLAUDE.md` | Custom agent prompt | Shogun generates `.kiro/agents/*.json` |
| Context reset | `/clear` | `/chat new` | inbox_watcher converts resets |
| Runtime model switch | `/model` | Not supported in-session | Use `switch_cli.sh` restart flow |
| Self-watch stop hook | Available | Not available | Kiro relies on tmux nudges |

## Configuration Files Summary

| File | Location | Purpose |
|------|----------|---------|
| Agent configs | `.kiro/agents/*.json` | Role-specific Kiro custom agents |
| Steering | `.kiro/steering/*.md` | Workspace steering files |
| MCP config | `.kiro/settings/mcp.json` or `~/.kiro/settings/mcp.json` | MCP servers |
| Session DB | `~/.kiro/` | Directory-scoped chat history |

---

*Sources: [Kiro CLI docs](https://kiro.dev/docs/cli/), [Custom agents](https://kiro.dev/docs/cli/custom-agents/creating/), [Agent configuration reference](https://kiro.dev/docs/cli/custom-agents/configuration-reference/), [Steering](https://kiro.dev/docs/cli/steering/), [Session management](https://kiro.dev/docs/cli/chat/session-management/)*