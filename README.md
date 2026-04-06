# AI Discord Bot

A multi-backend AI Discord bot that routes messages to **Claude Code**, **OpenAI Codex**, or **local MLX models** — using your existing subscriptions. No API keys needed.

## Why?

Most AI Discord bots require API keys and charge per token. AI Discord Bot uses the CLI tools that come with your Claude and ChatGPT subscriptions, so it costs nothing extra. It also supports local models via Apple MLX for fully offline, free inference.

## Backends

| Backend | CLI Tool | What It Does | Subscription |
|---|---|---|---|
| **Claude** | [Claude Code](https://docs.anthropic.com/en/docs/claude-code) | Full AI agent — chat, code, file editing, bash, image analysis | Claude Max/Pro |
| **Codex** | [OpenAI Codex](https://github.com/openai/codex) | Coding agent — code generation, review, file editing | ChatGPT Pro/Plus |
| **MLX** | [mlx-vlm](https://github.com/Blaizzy/mlx-vlm) | Local model inference on Apple Silicon | Free (runs locally) |

## Setup

### 1. Create a Discord Bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. Create a new application, add a bot
3. Enable **Message Content Intent** under Bot settings
4. Invite to your server with `bot` + `applications.commands` scopes and `Send Messages` + `Read Message History` permissions
5. Copy the bot token

### 2. Install

```bash
git clone https://github.com/YOUR_USERNAME/ai-discord-bot.git
cd ai-discord-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure

```bash
cp .env.example .env
# Edit .env with your bot token and Discord user ID
```

### 4. Install AI CLIs

You need at least one backend installed. Each one connects to your existing subscription — no API keys, no per-token billing.

#### Claude Code (requires Claude Max or Pro subscription)

```bash
# Install Node.js first if you don't have it
# macOS: brew install node
# Linux: https://nodejs.org/

# Install Claude Code globally
npm install -g @anthropic-ai/claude-code

# Authenticate — this opens a browser window to log in with your Anthropic account
claude login

# Verify it works
claude --version
claude -p "say hello"
```

After `claude login`, your session token is stored locally. The bot calls `claude` via subprocess — as long as the CLI works in your terminal, it works in the bot.

**Troubleshooting:**
- If `claude` isn't found after install, make sure your npm global bin is in your PATH (`npm bin -g`)
- If auth expires, run `claude login` again on the host machine
- Claude Code requires an active Claude Max ($100/mo) or Pro ($20/mo) subscription with CLI access enabled

#### OpenAI Codex (requires ChatGPT Pro or Plus subscription)

```bash
# Install Codex CLI globally
npm install -g @openai/codex

# Authenticate — opens a browser to log in with your OpenAI/ChatGPT account
codex login

# Verify it works
codex exec "say hello"
```

After `codex login`, your auth is stored locally. The bot uses `codex exec` (non-interactive mode) to run prompts and get responses.

**Important — model availability on ChatGPT Plus ($20/mo):**
- The default model (`gpt-5.3-codex`) works out of the box — this is the code-specialized model
- API-tier model IDs like `gpt-5-3-codex` and `gpt-5-3.1-codex-max` do **not** work on ChatGPT accounts — you'll get `"model is not supported when using Codex with a ChatGPT account"`
- Just use the default — don't pass a custom model flag and it picks `gpt-5.3-codex` automatically

**Important — skip the MCP server:**
OpenAI provides a Codex MCP server (`codex mcp-server`) that theoretically lets other AI tools call Codex as a tool. In practice, it's unreliable — simple prompts timeout after 5+ minutes and error messages are unclear. This bot uses the direct CLI approach (`codex exec`) instead, which is fast and reliable. Don't bother with the MCP server.

**Troubleshooting:**
- If `codex` isn't found, check your PATH (`npm bin -g`)
- If auth expires, run `codex login` again on the host machine
- Codex needs to run inside a git repository — the bot's working directory must be a git repo (or use the `in ~/path:` command to point at one)

#### MLX (optional, Apple Silicon only)

For fully offline, free local inference on Mac with Apple Silicon:

```bash
# Install mlx-vlm
pip install mlx-vlm

# Download a model (this is ~15GB for the 26B model)
huggingface-cli download mlx-community/gemma-4-26b-a4b-it-4bit

# Start the MLX server
./start-mlx.sh
# Or with pm2:
pm2 start start-mlx.sh --name mlx-server
```

The MLX server runs on `localhost:8800` by default. The bot sends HTTP requests to it. No subscription needed — runs entirely on your GPU.

**Note:** Large models (26B+) need significant RAM. If you're running other services on the same machine, consider a smaller model or monitor memory usage.

### 5. Run

```bash
source venv/bin/activate
python bot.py
```

Or with pm2 for auto-restart:
```bash
pm2 start bot.py --name ai-discord-bot --interpreter python3
pm2 save
```

## Usage

Message the bot in DMs or @mention it in a channel.

### Commands

| Command | What It Does |
|---|---|
| `use claude` / `use codex` / `use mlx` | Switch AI backend |
| `use sonnet` / `use opus` / `use haiku` | Switch Claude model |
| `use gpt5` / `use codex` / `use o3` | Switch Codex model |
| `use gemma4` | Switch to local Gemma 4 |
| `status` | Show current backend, model, and session |
| `new session` / `reset` | Start fresh conversation |
| `save memory` | Save session summary to disk |
| `sessions` | List all active sessions |
| `brief <project>` | Claude writes a project brief from the conversation |
| `build <project>` | Build from the brief using the current backend |
| `review <project>` | Claude reviews the build against the brief |
| `in ~/path: prompt` | Run in a specific directory |
| `help` | Show all commands |

### Project Handoff (brief / build / review)

Plan a project with Claude, then hand it off to any backend to build:

```
You:     "I want a REST API for tracking expenses with auth and categories..."
Claude:  [discusses architecture, tech stack, etc.]
You:     brief expense-tracker
Claude:  [writes ~/projects/expense-tracker/BRIEF.md]
You:     use codex
You:     build expense-tracker
Codex:   [reads BRIEF.md, builds everything]
You:     use claude
You:     review expense-tracker
Claude:  [reviews code against the brief, pass/fail per requirement]
```

The brief is a plain markdown file — any backend can read it anytime.

### Session Persistence

- **Claude**: Full session persistence via `--session-id` / `--resume`
- **Codex**: Thread-based session resume via `codex exec resume`
- **MLX**: In-memory conversation history (resets on bot restart)

### Image Support

Attach images to your message — Claude analyzes them via its Read tool, Codex uses its native `-i` flag.

## Architecture

```
Discord DM/Channel
        |
    AI Discord Bot (bot.py)
        |
   ┌────┼────────────┐
   |    |             |
Claude  Codex    MLX Server
 Code    CLI    (localhost:8800)
   |    |             |
 Your   Your     Local GPU
 Sub    Sub      (Apple Silicon)
```

Single Python file. No framework. Just discord.py + subprocess calls to CLI tools + HTTP to local MLX server.

## Error Handling

The bot handles failures gracefully and reports errors back to Discord:

- **CLI not installed**: If `claude` or `codex` isn't found in PATH, the subprocess will fail and the bot sends the error message to the channel. Install the missing CLI and try again.
- **Subscription expired / auth issues**: The CLI tools will return an error about authentication. You'll see the stderr output in Discord. Run `claude login` or `codex login` on the host machine to re-authenticate.
- **MLX server not running**: If the MLX backend is selected but the server isn't running on the configured port, you'll get a connection error in Discord. Start it with `pm2 start mlx-server` or `./start-mlx.sh`.
- **Timeouts**: All backends have a 10-minute timeout (5 minutes for MLX). If a task takes longer, the process is killed and the bot reports a timeout.
- **No fallback between backends**: If one backend fails, the bot does not automatically try another. Switch manually with `use claude` / `use codex`.

## Security

This bot runs AI agents with **full access to your machine** — file read/write, bash commands, network access. This is by design (it's the whole point), but understand the implications:

- **Allowed users only**: Only Discord user IDs listed in `ALLOWED_USER_IDS` can interact with the bot. Everyone else is silently ignored.
- **No input sanitization on prompts**: Prompts are passed directly to CLI tools. The CLI tools themselves handle sandboxing and safety — the bot does not add an additional layer.
- **Directory override (`in ~/path:`)**: This validates that the path exists via `os.path.isdir()` before using it, but does not restrict which directories are accessible. The AI agent already has full filesystem access regardless of the working directory.
- **Run on a trusted network**: This bot is designed to run on your own machine, accessed via your own Discord server. Do not add untrusted users to `ALLOWED_USER_IDS`.
- **Bot token**: Keep your `.env` file secure. Anyone with the bot token can impersonate the bot (though they still can't trigger AI commands without being in `ALLOWED_USER_IDS`).

## Requirements

- Python 3.10+
- macOS (for MLX backend) or Linux (Claude + Codex only)
- Active Claude and/or ChatGPT subscription with CLI access
- Discord bot token

## License

MIT
