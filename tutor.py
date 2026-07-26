#!/usr/bin/env python3
"""
tutor.py - a small, safe Linux helper agent for people new to the terminal.

Built for workshops. Three design goals, in order:

  1. SAFE      Destructive commands are refused outright. Anything that changes
               the system is shown and explained, then needs a yes.
  2. CHEAP     Four small tools instead of a coding agent's twenty. ~900 tokens
               of overhead per turn instead of ~25,000, so it runs happily on
               Groq's free tier where full agents get a 413.
  3. TEACHING  It says what a command does and why, so students learn the shell
               instead of just watching an AI use it.

Usage
-----
    ./tutor.py                        chat (this is the normal way to use it)
    ./tutor.py "how do I find big files?"
    ./tutor.py --yes "..."            auto-run safe read-only commands
    ./tutor.py --dry-run "..."        never execute anything, just explain

Inside chat: /quit  /status  /reset  /cost  /help

Provider routing, quota tracking and failover all come from smart_llm.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

import smart_llm

# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------
# Never run, no matter what the model or the student says.
FORBIDDEN = [
    (r"\brm\s+(-[a-zA-Z]*\s+)*(-[a-zA-Z]*r[a-zA-Z]*f|-[a-zA-Z]*f[a-zA-Z]*r)\s+/\s*$", "recursive delete of /"),
    (r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*\s+(/|~|\$HOME)(\s|/\*|$)", "recursive delete of your home or root"),
    (r"\bmkfs(\.\w+)?\b", "formats a filesystem"),
    (r"\bdd\b.*\bof=/dev/(sd|nvme|hd)", "overwrites a raw disk"),
    (r">\s*/dev/(sd|nvme|hd)\w", "writes directly to a disk device"),
    (r":\(\)\s*\{.*\|.*&.*\}\s*;?\s*:", "fork bomb"),
    (r"\bchmod\s+-R\s+777\s+/\s*$", "makes the whole system world-writable"),
    (r"\bchown\s+-R\s+.*\s+/\s*$", "changes ownership of the whole system"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "shuts the machine down"),
    (r"\b(curl|wget)\b[^|]*\|\s*(sudo\s+)?(ba)?sh", "pipes an internet script straight into a shell"),
    (r"\bhistory\s+-c\b", "erases your shell history"),
    # Beginners should not be running admin commands through an AI at all.
    (r"(^|[\s;|&])(sudo|doas|su)(\s|$)", "needs administrator rights, which this tutor will not use"),
    (r"/dev/urandom.*>\s*/dev/", "overwrites a device with random data"),
]

# Safe to run without asking: they only look, never change anything.
READ_ONLY = {
    "ls", "pwd", "cd", "cat", "less", "more", "head", "tail", "wc", "file",
    "stat", "du", "df", "free", "uname", "whoami", "id", "groups", "date",
    "uptime", "ps", "top", "env", "printenv", "which", "type", "whereis",
    "find", "grep", "egrep", "fgrep", "locate", "tree", "diff", "sort",
    "uniq", "cut", "awk", "sed", "echo", "man", "help", "history", "hostname",
    "lscpu", "lsblk", "lsusb", "nproc", "python3", "python", "pip", "conda",
    "git", "realpath", "basename", "dirname", "readlink", "column", "nl",
}

SYSTEM_PROMPT = """You are a patient Linux tutor helping a complete beginner in a terminal.

Rules:
- Use run_command to actually check things rather than guessing. Prefer looking
  before advising.
- Explain in plain language what a command does BEFORE you run it. One or two
  short sentences, no jargon dumps.
- When a command fails, read the error and explain the cause in beginner terms,
  then give the fix.
- Never suggest sudo, package installation, or anything that changes system
  files. If a task needs that, say so and stop.
- Keep answers short. The student is at a terminal, not reading a manual.
"""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Run a shell command and get its output. Use for looking at the system, running scripts, and checking whether something worked.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "the shell command"},
                    "why": {"type": "string", "description": "one short sentence, for the student, on what this does"},
                },
                "required": ["command", "why"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a text file.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a text file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                    "why": {"type": "string", "description": "one short sentence on what this file is for"},
                },
                "required": ["path", "content", "why"],
            },
        },
    },
]

# Measured 2026-07-26: llama-3.3-70b on Groq emits malformed tool calls 2 times
# in 3 (it prints "<function/run_command ...>" as text instead of calling it).
# Gemini, NVIDIA and Mistral were clean 3/3, so the agent prefers those. Groq
# is still fine for plain question-answering, which is what `ai <question>` uses.
# Agent precedence, chosen from measurements rather than headline quota:
#   mistral  2s per task, ~1B tokens/month  -> the default
#   nvidia   no daily cap, but latency swung from 8s to 58s -> backstop only
#   gemini   flawless at tool calls but just 20 requests/day/model -> last resort
# Groq is excluded on purpose: fast and huge quota, but llama-3.3-70b produced
# malformed tool calls in 2 of 3 runs. It still serves all plain `ai` questions.
TOOL_CAPABLE = ["mistral", "nvidia", "gemini-flash-lite", "gemini-flash-lite-2",
                "cohere", "gemini-flash"]

LEAKED_CALL = re.compile(r"<function|<tool_call|^\s*\{\s*\"(command|path)\"", re.MULTILINE)

C_ASK = "\033[93m"; C_RUN = "\033[96m"; C_BAD = "\033[91m"; C_OK = "\033[92m"; C_DIM = "\033[90m"; C_OFF = "\033[0m"


def _stages(cmd: str) -> list[list[str]]:
    """Split a command line into pipeline/sequence stages, tokenised."""
    out = []
    for stage in re.split(r"\||;|&&|\|\||&", cmd):
        try:
            toks = shlex.split(stage)
        except ValueError:
            toks = stage.split()
        if toks:
            out.append(toks)
    return out


def _reckless_rm(cmd: str) -> bool:
    """A recursive rm aimed at an absolute path or a home directory.

    Regexes kept missing variants like `sudo rm -rf /home/sai`, so check the
    parsed tokens instead: any -r flag plus any target outside the current tree.
    """
    for toks in _stages(cmd):
        i = 1 if toks and toks[0] in ("sudo", "doas") else 0
        if len(toks) <= i or toks[i] != "rm":
            continue
        flags = [t for t in toks[i + 1:] if t.startswith("-")]
        targets = [t for t in toks[i + 1:] if not t.startswith("-")]
        if not any("r" in f.lower() for f in flags):
            continue
        for t in targets:
            expanded = os.path.expandvars(os.path.expanduser(t))
            if expanded.startswith("/") or t.strip("'\"") in ("~", "$HOME", "*"):
                return True
    return False


def is_forbidden(cmd: str) -> str | None:
    if _reckless_rm(cmd):
        return "recursively deletes an absolute path - far too easy to get wrong"
    for pattern, reason in FORBIDDEN:
        if re.search(pattern, cmd, re.IGNORECASE):
            return reason
    return None


def is_read_only(cmd: str) -> bool:
    """True only if every stage of the pipeline is a known harmless command."""
    if re.search(r"[>]{1,2}[^&]|(^|\s)sudo\s|`|\$\(", cmd):
        return False                       # redirects out, sudo, command substitution
    for stage in re.split(r"\||;|&&|\|\|", cmd):
        try:
            parts = shlex.split(stage)
        except ValueError:
            return False
        if not parts:
            continue
        if parts[0] not in READ_ONLY:
            return False
        if parts[0] in ("git",) and len(parts) > 1 and parts[1] not in (
                "status", "log", "diff", "branch", "show", "remote", "config"):
            return False
    return True


def confirm(prompt: str) -> bool:
    try:
        return input(f"{C_ASK}{prompt} [y/N] {C_OFF}").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


# ---------------------------------------------------------------------------
# Tool implementations
# ---------------------------------------------------------------------------

def do_run_command(args: dict, opts) -> str:
    cmd = (args.get("command") or "").strip()
    why = (args.get("why") or "").strip()
    if not cmd:
        return "ERROR: no command given"

    reason = is_forbidden(cmd)
    if reason:
        print(f"{C_BAD}  BLOCKED: {cmd}{C_OFF}\n{C_BAD}  ({reason}){C_OFF}")
        return f"REFUSED - this command {reason}. Do not try to work around this; suggest a safe alternative instead."

    safe = is_read_only(cmd)
    if why:
        print(f"{C_DIM}  {why}{C_OFF}")
    print(f"{C_RUN}  $ {cmd}{C_OFF}")

    if opts.dry_run:
        return "DRY RUN - not executed. Tell the student what this would have done."
    if not safe and not opts.yes:
        if not confirm("  This changes something. Run it?"):
            return "The student declined to run this. Explain what it would have done, or offer another way."
    elif safe and not (opts.yes or opts.auto_safe):
        pass                               # read-only: run without asking

    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           timeout=opts.command_timeout, cwd=os.getcwd())
    except subprocess.TimeoutExpired:
        return f"TIMEOUT after {opts.command_timeout}s"
    except Exception as e:                                  # noqa: BLE001
        return f"ERROR running command: {e}"

    out = (r.stdout or "") + (("\n[stderr]\n" + r.stderr) if r.stderr else "")
    out = out.strip() or "(no output)"
    if len(out) > 4000:                    # protect the token budget
        out = out[:4000] + f"\n... [truncated, {len(out)} chars total]"
    for line in out.splitlines()[:15]:
        print(f"{C_DIM}  | {line}{C_OFF}")
    if len(out.splitlines()) > 15:
        print(f"{C_DIM}  | ... ({len(out.splitlines())} lines){C_OFF}")
    return f"exit code {r.returncode}\n{out}"


def do_read_file(args: dict, opts) -> str:
    path = Path(os.path.expanduser(args.get("path", "")))
    print(f"{C_RUN}  read {path}{C_OFF}")
    try:
        text = path.read_text(errors="replace")
    except OSError as e:
        return f"ERROR: {e}"
    if len(text) > 6000:
        text = text[:6000] + "\n... [truncated]"
    return text or "(empty file)"


def do_write_file(args: dict, opts) -> str:
    path = Path(os.path.expanduser(args.get("path", "")))
    content = args.get("content", "")
    why = args.get("why", "")
    if why:
        print(f"{C_DIM}  {why}{C_OFF}")
    action = "overwrite" if path.exists() else "create"
    print(f"{C_RUN}  {action} {path} ({len(content)} bytes){C_OFF}")
    if opts.dry_run:
        return "DRY RUN - file not written."
    if not opts.yes and not confirm(f"  {action.capitalize()} this file?"):
        return "The student declined. Do not write the file."
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    except OSError as e:
        return f"ERROR: {e}"
    return f"Wrote {path} ({len(content)} bytes)."


HANDLERS = {"run_command": do_run_command, "read_file": do_read_file, "write_file": do_write_file}


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

def turn(history: list[dict], opts, budget: dict) -> None:
    """One student turn: let the model call tools until it answers in words."""
    for _ in range(opts.max_steps):
        try:
            message, meta = smart_llm.ask(
                None,                       # history already holds the new user turn
                system=None,
                history=history,
                tools=TOOLS,
                raw=True,
                return_meta=True,
                only=opts.provider,
                order=TOOL_CAPABLE,
                exclude=opts.exclude,
                max_tokens=opts.max_tokens,
                timeout=opts.timeout,
                quiet=True,
            )
        except RuntimeError as e:
            print(f"{C_BAD}{e}{C_OFF}")
            return

        budget["tokens"] += meta["tokens"]
        budget["calls"] += 1
        budget["provider"] = meta["provider"]

        calls = getattr(message, "tool_calls", None) or []
        entry = {"role": "assistant", "content": message.content or ""}
        if calls:
            entry["tool_calls"] = [
                {"id": c.id, "type": "function",
                 "function": {"name": c.function.name, "arguments": c.function.arguments}}
                for c in calls
            ]
        history.append(entry)

        leaked = bool(message.content and LEAKED_CALL.search(message.content))
        if message.content and not leaked:
            print(f"\n{message.content.strip()}\n")

        if not calls:
            if leaked:
                # The model tried to call a tool but wrote it as text. Drop the
                # bad turn and let it try again rather than showing raw markup.
                history.pop()
                history.append({"role": "user", "content":
                                "That was not a valid tool call. Use the tools properly."})
                continue
            return

        for call in calls:
            name = call.function.name
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            handler = HANDLERS.get(name)
            result = handler(args, opts) if handler else f"ERROR: no tool named {name}"
            history.append({"role": "tool", "tool_call_id": call.id,
                            "name": name, "content": str(result)})

    print(f"{C_BAD}Stopped after {opts.max_steps} steps. Ask a smaller question.{C_OFF}")


def main(argv=None, prog="tutor.py") -> int:
    smart_llm.load_env_files()
    ap = argparse.ArgumentParser(
        prog=prog,
        description="Interactive Linux helper. It can list your files, read them, "
                    "run commands and react to what it finds. Just describe what "
                    "went wrong. Inside: /quit /reset /cost /status",
        epilog="Examples:\n"
               f"  {prog}\n"
               f"  {prog} my script gives command not found\n"
               f"  {prog} --dry-run explain what would fix this\n",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="*")
    ap.add_argument("--yes", action="store_true",
                    help="don't ask before commands that change things (not for beginners)")
    ap.add_argument("--dry-run", action="store_true", help="explain everything, execute nothing")
    ap.add_argument("--auto-safe", action="store_true", default=True,
                    help="run read-only commands without asking (default)")
    ap.add_argument("-p", "--provider", action="append", help="restrict to these providers")
    ap.add_argument("-x", "--exclude", action="append", default=[])
    ap.add_argument("-n", "--max-tokens", type=int, default=1000)
    ap.add_argument("--max-steps", type=int, default=8, help="tool calls per question")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--command-timeout", type=float, default=30.0)
    opts = ap.parse_args(argv)

    if not opts.provider:
        reliable = smart_llm.candidates(smart_llm.load_providers(), only=TOOL_CAPABLE)
        opts.provider = TOOL_CAPABLE if reliable else None   # fall back if no keys

    history = [{"role": "system", "content": SYSTEM_PROMPT}]
    budget = {"tokens": 0, "calls": 0, "provider": "-"}

    if opts.question:
        history.append({"role": "user", "content": " ".join(opts.question)})
        turn(history, opts, budget)
        print(f"{C_DIM}[{budget['provider']}, {budget['calls']} calls, {budget['tokens']} tokens]{C_OFF}")
        return 0

    print(f"{C_OK}Linux tutor.{C_OFF} Ask anything about the terminal. "
          f"{C_DIM}/help for commands, /quit to leave.{C_OFF}")
    if opts.dry_run:
        print(f"{C_DIM}(dry run: nothing will actually be executed){C_OFF}")
    while True:
        try:
            line = input(f"\n{C_OK}you>{C_OFF} ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            break
        if line == "/help":
            print("  /quit  /reset  /status  /cost\n"
                  "  Ask things like: where am I? / what's taking up disk space? /\n"
                  "  I typed a command and got an error, what does it mean?")
            continue
        if line == "/reset":
            history = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("  (conversation cleared)")
            continue
        if line == "/status":
            smart_llm.main(["--status"])
            continue
        if line == "/cost":
            print(f"  {budget['calls']} API calls, {budget['tokens']} tokens this session "
                  f"(last provider: {budget['provider']})")
            continue
        history.append({"role": "user", "content": line})
        turn(history, opts, budget)

    print(f"{C_DIM}Session total: {budget['calls']} calls, {budget['tokens']} tokens.{C_OFF}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
