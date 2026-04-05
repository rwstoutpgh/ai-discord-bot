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

**Claude Code** (requires Claude Max or Pro subscription):
```bash
# Install via Claude Desktop app or:
npm install -g @anthropic-ai/claude-code
claude login
```

**OpenAI Codex** (requires ChatGPT Pro or Plus subscription):
```bash
npm install -g @openai/codex
codex login
```

**MLX** (optional, Apple Silicon only):
```bash
pip install mlx-vlm
# Download a model:
huggingface-cli download mlx-community/gemma-4-26b-a4b-it-4bit
# Start the server:
./start-mlx.sh
```

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
    AI Discord Bot Bot (bot.py)
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

## Requirements

- Python 3.10+
- macOS (for MLX backend) or Linux (Claude + Codex only)
- Active Claude and/or ChatGPT subscription with CLI access
- Discord bot token

## License

MIT
