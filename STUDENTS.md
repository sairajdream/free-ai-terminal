# Your AI terminal helper: one page

You only need to remember **two commands**.

```bash
ai <your question>      # ask anything
ai fix                  # something broke, help me
```

**Forgotten how it works?** Type `ai help`. It prints everything on this page,
with examples. You never need to come back to this document.

---

## Setup (once, about 3 minutes)

The `ai` command is already installed. You only need to add your own keys.
**Do not edit `~/.bashrc`.** You never need to touch it.

```bash
cp /usr/local/share/ai-toolkit/ai-keys.template ~/.ai-keys
nano ~/.ai-keys        # paste your keys, then Ctrl+O, Enter, Ctrl+X
chmod 600 ~/.ai-keys   # keeps your keys private
ai status              # should say "ready"
```

Get your own free keys first. A minute each, no credit card:

| | Where | Why |
|---|---|---|
| **Groq** | https://console.groq.com/keys | answers most questions, very fast |
| **Mistral** | https://console.mistral.ai/api-keys | powers `ai fix` |

Two keys is enough. Optional extras are listed inside the template.

Use **your own** keys, never a shared one. The free limits are counted per
account, so one shared key means the whole room runs out at the same moment.

If `ai status` says `no key`, check that `~/.ai-keys` has no spaces around the
`=` sign and no quotes: `GROQ_API_KEY=gsk_abc123`

---

## 1. `ai`: ask anything

No quotes needed for ordinary questions.

```bash
ai how do I see how much disk space is free
ai what does chmod 755 mean
ai how do I copy a folder and everything inside it
```

If the answer is a command, it offers to run it for you. Press Enter for yes.

**Ask about a file** by naming it:

```bash
ai pw.out what went wrong
ai pw.in pw.out why did this not converge
```

This is the one to use for long output files. It handles thousands of lines.

---

## 2. `ai fix`: when you're stuck

Use this when something is broken and you don't know why. Unlike `ai`, it can
actually look around: list your files, read them, run things, and react.

```bash
ai fix
```

Then just talk to it:

```
you> I ran my script and got "command not found"
you> my file disappeared, where did it go?
you> /quit
```

Useful inside: `/quit` `/reset` `/cost` `/status`

---

## Safety: what it will refuse

It will **never** run these, even if you ask:

`rm -rf ~` · `rm -rf /` · anything with `sudo` · `mkfs` · `dd of=/dev/sda` ·
fork bombs · `curl … | bash` · `shutdown`

Commands that only **look** (`ls`, `cat`, `df`, `grep`) run straight away.
Commands that **change** something always ask you first. Read what it's about to
do before typing `y`.

---

## Running out of quota

```bash
ai status
```

Shows what's left. If one provider is used up it switches to another
automatically, so you don't need to do anything.

---

## ⚠️ The one thing to remember

**The AI is often confident and wrong.**

It will happily write you a Quantum ESPRESSO input file with a water molecule
whose bond angle is 45° instead of 104.5°. The calculation runs. It converges.
It prints an energy. And it is meaningless.

Use it to:
- explain errors ✅
- remember command syntax ✅
- understand what a file is telling you ✅

Do **not** use it to:
- invent scientific input files from scratch ❌
- decide whether a result is correct ❌

Always start from a template your instructor gave you, and check any number it
produces against something you trust.
