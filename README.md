# ai — a free AI helper for the Linux terminal

Two commands. No subscription. No credit card.

```bash
ai how do I find files bigger than 100MB    # ask anything
ai fix                                       # something broke, help me
```

It routes your questions across a dozen free AI providers, tracks how much of
each free quota you have left (per minute **and** per day, requests **and**
tokens), and switches automatically when one runs out. Built for a university
workshop where students had never used a terminal before.

---

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/sairajdream/free-ai-terminal/main/install.sh | bash
```

For everyone on a shared machine or a teaching VM:

```bash
curl -fsSL https://raw.githubusercontent.com/sairajdream/free-ai-terminal/main/install.sh | sudo bash
```

<details>
<summary>Prefer not to pipe a script into your shell? (Good instinct — here's the manual way)</summary>

Piping a URL straight into `bash` runs code you have not read. This tool
actually blocks students from doing it. So if you would rather check first:

```bash
git clone https://github.com/sairajdream/free-ai-terminal.git
cd free-ai-terminal
less install.sh        # read it
sudo ./install.sh      # then run it
```
</details>

Only dependency is the `openai` Python package, which the installer handles.

---

## Get your keys (2 minutes)

Copy the template, paste your keys, done. **You never edit `~/.bashrc`.**

```bash
cp /usr/local/share/ai-toolkit/ai-keys.template ~/.ai-keys
nano ~/.ai-keys
chmod 600 ~/.ai-keys
ai status
```

### The seven worth having

Add as many as you like — unset keys are skipped silently. **Two is enough to
start; ⭐ marks those.** Every limit and latency below was measured against the
live API in July 2026, not copied from a blog post.

| # | Provider | Get a key | Free limit | Measured | Best for |
|---|---|---|---|---|---|
| 1 ⭐ | **Groq** | [console.groq.com/keys](https://console.groq.com/keys) | 30/min · ~1,000/day **per model** · 8–12k tok/min | ✅ 0.5–1.0 s | Almost every question. Fastest free inference anywhere. Two model buckets from one key |
| 2 ⭐ | **Mistral** | [console.mistral.ai/api-keys](https://console.mistral.ai/api-keys) | ~1/sec · 500k tok/min · ~1B tok/month | ✅ 0.5–2 s | Powers `ai fix`. Huge volume ⚠️ free tier trains on your data |
| 3 | **NVIDIA NIM** | [build.nvidia.com](https://build.nvidia.com/) | 40/min · **no daily cap** | ✅ 1–58 s | The volume backstop when everything else is spent. Latency swings wildly |
| 4 | **Google AI Studio** | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | **20/day per model** · 250k tok/min · 1M context | ✅ 0.6–1.2 s | Huge files. `ai bigfile.log what broke` routes here automatically |
| 5 | **Cohere** | [dashboard.cohere.com/api-keys](https://dashboard.cohere.com/api-keys) | 20/min · 1,000/**month** (~33/day) | ✅ 0.6–2.7 s | A genuinely different model family — a second opinion when an answer smells wrong |
| 6 | **OpenRouter** | [openrouter.ai/keys](https://openrouter.ai/keys) | 20/min · **50/day** across all `:free` models | ✅ 0.7–3.8 s | ~15 models behind one key. The escape hatch when a model ID gets retired |
| 7 | **Cerebras** | [cloud.cerebras.ai](https://cloud.cerebras.ai/) | 5/min · 30k tok/min · **1M tokens/day** | ⚠️ 402 on our test account | A few very large prompts. Free tier may need activating in the billing tab |

<details>
<summary>Also supported (configured, lower priority)</summary>

| Provider | Get a key | Free limit | Measured | Notes |
|---|---|---|---|---|
| GitHub Models | [github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens) | 10/min · 150/day (mini) · 8k context | ❌ 401 with a classic token | Needs a **fine-grained** PAT with the **Models** permission. 8k context is too small for long logs |
| Cloudflare Workers AI | [dash.cloudflare.com](https://dash.cloudflare.com/profile/api-tokens) | 10,000 neurons/day | not tested | Also needs `CLOUDFLARE_ACCOUNT_ID`. Mostly re-serves the same Llama models as Groq and NVIDIA |
| Ollama | [ollama.com](https://ollama.com/) | unlimited, offline, private | not tested | No key at all. The only option that keeps unpublished data on your machine. Enable with `--enable ollama` |
| Pollinations | — | credit-limited | not tested | Keyless emergency fallback, community-hosted. Off by default |

Adding any other OpenAI-compatible endpoint takes four lines of JSON — see
[Adding a provider](#adding-a-provider).
</details>

**Get your own keys.** Free limits are counted per *account* (Groq meters per
organisation), so one shared key means everybody runs out at the same moment.

#### What the testing changed

- **Google is 20 requests/day, not 1,500 or 500.** Its own 429 reports
  `GenerateRequestsPerDayPerProjectPerModel-FreeTier` with a value of `20`. The
  figures repeated across the internet are years out of date, so this tool
  keeps Gemini in reserve for large-context work instead of everyday questions.
- **Groq is 1,000/day on the 70B model, not 14,400.** The 14,400 figure belongs
  to the small 8B model.
- **Groq cannot run tool-calling agents.** Its 8k tokens/minute limit is smaller
  than a typical agent's tool schema (~25k), so you get an instant `413`. It is
  excellent for everything else, which is why `ai` uses it and `ai fix` does not.
- **llama-3.3-70b emitted malformed tool calls in 2 of 3 runs.** Mistral, NVIDIA
  and Gemini were clean 3 of 3, so `ai fix` prefers those.
- **SambaNova, Hugging Face and Together are not permanent free tiers** any more
  — they are trial credits. They are deliberately not included.

Providers are tried abundant-first, scarce-last, so a throwaway one-liner never
burns a slot you will want later. `ai status` shows exactly what is left.

---

## Using it

```bash
ai how do I see how much disk space is free     # no quotes needed
ai what does chmod 755 mean

ai pw.out what went wrong                        # just name the file
ai pw.in pw.out why did this not converge        # several files

ai fix                                           # interactive, when stuck
ai fix my script says command not found

ai status                                        # quota left today
ai help                                          # full guide, with examples
```

If the answer is a command, it offers to run it. Read-only commands (`ls`,
`cat`, `df`) run straight away; anything that changes something asks first.

### It will refuse to run these

`rm -rf ~` · `rm -rf /` · anything with `sudo` · `mkfs` · `dd of=/dev/sda` ·
fork bombs · `curl … | bash` · `shutdown`

Checked by parsing the command, not pattern-matching — `sudo rm -rf /home/you`
is caught too.

---

## Why it is cheap

Measured overhead per call, same task on each tool:

| Tool | Tokens per task |
|---|---|
| **`ai`** | **~130** |
| **`ai fix`** | **~930** |
| Aider | ~2,300 |
| Qwen Code / Gemini CLI | **~25,000** |

Full coding agents send their entire tool schema on every call. On Groq's free
tier that is an instant `413 Request too large` — the agent cannot run at all.
`ai` uses a short prompt and three small tools instead, so it fits comfortably
inside every free tier.

---

## For instructors

```bash
sudo ./install.sh          # on the VM image, then snapshot
```

Everything is per-user afterwards: keys in `~/.ai-keys`, quota ledger in
`~/.local/state/ai-toolkit/`. Nobody shares a counter, nobody edits a shell
config. [`STUDENTS.md`](STUDENTS.md) is a one-page handout — print it, or tell
them to type `ai help`.

Uninstall with `sudo ./install.sh --uninstall` (leaves everyone's keys alone).

### Files

| File | What it does |
|---|---|
| `ai` | the only command students use |
| `smart_llm.py` | provider routing, quota ledger, failover |
| `tutor.py` | the `ai fix` agent and its safety rules |
| `ai-keys.template` | students copy this to `~/.ai-keys` |
| `install.sh` | installer |
| `STUDENTS.md` | one-page handout |

### Adding a provider

Anything with an OpenAI-compatible endpoint works. Drop a `providers.json` next
to the script; entries merge over the built-ins by name:

```json
[{"name": "my-provider",
  "base_url": "https://api.example.com/v1",
  "api_key_env": "MY_API_KEY",
  "model": "some-model",
  "rpm": 10, "rpd": 200, "ctx": 32000, "priority": 45}]
```

---

## ⚠️ The thing to tell your students

**The AI is often confident and wrong.**

Asked for a Quantum ESPRESSO input file for water, a free model produced a
molecule with a bond angle of **45°** instead of 104.5°, and one O–H bond 41%
too long. The file was accepted. The calculation converged. It printed an
energy. And it was meaningless.

Automated testing catches **syntax** errors. Nothing catches **wrong physics**.

Good for: explaining errors, remembering command syntax, reading long logs.
Not for: inventing scientific input files, or deciding whether a result is right.

---

## License

MIT — see [LICENSE](LICENSE).
