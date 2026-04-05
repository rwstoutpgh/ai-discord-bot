#!/usr/bin/env python3
"""
AI Discord Bot — Multi-backend AI Discord bot.

Routes messages to Claude Code, OpenAI Codex, or local MLX models via CLI/API.
Uses your existing subscriptions — no API keys needed for Claude or Codex.

Session support: each DM/channel gets a persistent conversation session.
"""

import discord
import asyncio
import json
import os
import signal
import sys
import aiohttp
import uuid
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# --- Config (from .env) ---
BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
ALLOWED_USERS = {int(uid) for uid in os.environ["ALLOWED_USER_IDS"].split(",")}
MAX_MSG_LEN = int(os.getenv("MAX_MSG_LEN", "1900"))
WORKING_DIR = os.getenv("WORKING_DIR", os.path.expanduser("~"))
IMAGE_DIR = os.getenv("IMAGE_DIR", "/tmp/discord-bot-images")
DATA_DIR = os.getenv("DATA_DIR", os.path.dirname(os.path.abspath(__file__)))
SESSIONS_FILE = os.path.join(DATA_DIR, "sessions.json")
CODEX_SESSIONS_FILE = os.path.join(DATA_DIR, "codex_sessions.json")
SESSION_LOGS_DIR = os.path.join(DATA_DIR, "session-logs")

# Backend config
current_backend = os.getenv("DEFAULT_BACKEND", "claude")  # "claude", "codex", or "mlx"

# Claude models
CLAUDE_MODELS = {
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}
current_model = os.getenv("DEFAULT_CLAUDE_MODEL", "sonnet")

# Codex models
CODEX_MODELS = {
    "gpt5": "gpt-5.4",
    "gpt5-mini": "gpt-5.4-mini",
    "codex": "gpt-5.3-codex",
    "codex-mini": "gpt-5.3-codex-mini",
    "codex-max": "gpt-5-3.1-codex-max",
    "o3": "o3",
}
current_codex_model = os.getenv("DEFAULT_CODEX_MODEL", "codex")

# MLX local models
MLX_MODELS = {
    "gemma4": "mlx-community/gemma-4-26b-a4b-it-4bit",
}
current_mlx_model = os.getenv("DEFAULT_MLX_MODEL", "gemma4")
MLX_URL = os.getenv("MLX_URL", "http://localhost:8800")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

SAVE_MEMORY_PROMPT = (
    "Summarize this entire conversation into a memory file. Include:\n"
    "1. What was discussed and decided\n"
    "2. What actions were taken (files changed, commands run, configs modified)\n"
    "3. Any unfinished work or next steps\n"
    "4. Key context the user would want preserved\n\n"
    "Write the summary to {filepath} using the Write tool. "
    "Use markdown format with clear headers. Be thorough but concise — "
    "this will be the only record of this conversation."
)

# --- Session Management ---
# Claude sessions: maps channel_id (str) -> {"session_id": uuid, "created": timestamp, "name": str}
sessions = {}
# Codex sessions: maps channel_id (str) -> {"thread_id": uuid, "created": timestamp, "name": str}
codex_sessions = {}


def load_sessions():
    global sessions, codex_sessions
    try:
        with open(SESSIONS_FILE, "r") as f:
            sessions = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        sessions = {}
    try:
        with open(CODEX_SESSIONS_FILE, "r") as f:
            codex_sessions = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        codex_sessions = {}


def save_sessions():
    with open(SESSIONS_FILE, "w") as f:
        json.dump(sessions, f, indent=2)


def save_codex_sessions():
    with open(CODEX_SESSIONS_FILE, "w") as f:
        json.dump(codex_sessions, f, indent=2)


def get_or_create_session(channel_id, channel_name=""):
    """Get existing session for a channel, or create a new one."""
    key = str(channel_id)
    if key not in sessions:
        sessions[key] = {
            "session_id": str(uuid.uuid4()),
            "created": datetime.now().isoformat(),
            "name": channel_name or f"channel-{channel_id}",
            "message_count": 0,
        }
        save_sessions()
    return sessions[key]


def new_session(channel_id, channel_name=""):
    """Create a fresh session for a channel."""
    key = str(channel_id)
    sessions[key] = {
        "session_id": str(uuid.uuid4()),
        "created": datetime.now().isoformat(),
        "name": channel_name or f"channel-{channel_id}",
        "message_count": 0,
    }
    save_sessions()
    return sessions[key]


def find_claude():
    """Find the Claude Code CLI binary."""
    # macOS: check Claude Desktop bundled CLI
    claude_code_dir = os.path.expanduser(
        "~/Library/Application Support/Claude/claude-code"
    )
    if os.path.isdir(claude_code_dir):
        claude_dirs = sorted(
            [d for d in os.listdir(claude_code_dir)
             if os.path.isdir(os.path.join(claude_code_dir, d))],
            reverse=True,
        )
        if claude_dirs:
            path = os.path.join(
                claude_code_dir, claude_dirs[0],
                "claude.app/Contents/MacOS/claude",
            )
            if os.path.exists(path):
                return path

    # Check common install paths
    for p in ["/opt/homebrew/bin/claude", "/usr/local/bin/claude"]:
        if os.path.exists(p):
            return p

    # Fall back to PATH
    return "claude"


CLAUDE_BIN = find_claude()

# --- Discord Bot ---
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
client = discord.Client(intents=intents)


def split_message(text, limit=MAX_MSG_LEN):
    """Split text into Discord-safe chunks."""
    chunks = []
    while len(text) > limit:
        idx = text.rfind('\n', 0, limit)
        if idx == -1:
            idx = limit
        chunks.append(text[:idx])
        text = text[idx:].lstrip('\n')
    if text:
        chunks.append(text)
    return chunks


async def download_attachment(attachment):
    """Download a Discord attachment to local disk."""
    filename = f"{uuid.uuid4().hex[:8]}_{attachment.filename}"
    filepath = os.path.join(IMAGE_DIR, filename)

    async with aiohttp.ClientSession() as session:
        async with session.get(attachment.url) as resp:
            if resp.status == 200:
                with open(filepath, 'wb') as f:
                    f.write(await resp.read())
                return filepath
    return None


# ---------------------------------------------------------------------------
# Backend: Claude Code CLI
# ---------------------------------------------------------------------------

async def run_claude(prompt, channel, working_dir=None, image_paths=None):
    """Run Claude Code CLI and send results back to Discord."""
    global current_model
    cwd = working_dir or WORKING_DIR

    env = os.environ.copy()
    env["PATH"] = (
        "/opt/homebrew/bin:/opt/homebrew/sbin:"
        "/Applications/Docker.app/Contents/Resources/bin:"
        + os.path.dirname(CLAUDE_BIN) + ":"
        + env.get("PATH", "")
    )
    env["HOMEBREW_PREFIX"] = "/opt/homebrew"
    env["HOMEBREW_CELLAR"] = "/opt/homebrew/Cellar"

    model_id = CLAUDE_MODELS.get(current_model, CLAUDE_MODELS["sonnet"])

    # Get or create session for this channel
    channel_name = getattr(channel, 'name', None) or f"dm-{channel.id}"
    session = get_or_create_session(channel.id, channel_name)
    session_id = session["session_id"]

    # Build prompt with image references
    full_prompt = prompt
    if image_paths:
        image_refs = "\n".join(f"[Attached image: {p}]" for p in image_paths)
        full_prompt = (
            f"{prompt}\n\n"
            f"The user attached the following image(s). "
            f"Use the Read tool to view them:\n{image_refs}"
        )

    is_new = session.get("message_count", 0) == 0

    cmd = [
        CLAUDE_BIN,
        "--print",
        "--output-format", "text",
        "--max-turns", "50",
        "--model", model_id,
    ]

    if is_new:
        cmd += ["--session-id", session_id]
    else:
        cmd += ["--resume", session_id]

    cmd += ["-p", full_prompt]

    thinking_msg = await channel.send(
        f"\U0001f9e0 Thinking... (`{current_model}` | session: `{session_id[:8]}...`)"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=600,
        )

        output = stdout.decode("utf-8", errors="replace").strip()
        errors = stderr.decode("utf-8", errors="replace").strip()

        session["message_count"] = session.get("message_count", 0) + 1
        save_sessions()

        try:
            await thinking_msg.delete()
        except:
            pass

        if not output and errors:
            output = f"\u26a0\ufe0f Error:\n```\n{errors[:1500]}\n```"
        elif not output:
            output = "\u2705 Done (no output)"

        chunks = split_message(output)
        for i, chunk in enumerate(chunks):
            await channel.send(chunk)
            if i < len(chunks) - 1:
                await asyncio.sleep(0.5)

    except asyncio.TimeoutError:
        try:
            proc.kill()
        except:
            pass
        try:
            await thinking_msg.delete()
        except:
            pass
        await channel.send("\u23f0 Timed out after 10 minutes.")

    except Exception as e:
        try:
            await thinking_msg.delete()
        except:
            pass
        await channel.send(f"\u274c Error: {str(e)[:500]}")

    finally:
        if image_paths:
            for p in image_paths:
                try:
                    os.remove(p)
                except:
                    pass


# ---------------------------------------------------------------------------
# Backend: OpenAI Codex CLI
# ---------------------------------------------------------------------------

async def run_codex(prompt, channel, working_dir=None, image_paths=None):
    """Run OpenAI Codex CLI and send results back to Discord."""
    global current_codex_model
    cwd = working_dir or WORKING_DIR

    env = os.environ.copy()
    env["PATH"] = (
        "/opt/homebrew/bin:/opt/homebrew/sbin:"
        "/Applications/Docker.app/Contents/Resources/bin:"
        + env.get("PATH", "")
    )

    model_id = CODEX_MODELS.get(current_codex_model, CODEX_MODELS["codex"])

    # Check for existing codex session on this channel
    key = str(channel.id)
    codex_session = codex_sessions.get(key)

    # Build command — resume if we have a session, otherwise new
    if codex_session:
        thread_id = codex_session["thread_id"]
        cmd = [
            "codex", "exec", "resume",
            "--skip-git-repo-check",
            "--json",
            "-m", model_id,
            thread_id,
            prompt,
        ]
    else:
        thread_id = None
        cmd = [
            "codex", "exec",
            "--skip-git-repo-check",
            "--json",
            "-m", model_id,
            "-C", cwd,
            prompt,
        ]

    # Attach images natively via -i flags
    if image_paths:
        for p in image_paths:
            cmd.insert(-1, "-i")
            cmd.insert(-1, p)

    session_label = f" | thread: `{thread_id[:8]}...`" if thread_id else ""
    thinking_msg = await channel.send(
        f"\U0001f916 Thinking... (`codex/{current_codex_model}` \u2014 {model_id}{session_label})"
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd,
            env=env,
        )

        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=600,
        )

        raw = stdout.decode("utf-8", errors="replace").strip()
        errors = stderr.decode("utf-8", errors="replace").strip()

        # Parse JSON lines to extract thread_id and agent messages
        output_parts = []
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Capture thread_id from first event
            if event.get("type") == "thread.started" and event.get("thread_id"):
                new_thread_id = event["thread_id"]
                channel_name = getattr(channel, 'name', None) or f"dm-{channel.id}"
                codex_sessions[key] = {
                    "thread_id": new_thread_id,
                    "created": datetime.now().isoformat(),
                    "name": channel_name,
                    "message_count": 0,
                }
                save_codex_sessions()

            # Collect agent message text
            if event.get("type") == "item.completed":
                item = event.get("item", {})
                if item.get("type") == "agent_message" and item.get("text"):
                    output_parts.append(item["text"])

        # Update message count
        if key in codex_sessions:
            codex_sessions[key]["message_count"] = codex_sessions[key].get("message_count", 0) + 1
            save_codex_sessions()

        output = "\n\n".join(output_parts)

        try:
            await thinking_msg.delete()
        except:
            pass

        if not output and errors:
            output = f"\u26a0\ufe0f Error:\n```\n{errors[:1500]}\n```"
        elif not output:
            output = "\u2705 Done (no output)"

        chunks = split_message(output)
        for i, chunk in enumerate(chunks):
            await channel.send(chunk)
            if i < len(chunks) - 1:
                await asyncio.sleep(0.5)

    except asyncio.TimeoutError:
        try:
            proc.kill()
        except:
            pass
        try:
            await thinking_msg.delete()
        except:
            pass
        await channel.send("\u23f0 Timed out after 10 minutes.")

    except Exception as e:
        try:
            await thinking_msg.delete()
        except:
            pass
        await channel.send(f"\u274c Error: {str(e)[:500]}")

    finally:
        if image_paths:
            for p in image_paths:
                try:
                    os.remove(p)
                except:
                    pass


# ---------------------------------------------------------------------------
# Backend: MLX (local models via OpenAI-compatible API)
# ---------------------------------------------------------------------------

# MLX conversation history per channel (in-memory, cleared on restart or new session)
mlx_histories = {}


async def run_mlx(prompt, channel, working_dir=None, image_paths=None):
    """Run a local MLX model via OpenAI-compatible REST API."""
    global current_mlx_model
    model_id = MLX_MODELS.get(current_mlx_model, current_mlx_model)
    key = str(channel.id)

    if key not in mlx_histories:
        mlx_histories[key] = []

    user_msg = {"role": "user", "content": prompt}
    if image_paths:
        image_note = f"\n\n[{len(image_paths)} image(s) attached — image analysis not available on local models]"
        user_msg["content"] = prompt + image_note

    mlx_histories[key].append(user_msg)

    thinking_msg = await channel.send(
        f"\U0001f9e0 Thinking... (`mlx/{current_mlx_model}` \u2014 local)"
    )

    try:
        payload = {
            "model": model_id,
            "messages": mlx_histories[key],
        }

        async with aiohttp.ClientSession() as http_session:
            async with http_session.post(
                f"{MLX_URL}/v1/chat/completions",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    raise Exception(f"MLX server returned {resp.status}: {error_text[:500]}")
                data = await resp.json()

        output = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()

        if output:
            mlx_histories[key].append({"role": "assistant", "content": output})

        try:
            await thinking_msg.delete()
        except:
            pass

        if not output:
            output = "\u2705 Done (no output)"

        chunks = split_message(output)
        for i, chunk in enumerate(chunks):
            await channel.send(chunk)
            if i < len(chunks) - 1:
                await asyncio.sleep(0.5)

    except asyncio.TimeoutError:
        try:
            await thinking_msg.delete()
        except:
            pass
        await channel.send("\u23f0 Timed out after 5 minutes.")

    except Exception as e:
        try:
            await thinking_msg.delete()
        except:
            pass
        await channel.send(f"\u274c Error: {str(e)[:500]}")

    finally:
        if image_paths:
            for p in image_paths:
                try:
                    os.remove(p)
                except:
                    pass


# ---------------------------------------------------------------------------
# Bot commands
# ---------------------------------------------------------------------------

def handle_bot_command(content, channel):
    global current_model, current_backend, current_codex_model, current_mlx_model
    lower = content.lower().strip()

    if lower in ("model", "status"):
        key = str(channel.id)
        claude_session = sessions.get(key)
        claude_info = "none"
        if claude_session:
            msg_count = claude_session.get("message_count", 0)
            created = claude_session.get("created", "unknown")[:10]
            claude_info = f"`{claude_session['session_id'][:8]}...` ({msg_count} msgs, since {created})"

        codex_session = codex_sessions.get(key)
        codex_info = "none"
        if codex_session:
            msg_count = codex_session.get("message_count", 0)
            created = codex_session.get("created", "unknown")[:10]
            codex_info = f"`{codex_session['thread_id'][:8]}...` ({msg_count} msgs, since {created})"

        if current_backend == "claude":
            active_model = f"`{current_model}` ({CLAUDE_MODELS[current_model]})"
        elif current_backend == "codex":
            active_model = f"`codex/{current_codex_model}` ({CODEX_MODELS[current_codex_model]})"
        else:
            active_model = f"`mlx/{current_mlx_model}` (local)"
        claude_list = ', '.join(f'`{m}`' for m in CLAUDE_MODELS)
        codex_list = ', '.join(f'`{m}`' for m in CODEX_MODELS)
        mlx_list = ', '.join(f'`{m}`' for m in MLX_MODELS)

        return (
            f"\U0001f35f **AI Discord Bot Status**\n"
            f"\u2022 Backend: **{current_backend}**\n"
            f"\u2022 Model: {active_model}\n"
            f"\u2022 Claude session: {claude_info}\n"
            f"\u2022 Codex session: {codex_info}\n"
            f"\u2022 Claude models: {claude_list}\n"
            f"\u2022 Codex models: {codex_list}\n"
            f"\u2022 MLX models: {mlx_list}\n"
            f"\u2022 `use claude` / `use codex` / `use mlx` \u2014 switch backend\n"
            f"\u2022 `use <model>` \u2014 switch model within current backend\n"
            f"\u2022 `new session` \u2014 start fresh conversation\n"
            f"\u2022 `sessions` \u2014 list all sessions"
        ), True

    if lower.startswith("use "):
        choice = lower[4:].strip()

        # Backend switches
        if choice in ("claude", "anthropic"):
            current_backend = "claude"
            return (
                f"\U0001f504 Switched to **Claude** backend \u2014 "
                f"model: `{current_model}` ({CLAUDE_MODELS[current_model]})"
            ), True
        if choice in ("codex", "gpt", "openai"):
            current_backend = "codex"
            return (
                f"\U0001f504 Switched to **Codex** backend \u2014 "
                f"model: `{current_codex_model}` ({CODEX_MODELS[current_codex_model]})"
            ), True
        if choice in ("mlx", "local", "gemma", "gemma4"):
            current_backend = "mlx"
            if choice in MLX_MODELS:
                current_mlx_model = choice
            return (
                f"\U0001f504 Switched to **MLX** backend (local) \u2014 "
                f"model: `{current_mlx_model}`"
            ), True

        # Claude model selection
        if choice in CLAUDE_MODELS:
            current_model = choice
            current_backend = "claude"
            return (
                f"\U0001f504 Switched to **{choice}** (`{CLAUDE_MODELS[choice]}`) on Claude"
            ), True

        # Codex model selection
        if choice in CODEX_MODELS:
            current_codex_model = choice
            current_backend = "codex"
            return (
                f"\U0001f504 Switched to **{choice}** (`{CODEX_MODELS[choice]}`) on Codex"
            ), True

        # MLX model selection
        if choice in MLX_MODELS:
            current_mlx_model = choice
            current_backend = "mlx"
            return (
                f"\U0001f504 Switched to **{choice}** on MLX (local)"
            ), True

        all_models = list(CLAUDE_MODELS.keys()) + list(CODEX_MODELS.keys()) + list(MLX_MODELS.keys()) + ["claude", "codex", "mlx"]
        return f"\u274c Unknown: `{choice}`. Try: {', '.join(f'`{m}`' for m in all_models)}", True

    if lower in ("new session", "new", "reset", "fresh", "clear"):
        channel_name = getattr(channel, 'name', None) or f"dm-{channel.id}"
        key = str(channel.id)
        old_session = sessions.get(key)
        session = new_session(channel.id, channel_name)
        if key in codex_sessions:
            del codex_sessions[key]
            save_codex_sessions()
        if key in mlx_histories:
            del mlx_histories[key]
        warning = ""
        if old_session and old_session.get("message_count", 0) > 0:
            warning = "\n\u26a0\ufe0f Tip: use `save memory` before `new session` to preserve context!"
        return (
            f"\U0001f195 **New session started** (all backends)\n"
            f"Claude session: `{session['session_id'][:8]}...`\n"
            f"Previous conversation forgotten. Fresh start!{warning}"
        ), True

    if lower == "sessions":
        if not sessions and not codex_sessions:
            return "No active sessions.", True
        lines = ["\U0001f4cb **Active Sessions**"]
        if sessions:
            lines.append("\n**Claude:**")
            for ch_id, s in sessions.items():
                msg_count = s.get("message_count", 0)
                created = s.get("created", "unknown")[:10]
                name = s.get("name", "unknown")
                current = " \u2190 **current**" if str(channel.id) == ch_id else ""
                lines.append(
                    f"\u2022 **{name}**: `{s['session_id'][:8]}...` "
                    f"({msg_count} msgs, since {created}){current}"
                )
        if codex_sessions:
            lines.append("\n**Codex:**")
            for ch_id, s in codex_sessions.items():
                msg_count = s.get("message_count", 0)
                created = s.get("created", "unknown")[:10]
                name = s.get("name", "unknown")
                current = " \u2190 **current**" if str(channel.id) == ch_id else ""
                lines.append(
                    f"\u2022 **{name}**: `{s['thread_id'][:8]}...` "
                    f"({msg_count} msgs, since {created}){current}"
                )
        return "\n".join(lines), True

    if lower == "help":
        return (
            "\U0001f35f **AI Discord Bot \u2014 Commands**\n"
            "\u2022 Just message me and I'll route it to the active AI backend\n"
            "\u2022 Attach screenshots/images and I'll analyze them\n"
            "\n**Backends & Models:**\n"
            "\u2022 `use claude` / `use codex` / `use mlx` \u2014 switch AI backend\n"
            "\u2022 `use sonnet` / `use opus` / `use haiku` \u2014 Claude models\n"
            "\u2022 `use gpt5` / `use codex` / `use o3` \u2014 Codex models\n"
            "\u2022 `use gemma4` \u2014 local MLX models\n"
            "\u2022 `model` or `status` \u2014 show current config\n"
            "\n**Sessions:**\n"
            "\u2022 **`save memory`** \u2014 save session summary before starting fresh\n"
            "\u2022 `new session` / `reset` / `fresh` \u2014 start new conversation\n"
            "\u2022 `sessions` \u2014 list all active sessions\n"
            "\n**Other:**\n"
            "\u2022 `in ~/path: do something` \u2014 run in a specific directory\n"
            "\u2022 `help` \u2014 this message\n\n"
            "\U0001f4a1 **Workflow:** chat \u2192 `save memory` \u2192 `new session`"
        ), True

    return None, False


# ---------------------------------------------------------------------------
# Discord event handlers
# ---------------------------------------------------------------------------

@client.event
async def on_ready():
    load_sessions()
    os.makedirs(SESSION_LOGS_DIR, exist_ok=True)
    print(f"\U0001f35f AI Discord Bot online as {client.user}")
    print(f"   Claude binary: {CLAUDE_BIN}")
    print(f"   Backend: {current_backend}")
    print(f"   Claude model: {current_model} ({CLAUDE_MODELS[current_model]})")
    print(f"   Codex model: {current_codex_model} ({CODEX_MODELS[current_codex_model]})")
    print(f"   Working dir: {WORKING_DIR}")
    print(f"   Active sessions: {len(sessions)}")
    print(f"   Allowed users: {ALLOWED_USERS}")


@client.event
async def on_message(message):
    if message.author == client.user:
        return

    if message.author.id not in ALLOWED_USERS:
        return

    is_dm = isinstance(message.channel, discord.DMChannel)
    is_mentioned = client.user in message.mentions if not is_dm else False
    is_reply_to_bot = (
        message.reference
        and message.reference.resolved
        and hasattr(message.reference.resolved, 'author')
        and message.reference.resolved.author == client.user
    )

    if not (is_dm or is_mentioned or is_reply_to_bot):
        return

    content = message.content
    if client.user:
        content = content.replace(f'<@{client.user.id}>', '').strip()
        content = content.replace(f'<@!{client.user.id}>', '').strip()

    # Download any image attachments
    image_paths = []
    for att in message.attachments:
        ext = os.path.splitext(att.filename)[1].lower()
        if ext in IMAGE_EXTENSIONS:
            path = await download_attachment(att)
            if path:
                image_paths.append(path)
                print(f"   Downloaded image: {path}")

    if not content and not image_paths:
        await message.channel.send("What do you need?")
        return

    if not content and image_paths:
        content = "What is in this image? Describe what you see."

    # Handle "save memory" — route through Claude to summarize the session
    if content.lower().strip() in ("save memory", "save session", "save"):
        session = sessions.get(str(message.channel.id))
        if not session or session.get("message_count", 0) == 0:
            await message.channel.send("Nothing to save \u2014 this session has no messages yet.")
            return
        timestamp = datetime.now().strftime("%Y-%m-%d_%H%M")
        sid = session["session_id"][:8]
        filename = f"{timestamp}_{sid}.md"
        filepath = os.path.join(SESSION_LOGS_DIR, filename)
        save_prompt = SAVE_MEMORY_PROMPT.format(filepath=filepath)
        await message.channel.send(f"\U0001f4be Saving session memory to `{filename}`...")
        await run_claude(save_prompt, message.channel)
        return

    # Check for bot commands (only if no images)
    if not image_paths:
        response, handled = handle_bot_command(content, message.channel)
        if handled:
            await message.channel.send(response)
            return

    # Check for working directory override
    working_dir = WORKING_DIR
    if content.startswith("in ") and ":" in content:
        dir_part, content = content.split(":", 1)
        dir_path = dir_part[3:].strip()
        dir_path = os.path.expanduser(dir_path)
        if os.path.isdir(dir_path):
            working_dir = dir_path
            content = content.strip()

    img_note = f" + {len(image_paths)} image(s)" if image_paths else ""

    if current_backend == "codex":
        model_tag = f"codex/{current_codex_model}"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [{model_tag}] {message.author}: {content[:100]}...{img_note}")
        await run_codex(content, message.channel, working_dir, image_paths)
    elif current_backend == "mlx":
        model_tag = f"mlx/{current_mlx_model}"
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [{model_tag}] {message.author}: {content[:100]}...{img_note}")
        await run_mlx(content, message.channel, working_dir, image_paths)
    else:
        session = get_or_create_session(message.channel.id)
        print(f"[{datetime.now().strftime('%H:%M:%S')}] [{current_model}] [session:{session['session_id'][:8]}] {message.author}: {content[:100]}...{img_note}")
        await run_claude(content, message.channel, working_dir, image_paths)


def handle_signal(sig, frame):
    print(f"\n\U0001f35f AI Discord Bot shutting down...")
    save_sessions()
    asyncio.get_event_loop().run_until_complete(client.close())
    sys.exit(0)


signal.signal(signal.SIGTERM, handle_signal)
signal.signal(signal.SIGINT, handle_signal)

if __name__ == "__main__":
    os.makedirs(IMAGE_DIR, exist_ok=True)
    load_sessions()
    print("\U0001f35f Starting AI Discord Bot...")
    client.run(BOT_TOKEN)
