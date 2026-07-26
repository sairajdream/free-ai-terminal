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
curl -fsSL https://raw.githubusercontent.com/sairajdream/ai-terminal-helper/main/install.sh | bash
```

For everyone on a shared machine or a teaching VM:

```bash
curl -fsSL https://raw.githubusercontent.com/sairajdream/ai-terminal-helper/main/install.sh | sudo bash
```

<details>
<summary>Prefer not to pipe a script into your shell? (Good instinct — here's the manual way)</summary>

Piping a URL straight into `bash` runs code you have not read. This tool
actually blocks students from doing it. So if you would rather check first:

```bash
git clone https://github.com/sairajdream/ai-terminal-helper.git
cd ai-terminal-helper
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

**Start with these two — they cover almost everything:**

| Provider | Get a key | Free limit | Used for |
|---|---|---|---|
| **Groq** | **[console.groq.com/keys](https://console.groq.com/keys)** | 30/min · ~1,000/day per model | almost every question — very fast |
| **Mistral** | **[console.mistral.ai/api-keys](https://console.mistral.ai/api-keys)** | ~1/sec · ~1B tokens/month | powers `ai fix` |

<details>
<summary>Optional extras, once you run out</summary>

| Provider | Get a key | Free limit | Notes |
|---|---|---|---|
| NVIDIA NIM | [build.nvidia.com](https://build.nvidia.com/) | 40/min, no daily cap | latency varies a lot (8s–58s measured) |
| Google AI Studio | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) | **20/day per model**, 1M context | save it for huge files |
| Cerebras | [cloud.cerebras.ai](https://cloud.cerebras.ai/) | 5/min, 1M tokens/day | few big prompts, not chat |
| Cohere | [dashboard.cohere.com/api-keys](https://dashboard.cohere.com/api-keys) | 1,000/month (~33/day) | different model family |
| OpenRouter | [openrouter.ai/keys](https://openrouter.ai/keys) | 50/day total | escape hatch, many models |
| GitHub Models | [github.com/settings/personal-access-tokens](https://github.com/settings/personal-access-tokens) | 150/day (mini) | needs a **fine-grained** PAT with the **Models** permission |
| Cloudflare | [dash.cloudflare.com](https://dash.cloudflare.com/profile/api-tokens) | 10k neurons/day | also needs `CLOUDFLARE_ACCOUNT_ID` |
| Ollama | [ollama.com](https://ollama.com/) | unlimited, offline | no key; fully private |

**Get your own keys.** Free limits are counted per account, so a shared key
means everyone runs out at the same moment.
</details>

> **Numbers verified July 2026 against each provider's live API.** Google's own
> 429 response reports `GenerateRequestsPerDayPerProjectPerModel-FreeTier` with
> a value of **20** — the "1,500/day" and "500/day" figures repeated across the
> internet are years out of date.

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
