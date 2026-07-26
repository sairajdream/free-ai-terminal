#!/usr/bin/env bash
# agent.sh - launch an agentic terminal assistant on a free API key.
#
# Unlike smart_llm.py (one question, one answer), this gives you an agent that
# can read/write files and run commands: "make me a QE input for silicon, run
# pw.x on it, and fix it if it fails".
#
# Usage:
#   ./agent.sh                        interactive session, auto-picked backend
#   ./agent.sh "write hello.py and run it"     one-shot
#   ./agent.sh -b nvidia "..."        force a backend
#   ./agent.sh -l                     list backends
#   ./agent.sh --safe "..."           ask before every command (recommended for beginners)
#
# IMPORTANT: the default mode lets the agent run shell commands without asking.
# Use --safe until you trust it, and never run it in a directory you can't
# afford to lose. Start it from a scratch folder, not from $HOME.

set -uo pipefail

# backend: name|env var|base url|model
BACKENDS=(
  "gemini|GEMINI_API_KEY|https://generativelanguage.googleapis.com/v1beta/openai/|gemini-2.5-flash-lite"
  "nvidia|NVIDIA_API_KEY|https://integrate.api.nvidia.com/v1|meta/llama-3.3-70b-instruct"
  "mistral|MISTRAL_API_KEY|https://api.mistral.ai/v1|mistral-small-latest"
  "ollama|__NONE__|http://localhost:11434/v1|gemma4:e2b"
)

# Groq and Cohere are deliberately absent: an agent's tool definitions are
# ~25k tokens per call, and Groq's free tier allows 8-12k tokens/minute, so
# every request fails with 413 before the agent does anything. Cohere and
# OpenRouter have enough tokens but only 33-50 requests/day, and a single
# agent task burns 10-30 requests.

approval="yolo"
backend=""
mode="interactive"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -b|--backend) backend="$2"; shift 2 ;;
    -l|--list)
      printf "%-9s %-22s %s\n" BACKEND KEY STATUS
      for e in "${BACKENDS[@]}"; do
        IFS='|' read -r n v u m <<< "$e"
        if [[ "$v" == "__NONE__" ]]; then
          curl -s -m 2 -o /dev/null "${u%/v1}/api/tags" && s="local, running" || s="local, not running"
        elif [[ -n "${!v:-}" ]]; then s="ready"; else s="no key ($v unset)"; fi
        printf "%-9s %-22s %s\n" "$n" "$m" "$s"
      done
      exit 0 ;;
    --safe)  approval="default"; shift ;;
    -h|--help) sed -n '2,20p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) break ;;
  esac
done

command -v qwen >/dev/null || { echo "qwen not found. Install: npm i -g @qwen-code/qwen-code"; exit 1; }

pick() {
  for e in "${BACKENDS[@]}"; do
    IFS='|' read -r n v u m <<< "$e"
    [[ -n "$backend" && "$n" != "$backend" ]] && continue
    if [[ "$v" == "__NONE__" ]]; then
      curl -s -m 2 -o /dev/null "${u%/v1}/api/tags" || continue
      echo "$n|dummy|$u|$m"; return 0
    fi
    [[ -n "${!v:-}" ]] && { echo "$n|${!v}|$u|$m"; return 0; }
  done
  return 1
}

sel="$(pick)" || { echo "No usable backend. Try: $0 --list"; exit 1; }
IFS='|' read -r name key url model <<< "$sel"

echo "[agent: $name / $model${1:+ }]" >&2
[[ "$approval" == "yolo" ]] && echo "[running commands WITHOUT asking - use --safe to be prompted]" >&2

exec qwen --auth-type openai \
  --openai-api-key "$key" \
  --openai-base-url "$url" \
  -m "$model" \
  --approval-mode "$approval" \
  ${1:+"$*"}
