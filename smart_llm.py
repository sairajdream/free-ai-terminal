#!/usr/bin/env python3
"""
smart_llm.py - auto-switching free-tier LLM client for the terminal.

Keeps a local ledger of how much of each provider's free quota you have spent
(requests/minute, requests/day, tokens/minute, tokens/hour, tokens/day) and
routes each request to the first provider in priority order that still has
room. When a provider says 429 it is put on a cooldown and the next one takes
over, so you never sit there watching a rate-limit error.

Install
-------
    pip install openai --break-system-packages

Keys
----
Export whichever you have (all optional - unset providers are skipped):

    export GROQ_API_KEY=...          # console.groq.com
    export CEREBRAS_API_KEY=...      # cloud.cerebras.ai
    export GEMINI_API_KEY=...        # aistudio.google.com/apikey
    export NVIDIA_API_KEY=...        # build.nvidia.com
    export GITHUB_TOKEN=...          # github.com/settings/tokens (any classic PAT)
    export MISTRAL_API_KEY=...       # console.mistral.ai
    export OPENROUTER_API_KEY=...    # openrouter.ai/keys
    export COHERE_API_KEY=...        # dashboard.cohere.com
    export CLOUDFLARE_API_KEY=... CLOUDFLARE_ACCOUNT_ID=...

Or put them in a `.env` file next to this script, or ~/.config/smart_llm/env.

Usage
-----
    ./smart_llm.py "why is my pw.x run not reaching scf convergence?"
    cat pw.out | ./smart_llm.py "explain the error at the end"
    ./smart_llm.py -f pw.in -f pw.out "why did this fail?"
    ./smart_llm.py --status                 # quota ledger
    ./smart_llm.py --list                   # configured providers
    ./smart_llm.py --test                   # send a 1-token ping to each
    ./smart_llm.py --chat                   # interactive session
    ./smart_llm.py -p gemini --long "..."   # force a provider / prefer big context

As a library:
    from smart_llm import ask
    text = ask("explain this pwtk error: ...", system="You are terse.")

Adding providers
----------------
Anything with an OpenAI-compatible endpoint works. Either edit PROVIDERS below
or drop a providers.json next to this script (see --dump-config for the format);
entries there are merged over the built-ins by name.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Provider table
# ---------------------------------------------------------------------------
# Limits below are the documented free tiers as of July 2026. They are checked
# locally *before* a request goes out, so the numbers only need to be roughly
# right - a 429 is caught anyway and simply moves you to the next provider.
#
#   priority  lower number = tried first
#   rpm/rpd   requests per rolling minute / per calendar day
#   tpm/tph/tpd  tokens per rolling minute / rolling hour / calendar day
#   ctx       usable context, used by --long to pick a big-context provider
#   any limit set to None or 0 means "not enforced locally"
#
# Note: Groq and Google count quota PER MODEL, so listing two models from the
# same provider genuinely doubles what you get.

PROVIDERS = [
    {
        "name": "groq-llama33",
        "priority": 10,
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "model": "llama-3.3-70b-versatile",
        "rpm": 30, "rpd": 1000, "tpm": 12000, "tpd": 100000,
        "ctx": 128000,
        "notes": "fastest general model; per-model daily cap is small",
    },
    {
        "name": "groq-oss120b",
        "priority": 11,
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "model": "openai/gpt-oss-120b",
        "rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000,
        "ctx": 131000,
        "min_max_tokens": 512,   # reasoning model: needs room before it answers
        "notes": "separate quota bucket from llama-3.3 on the same key",
    },
    {
        "name": "cerebras",
        "priority": 40,
        "base_url": "https://api.cerebras.ai/v1",
        "api_key_env": "CEREBRAS_API_KEY",
        "model": "gpt-oss-120b",
        "rpm": 5, "rpd": None, "tpm": 30000, "tph": 1000000, "tpd": 1000000,
        "ctx": 131000,
        "min_max_tokens": 512,
        "notes": "huge token budget but only 5 req/min - good for few big calls",
    },
    {
        "name": "gemini-flash-lite",
        "priority": 65,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
        "model": "gemini-2.5-flash-lite",
        "rpm": 15, "rpd": 20, "tpm": 250000,
        "ctx": 1000000,
        "notes": "1M context, but only 20 requests/day (measured from Google's own "
                 "GenerateRequestsPerDayPerProjectPerModel-FreeTier violation). "
                 "Save it for long files.",
    },
    {
        "name": "nvidia",
        "priority": 20,
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY",
        "model": "meta/llama-3.3-70b-instruct",
        "rpm": 40, "rpd": None,
        "ctx": 128000,
        "notes": "no published daily cap; steady workhorse",
    },
    {
        "name": "github-models",
        "priority": 85,
        "base_url": "https://models.github.ai/inference",
        "api_key_env": "GITHUB_TOKEN",
        "model": "openai/gpt-4.1-mini",
        "rpm": 10, "rpd": 150,
        "ctx": 8000,
        "max_input_tokens": 8000,
        "notes": "8k in / 4k out hard cap per request",
    },
    {
        "name": "mistral",
        "priority": 30,
        "base_url": "https://api.mistral.ai/v1",
        "api_key_env": "MISTRAL_API_KEY",
        "model": "mistral-small-latest",
        "rpm": 60, "tpm": 500000, "tpd": 30000000,
        "ctx": 128000,
        "notes": "free 'Experiment' tier trains on your data - do not send anything private",
    },
    {
        "name": "gemini-flash",
        "priority": 70,
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "api_key_env": "GEMINI_API_KEY",
        "model": "gemini-2.5-flash",
        "rpm": 5, "rpd": 20, "tpm": 250000,
        "ctx": 1000000,
        "notes": "only 20/day - reserve it for long logs (--long picks it)",
    },
    {
        "name": "cohere",
        "priority": 60,
        "base_url": "https://api.cohere.ai/compatibility/v1",
        "api_key_env": "COHERE_API_KEY",
        "model": "command-a-03-2025",
        "rpm": 20, "rpd": 33,
        "ctx": 256000,
        "notes": "1000 requests/month total, so ~33/day",
    },
    {
        "name": "cloudflare",
        "priority": 88,
        "base_url": "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/ai/v1",
        "api_key_env": "CLOUDFLARE_API_KEY",
        "model": "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "rpm": 30, "rpd": None,
        "ctx": 24000,
        "notes": "10k neurons/day; needs CLOUDFLARE_ACCOUNT_ID too",
    },
    {
        "name": "openrouter",
        "priority": 80,
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
        "rpm": 20, "rpd": 50,
        "ctx": 1000000,
        "notes": "only 50 free calls/day across all :free models - keep as reserve. "
                 "See openrouter.ai/models?max_price=0 for the current free list",
    },
    {
        "name": "ollama",
        "priority": 95,
        "base_url": "http://localhost:11434/v1",
        "api_key_env": None,          # no key needed
        "api_key": "ollama",
        "model": "llama3.1:8b",
        "ctx": 128000,
        "enabled": False,             # flip to true (or --enable ollama) if you run Ollama
        "notes": "local, unlimited, offline - never rate limited",
    },
    {
        "name": "pollinations",
        "priority": 99,
        "base_url": "https://text.pollinations.ai/openai",
        "api_key_env": None,
        "api_key": "none",
        "model": "openai",
        "rpm": 3, "rpd": 100,
        "ctx": 32000,
        "enabled": False,             # keyless emergency fallback; quality varies
        "notes": "no signup at all; community-hosted, treat as best-effort",
    },
]

HERE = Path(__file__).resolve().parent


def _state_path() -> Path:
    """Where to keep the usage ledger.

    Next to the script for a personal checkout, but under the user's own home
    when installed system-wide (e.g. /usr/local/share), where the install
    directory is root-owned and every student needs a separate ledger.
    """
    override = os.environ.get("SMART_LLM_STATE")
    if override:
        return Path(override)
    try:
        mine = HERE.stat().st_uid == os.getuid()
    except OSError:
        mine = False
    # Only keep the ledger beside the script for a personal checkout. A shared
    # install directory must never hold one shared counter for the whole class,
    # even if its permissions happen to allow writing.
    if mine and os.access(HERE, os.W_OK):
        return HERE / "usage_state.json"
    return Path.home() / ".local" / "state" / "ai-toolkit" / "usage_state.json"


STATE_FILE = _state_path()
CONFIG_FILE = Path(os.environ.get("SMART_LLM_CONFIG", HERE / "providers.json"))
# Checked in order; the first value found for a key wins.
ENV_FILES = [
    Path.home() / ".ai-keys",                       # what students are given
    HERE / ".env",
    Path.home() / ".config" / "smart_llm" / "env",
]

DEFAULT_SYSTEM = None
CHARS_PER_TOKEN = 3.6      # deliberately pessimistic so we under-promise quota


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def log(msg: str, quiet: bool = False) -> None:
    if not quiet:
        print(msg, file=sys.stderr)


def estimate_tokens(text: str) -> int:
    return max(1, int(len(text) / CHARS_PER_TOKEN))


def read_piped_stdin(force: bool = False, wait: float = 0.5) -> str:
    """Read stdin only when something is genuinely being piped in.

    Naively calling sys.stdin.read() whenever stdin is not a tty hangs forever
    under cron, systemd, watch, or a shell loop, where stdin is open but idle.
    """
    import select
    import stat

    if force:
        return sys.stdin.read()
    if sys.stdin is None or sys.stdin.closed or sys.stdin.isatty():
        return ""
    try:
        mode = os.fstat(sys.stdin.fileno()).st_mode
    except (OSError, ValueError):
        return ""
    if stat.S_ISCHR(mode):                 # /dev/null and friends
        return ""
    if stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
        ready, _, _ = select.select([sys.stdin], [], [], wait)
        if not ready:                      # nothing coming; don't block the CLI
            return ""
    return sys.stdin.read()


def load_env_files() -> None:
    """Read simple KEY=value lines from .env files without needing python-dotenv."""
    for path in ENV_FILES:
        if not path.is_file():
            continue
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip().lstrip("export ").strip()
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def expand(value: str) -> str:
    """Substitute ${VAR} from the environment (used for Cloudflare's account id)."""
    return re.sub(r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), value)


def load_providers() -> list[dict]:
    """Built-in table, with providers.json merged over it by name."""
    table = {p["name"]: dict(p) for p in PROVIDERS}
    if CONFIG_FILE.is_file():
        try:
            extra = json.loads(CONFIG_FILE.read_text())
        except json.JSONDecodeError as e:
            log(f"[warning] {CONFIG_FILE.name} is not valid JSON ({e}) - ignoring it")
            extra = []
        for entry in extra:
            name = entry.get("name")
            if not name:
                continue
            table.setdefault(name, {}).update(entry)
    out = [p for p in table.values() if p.get("enabled", True)]
    out.sort(key=lambda p: p.get("priority", 500))
    return out


def has_key(p: dict) -> bool:
    env = p.get("api_key_env")
    if env is None:
        return True                       # keyless provider (ollama, pollinations)
    if not os.environ.get(env):
        return False
    if "${" in p.get("base_url", ""):     # e.g. Cloudflare needs an account id too
        return "" not in [os.environ.get(v, "") for v in re.findall(r"\$\{(\w+)\}", p["base_url"])]
    return True


def api_key_for(p: dict) -> str:
    env = p.get("api_key_env")
    return os.environ[env] if env else p.get("api_key", "none")


# ---------------------------------------------------------------------------
# Usage ledger
# ---------------------------------------------------------------------------

class Ledger:
    """Persistent per-provider usage counters, safe for concurrent shells."""

    def __init__(self, path: Path = STATE_FILE):
        self.path = path
        self.data: dict = {}
        self._read()

    def _read(self) -> None:
        try:
            self.data = json.loads(self.path.read_text())
        except (OSError, json.JSONDecodeError):
            self.data = {}

    def _write(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(json.dumps(self.data, indent=2))
            tmp.replace(self.path)       # atomic on POSIX
        except OSError as e:
            log(f"[warning] could not save usage state: {e}")

    def entry(self, name: str) -> dict:
        today = date.today().isoformat()
        e = self.data.setdefault(name, {})
        e.setdefault("requests", [])          # [timestamp, ...] last hour
        e.setdefault("tokens", [])            # [[timestamp, count], ...] last hour
        e.setdefault("day", today)
        e.setdefault("day_requests", 0)
        e.setdefault("day_tokens", 0)
        e.setdefault("cooldown_until", 0)
        e.setdefault("day_exhausted", False)
        e.setdefault("last_error", "")
        if e["day"] != today:                 # new calendar day, daily caps reset
            e.update(day=today, day_requests=0, day_tokens=0, day_exhausted=False)
        cutoff = time.time() - 3600
        e["requests"] = [t for t in e["requests"] if t > cutoff]
        e["tokens"] = [x for x in e["tokens"] if x[0] > cutoff]
        return e

    # -- windowed reads ----------------------------------------------------
    @staticmethod
    def _req_in(e: dict, seconds: int) -> int:
        cutoff = time.time() - seconds
        return sum(1 for t in e["requests"] if t > cutoff)

    @staticmethod
    def _tok_in(e: dict, seconds: int) -> int:
        cutoff = time.time() - seconds
        return sum(n for t, n in e["tokens"] if t > cutoff)

    def usage(self, p: dict) -> dict:
        e = self.entry(p["name"])
        return {
            "rpm": self._req_in(e, 60),
            "tpm": self._tok_in(e, 60),
            "tph": self._tok_in(e, 3600),
            "rpd": e["day_requests"],
            "tpd": e["day_tokens"],
            "cooldown_until": e["cooldown_until"],
            "day_exhausted": e["day_exhausted"],
            "last_error": e["last_error"],
        }

    # -- decisions ---------------------------------------------------------
    def blocker(self, p: dict, est_tokens: int = 0) -> str | None:
        """Return a human-readable reason this provider can't be used, or None."""
        u = self.usage(p)
        now = time.time()
        if u["day_exhausted"]:
            return "daily quota spent (provider said so) - resets tomorrow"
        if u["cooldown_until"] > now:
            return f"cooling down {int(u['cooldown_until'] - now)}s ({u['last_error']})"
        checks = [
            ("rpm", u["rpm"] + 1, "requests/min"),
            ("rpd", u["rpd"] + 1, "requests/day"),
            ("tpm", u["tpm"] + est_tokens, "tokens/min"),
            ("tph", u["tph"] + est_tokens, "tokens/hour"),
            ("tpd", u["tpd"] + est_tokens, "tokens/day"),
        ]
        for key, projected, label in checks:
            limit = p.get(key)
            if limit and projected > limit:
                return f"{label} limit reached ({u[key]}/{limit})"
        cap = p.get("max_input_tokens")
        if cap and est_tokens > cap:
            return f"prompt too large ({est_tokens} > {cap} token input cap)"
        return None

    def seconds_until_free(self, p: dict) -> float:
        """Rough wait until this provider might accept a request again."""
        e = self.entry(p["name"])
        now = time.time()
        waits = [max(0.0, e["cooldown_until"] - now)]
        if e.get("day_exhausted"):
            midnight = datetime.combine(date.today(), datetime.min.time()).timestamp() + 86400
            waits.append(midnight - now)
        if p.get("rpm") and self._req_in(e, 60) >= p["rpm"]:
            oldest = sorted(t for t in e["requests"] if t > now - 60)[0]
            waits.append(oldest + 60 - now)
        if p.get("tpm") and self._tok_in(e, 60) >= p["tpm"]:
            waits.append(60.0)
        if p.get("tph") and self._tok_in(e, 3600) >= p["tph"]:
            waits.append(300.0)
        for key, used in (("rpd", e["day_requests"]), ("tpd", e["day_tokens"])):
            if p.get(key) and used >= p[key]:
                midnight = datetime.combine(date.today(), datetime.min.time()).timestamp() + 86400
                waits.append(midnight - now)
        return max(waits) if waits else 0.0

    # -- writes ------------------------------------------------------------
    def record(self, p: dict, tokens: int) -> None:
        self._read()                      # merge with anything another shell wrote
        e = self.entry(p["name"])
        now = time.time()
        e["requests"].append(now)
        e["day_requests"] += 1
        if tokens:
            e["tokens"].append([now, tokens])
            e["day_tokens"] += tokens
        e["cooldown_until"] = 0
        e["day_exhausted"] = False
        e["last_error"] = ""
        self._write()

    def cooldown(self, p: dict, seconds: float, reason: str) -> None:
        self._read()
        e = self.entry(p["name"])
        e["cooldown_until"] = time.time() + seconds
        e["last_error"] = reason[:120]
        self._write()

    def exhaust_day(self, p: dict, reason: str) -> None:
        """Provider says the daily quota is gone - believe it over our counter.

        Recorded as a flag rather than by inflating day_requests, so `--status`
        keeps showing the true number of calls made.
        """
        self._read()
        e = self.entry(p["name"])
        e["day_exhausted"] = True
        e["last_error"] = reason[:200]
        self._write()

    def reset(self, name: str | None = None) -> None:
        self.data = {} if name is None else {k: v for k, v in self.data.items() if k != name}
        self._write()


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

def classify(exc: Exception) -> tuple[str, float]:
    """Map an exception to (kind, cooldown_seconds). kind drives the ledger update."""
    status = getattr(exc, "status_code", None) or getattr(getattr(exc, "response", None), "status_code", None)
    text = str(exc)
    retry_after = 0.0
    headers = getattr(getattr(exc, "response", None), "headers", None)
    if headers:
        for h in ("retry-after", "x-ratelimit-reset-requests", "x-ratelimit-reset-tokens"):
            raw = headers.get(h)
            if raw:
                m = re.match(r"([\d.]+)\s*(ms|s|m|h)?", str(raw))
                if m:
                    value, unit = float(m.group(1)), (m.group(2) or "s")
                    retry_after = value * {"ms": 0.001, "s": 1, "m": 60, "h": 3600}[unit]
                    break

    if status in (401, 403):
        return "auth", 86400              # bad/missing key: stop trying today
    if status == 402:
        return "billing", 86400           # free tier not active on this account
    if status == 429 or "rate limit" in text.lower():
        daily = any(w in text.lower() for w in ("per day", "requests per day", "rpd", "daily", "quota exceeded"))
        return ("daily" if daily else "rate"), (retry_after or (3600 if daily else 60))
    if status == 404:
        return "model", 86400             # model name wrong for this provider
    if status and 500 <= status < 600:
        return "server", 30
    if status == 400:
        return "bad_request", 0           # our fault (context too long etc.) - don't blame provider
    return "other", 45


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------

def _client(p: dict, timeout: float):
    from openai import OpenAI
    return OpenAI(
        base_url=expand(p["base_url"]),
        api_key=api_key_for(p),
        timeout=timeout,
        max_retries=0,                    # we do our own failover, not blind retries
    )


def candidates(providers: list[dict], only=None, exclude=(), prefer_long=False,
               order=None) -> list[dict]:
    pool = [p for p in providers if has_key(p)]
    if only:
        wanted = {n.strip() for n in only}
        pool = [p for p in pool if p["name"] in wanted or p["name"].split("-")[0] in wanted]
    if exclude:
        skip = {n.strip() for n in exclude}
        pool = [p for p in pool if p["name"] not in skip and p["name"].split("-")[0] not in skip]
    if prefer_long:
        pool.sort(key=lambda p: (-p.get("ctx", 0), p.get("priority", 500)))
    elif order:
        # Explicit precedence wins over the global priority field. Used by the
        # agent, which needs fast, reliable tool-callers rather than the
        # providers with the most raw quota.
        rank = {name: i for i, name in enumerate(order)}
        pool.sort(key=lambda p: (rank.get(p["name"], len(rank)), p.get("priority", 500)))
    return pool


def ask(
    prompt: str,
    system: str | None = DEFAULT_SYSTEM,
    *,
    model: str | None = None,
    only=None,
    exclude=(),
    prefer_long: bool = False,
    temperature: float | None = None,
    max_tokens: int | None = None,
    stream: bool = False,
    timeout: float = 90.0,
    wait: bool = False,
    quiet: bool = False,
    history: list[dict] | None = None,
    return_meta: bool = False,
    tools: list | None = None,
    raw: bool = False,
    order=None,
):
    """Send a prompt, switching providers as quotas run out. Returns the reply text.

    tools/raw enable agent use: pass OpenAI-style tool schemas and get the whole
    assistant message back (including tool_calls) instead of just its text.
    Pass prompt=None to continue an existing conversation without adding a turn.
    """
    ledger = Ledger()
    providers = load_providers()
    pool = candidates(providers, only, exclude, prefer_long, order)
    if not pool:
        raise RuntimeError(
            "No usable providers. Set at least one API key (see --list) "
            "or enable the keyless ones with --enable pollinations."
        )

    messages = list(history) if history else []
    if system and not any(m["role"] == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": system})
    if prompt is not None:
        messages.append({"role": "user", "content": prompt})

    est_in = estimate_tokens(json.dumps(messages))
    est_total = est_in + (max_tokens or 800)

    attempted: list[str] = []
    skipped: dict[str, str] = {}
    retries: dict[str, int] = {}

    while True:
        picked = None
        for p in pool:
            if p["name"] in attempted:
                continue
            if p.get("ctx") and est_in > p["ctx"] * 0.9:
                skipped[p["name"]] = f"prompt too long for {p['ctx']} token context"
                continue
            reason = ledger.blocker(p, est_total)
            if reason:
                skipped[p["name"]] = reason
                continue
            picked = p
            break

        if picked is None:
            usable = [p for p in pool if p["name"] not in attempted]
            if wait and usable:
                delay = min(ledger.seconds_until_free(p) for p in usable)
                delay = max(1.0, min(delay, 300.0))
                log(f"[all providers busy - waiting {int(delay)}s]", quiet)
                time.sleep(delay)
                continue
            detail = "\n".join(f"  {n:<18} {r}" for n, r in skipped.items()) or "  (none configured)"
            raise RuntimeError(
                "Every provider is unavailable right now:\n" + detail +
                "\n\nOptions: wait for the per-minute windows to roll over (--wait does this "
                "for you), add another key, or run a local model via --enable ollama."
            )

        attempted.append(picked["name"])
        log(f"[{picked['name']} :: {model or picked['model']}]", quiet)

        kwargs = {
            "model": model or picked["model"],
            "messages": messages,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if max_tokens:
            # Reasoning models spend tokens thinking; a tight budget yields an
            # empty message and a pointless failover.
            kwargs["max_tokens"] = max(max_tokens, picked.get("min_max_tokens", 0))
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            client = _client(picked, timeout)
            message = None
            if stream:
                text, used = _run_stream(client, kwargs)
            else:
                resp = client.chat.completions.create(**kwargs)
                message = resp.choices[0].message
                text = (message.content or "").strip()
                usage = getattr(resp, "usage", None)
                used = getattr(usage, "total_tokens", 0) or est_in + estimate_tokens(text)
            # An empty reply is a failure - unless the model chose to call a tool.
            if not text and not (raw and getattr(message, "tool_calls", None)):
                raise RuntimeError("provider returned an empty response")

            ledger.record(picked, used)
            result = message if raw else text
            if return_meta:
                return result, {"provider": picked["name"], "model": kwargs["model"], "tokens": used}
            return result

        except KeyboardInterrupt:
            raise
        except Exception as e:                       # noqa: BLE001 - any failure means "try the next one"
            kind, cool = classify(e)
            short = str(e).replace("\n", " ")[:400]
            if kind == "bad_request" and "tool call" in short.lower():
                # Smaller models sometimes emit a malformed tool name. It's
                # stochastic, so the same provider usually succeeds on a retry.
                n = retries.get(picked["name"], 0)
                if n < 2:
                    retries[picked["name"]] = n + 1
                    attempted.remove(picked["name"])
                    log(f"[{picked['name']} sent a bad tool call - retrying]", quiet)
                    continue
            if kind == "model" and model:
                # The name came from -m, not from the provider table. Blaming the
                # provider for a day would be wrong - just move on.
                kind, cool = "bad_model_override", 0
            if kind == "daily":
                ledger.exhaust_day(picked, short)
            elif kind in ("bad_request", "bad_model_override"):
                # Our request, not their quota. Retrying elsewhere is still worth
                # a shot (context limits differ) but don't penalise this provider.
                pass
            else:
                ledger.cooldown(picked, cool, f"{kind}: {short}")
            skipped[picked["name"]] = f"{kind}: {short}"
            log(f"[{picked['name']} failed ({kind}) - switching]", quiet)


def _run_stream(client, kwargs) -> tuple[str, int]:
    """Stream to stdout, returning (full_text, tokens_used)."""
    chunks: list[str] = []
    used = 0
    try:
        stream = client.chat.completions.create(
            **kwargs, stream=True, stream_options={"include_usage": True}
        )
    except Exception:
        stream = client.chat.completions.create(**kwargs, stream=True)
    for event in stream:
        if getattr(event, "usage", None):
            used = getattr(event.usage, "total_tokens", 0) or used
        if not event.choices:
            continue
        piece = event.choices[0].delta.content or ""
        if piece:
            chunks.append(piece)
            sys.stdout.write(piece)
            sys.stdout.flush()
    sys.stdout.write("\n")
    text = "".join(chunks)
    return text.strip(), used or estimate_tokens(json.dumps(kwargs["messages"]) + text)


# ---------------------------------------------------------------------------
# CLI subcommands
# ---------------------------------------------------------------------------

def compact(n) -> str:
    """1500 -> 1.5k, 1000000 -> 1M, None -> '-'"""
    if not n:
        return "-"
    for div, suffix in ((1_000_000, "M"), (1_000, "k")):
        if n >= div:
            scaled = n / div
            text = f"{scaled:.0f}" if scaled >= 10 or scaled == int(scaled) else f"{scaled:.1f}"
            return text + suffix
    return str(n)


def ratio(used, limit) -> str:
    return f"{compact(used) if used else '0'}/{compact(limit)}"


def cmd_status(args) -> int:
    ledger = Ledger()
    providers = load_providers()
    head = f"{'provider':<18} {'key':<4} {'req/min':>9} {'req/day':>10} {'tok/min':>11} {'tok/day':>11}  state"
    print(head)
    print("-" * len(head))
    for p in providers:
        u = ledger.usage(p)
        blocked = ledger.blocker(p) if has_key(p) else "no key"
        print(
            f"{p['name']:<18} {'yes' if has_key(p) else '-':<4} "
            f"{ratio(u['rpm'], p.get('rpm')):>9} "
            f"{ratio(u['rpd'], p.get('rpd')):>10} "
            f"{ratio(u['tpm'], p.get('tpm')):>11} "
            f"{ratio(u['tpd'], p.get('tpd')):>11}  "
            f"{blocked or 'ready'}"
        )
    print(f"\nledger: {STATE_FILE}")
    return 0


def cmd_list(args) -> int:
    for p in load_providers():
        mark = "OK " if has_key(p) else "   "
        env = p.get("api_key_env") or "(keyless)"
        print(f"{mark}{p['name']:<18} {p['model']:<38} {env}")
        limits = ", ".join(
            f"{k}={p[k]}" for k in ("rpm", "rpd", "tpm", "tph", "tpd") if p.get(k)
        ) or "no local limits"
        print(f"   {limits} | ctx {p.get('ctx', '?')}")
        if p.get("notes"):
            print(f"   {p['notes']}")
    disabled = [p["name"] for p in PROVIDERS if not p.get("enabled", True)]
    if disabled:
        print(f"\ndisabled by default: {', '.join(disabled)}  (enable with --enable NAME)")
    return 0


def cmd_test(args) -> int:
    providers = candidates(load_providers(), args.provider, args.exclude)
    if not providers:
        print("No providers have keys set.")
        return 1
    ok = 0
    for p in providers:
        sys.stdout.write(f"{p['name']:<18} ")
        sys.stdout.flush()
        t0 = time.time()
        try:
            client = _client(p, timeout=30)
            r = client.chat.completions.create(
                model=p["model"],
                messages=[{"role": "user", "content": "Reply with the single word: ok"}],
                max_tokens=200,   # reasoning models spend tokens before answering
            )
            reply = (r.choices[0].message.content or "").strip()[:20] or "(empty)"
            print(f"OK    {time.time() - t0:5.2f}s  '{reply}'")
            ok += 1
        except Exception as e:                       # noqa: BLE001
            kind, _ = classify(e)
            print(f"FAIL  {kind}: {str(e)[:70]}")
    print(f"\n{ok}/{len(providers)} providers reachable")
    return 0 if ok else 1


def cmd_chat(args) -> int:
    history: list[dict] = []
    if args.system:
        history.append({"role": "system", "content": args.system})
    print("smart_llm chat - /quit to exit, /status for quotas, /reset to clear history")
    while True:
        try:
            line = input("\n> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/quit", "/exit", "/q"):
            return 0
        if line == "/status":
            cmd_status(args)
            continue
        if line == "/reset":
            history = history[:1] if args.system else []
            print("(history cleared)")
            continue
        try:
            reply, meta = ask(
                line,
                system=None,
                history=history,
                model=args.model,
                only=args.provider,
                exclude=args.exclude,
                prefer_long=args.long,
                temperature=args.temperature,
                max_tokens=args.max_tokens,
                stream=True,
                timeout=args.timeout,
                wait=args.wait,
                quiet=args.quiet,
                return_meta=True,
            )
        except RuntimeError as e:
            print(f"\n{e}", file=sys.stderr)
            continue
        history.append({"role": "user", "content": line})
        history.append({"role": "assistant", "content": reply})
        if not args.quiet:
            print(f"  -- {meta['provider']}, {meta['tokens']} tokens", file=sys.stderr)


def cmd_dump_config(args) -> int:
    print(json.dumps(PROVIDERS, indent=2))
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="smart_llm.py",
        description="Free-tier LLM client that fails over between providers as quotas run out.",
        epilog="Examples:\n"
               "  smart_llm.py 'why does pw.x stop at scf step 1?'\n"
               "  cat pw.out | smart_llm.py 'summarise the error'\n"
               "  smart_llm.py -f pw.in -f pw.out --long 'compare these'\n"
               "  smart_llm.py --status\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("prompt", nargs="*", help="your question (also reads piped stdin)")
    ap.add_argument("-s", "--system", help="system prompt")
    ap.add_argument("-f", "--file", action="append", default=[],
                    help="attach a file's contents (repeatable)")
    ap.add_argument("-i", "--stdin", action="store_true",
                    help="always read stdin to EOF (use with a slow producer pipe)")
    ap.add_argument("-m", "--model", help="override the model name")
    ap.add_argument("-p", "--provider", action="append",
                    help="restrict to these providers (repeatable)")
    ap.add_argument("-x", "--exclude", action="append", default=[],
                    help="never use these providers (repeatable)")
    ap.add_argument("--enable", action="append", default=[],
                    help="turn on a provider that is off by default (ollama, pollinations)")
    ap.add_argument("--long", action="store_true",
                    help="prefer the largest-context provider available")
    ap.add_argument("-t", "--temperature", type=float)
    ap.add_argument("-n", "--max-tokens", type=int)
    ap.add_argument("--stream", action="store_true", help="print tokens as they arrive")
    ap.add_argument("--timeout", type=float, default=90.0)
    ap.add_argument("--wait", action="store_true",
                    help="if every provider is throttled, sleep until one frees up")
    ap.add_argument("--json", action="store_true", help="emit {provider, model, tokens, reply}")
    ap.add_argument("-q", "--quiet", action="store_true", help="no [provider] chatter on stderr")

    ap.add_argument("--status", action="store_true", help="show quota usage and exit")
    ap.add_argument("--list", action="store_true", help="show configured providers and exit")
    ap.add_argument("--test", action="store_true", help="ping every provider and exit")
    ap.add_argument("--chat", action="store_true", help="interactive conversation")
    ap.add_argument("--reset", nargs="?", const="__all__", metavar="PROVIDER",
                    help="clear the usage ledger (optionally for one provider)")
    ap.add_argument("--dump-config", action="store_true",
                    help="print the provider table as JSON (starting point for providers.json)")
    return ap


def main(argv=None) -> int:
    load_env_files()
    args = build_parser().parse_args(argv)

    for name in args.enable:
        for p in PROVIDERS:
            if p["name"] == name:
                p["enabled"] = True

    if args.dump_config:
        return cmd_dump_config(args)
    if args.reset:
        Ledger().reset(None if args.reset == "__all__" else args.reset)
        print("usage ledger cleared")
        return 0
    if args.list:
        return cmd_list(args)
    if args.status:
        return cmd_status(args)
    if args.test:
        return cmd_test(args)
    if args.chat:
        return cmd_chat(args)

    parts = []
    for path in args.file:
        try:
            body = Path(path).read_text(errors="replace")
        except OSError as e:
            print(f"cannot read {path}: {e}", file=sys.stderr)
            return 1
        parts.append(f"--- {path} ---\n{body}")
    piped = read_piped_stdin(force=args.stdin).strip()
    if piped:
        parts.append(f"--- piped input ---\n{piped}")
    question = " ".join(args.prompt).strip()
    if question:
        parts.append(question)

    if not parts:
        build_parser().print_help()
        return 1

    prompt = "\n\n".join(parts)

    try:
        reply, meta = ask(
            prompt,
            system=args.system,
            model=args.model,
            only=args.provider,
            exclude=args.exclude,
            prefer_long=args.long,
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            stream=args.stream and not args.json,
            timeout=args.timeout,
            wait=args.wait,
            quiet=args.quiet,
            return_meta=True,
        )
    except KeyboardInterrupt:
        return 130
    except RuntimeError as e:
        print(str(e), file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps({**meta, "reply": reply}, indent=2))
    elif not args.stream:
        print(reply)
    return 0


if __name__ == "__main__":
    sys.exit(main())
