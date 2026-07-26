#!/usr/bin/env bash
# install.sh - put the AI toolkit on a machine (e.g. the workshop VirtualBox image)
#
#   sudo ./install.sh              install for everyone -> /usr/local/share/ai-toolkit
#   ./install.sh --user            install for me only  -> ~/.local/share/ai-toolkit
#   sudo ./install.sh --uninstall
#
# After a system-wide install every student runs `ai` from anywhere. Their keys
# live in their own ~/.ai-keys and their quota ledger in their own home, so no
# one edits ~/.bashrc and nobody shares a counter.

set -euo pipefail

REPO="${AI_TOOLKIT_REPO:-sairajdream/free-ai-terminal}"
BRANCH="${AI_TOOLKIT_BRANCH:-main}"
INSTALL_CMD="curl -fsSL https://raw.githubusercontent.com/$REPO/$BRANCH/install.sh | sudo bash"

# Any unexpected failure must say so. This script used to die silently when a
# command in a `... || { ... }` group failed under `set -e`, which left people
# staring at a blank prompt with no idea what to do next.
on_err() {
  echo >&2
  echo "-----------------------------------------------------------------" >&2
  echo "The installer stopped unexpectedly (install.sh line $1)." >&2
  echo "Nothing is half-configured - it is safe to just run it again." >&2
  echo "If it keeps failing, please open an issue with this output:" >&2
  echo "  https://github.com/$REPO/issues" >&2
  echo "-----------------------------------------------------------------" >&2
}
trap 'on_err $LINENO' ERR

PREFIX="/usr/local"
MODE="system"
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)      MODE="user"; PREFIX="$HOME/.local"; shift ;;
    --prefix)    PREFIX="$2"; MODE="custom"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)   sed -n '2,10p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown option: $1"; exit 1 ;;
  esac
done

SHAREDIR="$PREFIX/share/ai-toolkit"
BINDIR="$PREFIX/bin"

# Where the source files are. When this script is piped from curl there is no
# directory to read them from, so fetch the repo instead.
SRC="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [[ -z "$SRC" || ! -f "$SRC/smart_llm.py" ]]; then
  command -v curl >/dev/null || { echo "curl is required"; exit 1; }
  TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
  echo "Downloading $REPO ($BRANCH)..."
  if ! curl -fsSL "https://codeload.github.com/$REPO/tar.gz/refs/heads/$BRANCH" | tar xz -C "$TMP"; then
    echo "Could not download $REPO. Check the name, or clone the repo and run ./install.sh"
    exit 1
  fi
  SRC="$TMP/$(ls "$TMP" | head -1)"
  [[ -f "$SRC/smart_llm.py" ]] || { echo "Downloaded archive looks wrong"; exit 1; }
fi

# Piped installs are usually not run as root; fall back to a per-user install
# rather than failing with a sudo error.
if [[ "$MODE" == "system" && $EUID -ne 0 ]]; then
  MODE="user"; PREFIX="$HOME/.local"
  SHAREDIR="$PREFIX/share/ai-toolkit"; BINDIR="$PREFIX/bin"
  echo
  echo "Not running as root, so this will install for $(id -un) ONLY, in $PREFIX."
  echo "For a teaching VM you almost certainly want every student to have it:"
  echo "    $INSTALL_CMD"
  echo
fi

# Root is needed only when we cannot actually write to the target, not because
# of which flag was used - otherwise --prefix into a temp dir demands sudo.
writable_target() {
  local d="$PREFIX"
  while [[ -n "$d" && ! -e "$d" ]]; do d="$(dirname "$d")"; done
  [[ -w "$d" ]]
}
if ! writable_target && [[ $EUID -ne 0 ]]; then
  echo "Cannot write to $PREFIX. Use: sudo $0     (or $0 --user)"
  exit 1
fi

if [[ $UNINSTALL -eq 1 ]]; then
  rm -rf "$SHAREDIR" "$BINDIR/ai"
  rm -f /etc/profile.d/ai-toolkit.sh
  echo "Removed $SHAREDIR and $BINDIR/ai"
  echo "Students' own ~/.ai-keys files were left alone."
  exit 0
fi

command -v python3 >/dev/null || { echo "python3 not found. Install it with: sudo apt install python3"; exit 1; }

# The python3 running this script is not necessarily the one a plain student
# gets - a conda env is the classic trap. Target the one they will actually use.
STUDENT_PY="$(env -i PATH=/usr/local/bin:/usr/bin:/bin sh -c 'command -v python3' 2>/dev/null || true)"
[[ -n "$STUDENT_PY" ]] || STUDENT_PY="$(command -v python3)"
if [[ "$MODE" == "system" ]]; then TARGET_PY="$STUDENT_PY"; else TARGET_PY="$(command -v python3)"; fi

if [[ -n "${CONDA_PREFIX:-}" ]]; then
  echo "Note: you are inside the conda env '$(basename "$CONDA_PREFIX")'."
  echo "      Installing 'openai' for $TARGET_PY, not for conda, so that"
  echo "      students outside conda get a working 'ai' too."
  echo
fi

# ---------------------------------------------------------------------------
# Dependency handling. Never fatal: a missing module still leaves a usable
# install, and we print the exact command to finish the job.
# ---------------------------------------------------------------------------
has_openai() {   # $1 = interpreter
  if [[ "$MODE" == "system" ]]; then
    env PYTHONNOUSERSITE=1 "$1" -c "import openai" 2>/dev/null
  else
    "$1" -c "import openai" 2>/dev/null
  fi
}

have_pip() { "$1" -m pip --version >/dev/null 2>&1; }

# Debian and Ubuntu ship python3 without pip. Try to add it rather than dying
# with "No module named pip", which is what used to happen here.
bootstrap_pip() {   # $1 = interpreter
  local py="$1"
  have_pip "$py" && return 0
  echo "  pip is missing for $py, trying to add it..."
  "$py" -m ensurepip --default-pip >/dev/null 2>&1 || true
  have_pip "$py" && return 0
  if [[ $EUID -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
    echo "  installing python3-pip with apt (this can take a minute)..."
    DEBIAN_FRONTEND=noninteractive apt-get update -qq  >/dev/null 2>&1 || true
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3-pip >/dev/null 2>&1 || true
  fi
  have_pip "$py"
}

install_openai() {   # $1 = interpreter
  local py="$1"
  has_openai "$py" && return 0
  bootstrap_pip "$py" || return 1
  # PIP_USER=0 stops pip quietly installing into ~/.local when a shared
  # install is what was asked for.
  PIP_USER=0 "$py" -m pip install --quiet openai 2>/dev/null && return 0
  PIP_USER=0 "$py" -m pip install --quiet --break-system-packages openai 2>/dev/null && return 0
  return 1
}

# ---------------------------------------------------------------------------
# Install the files FIRST. They always succeed, and doing them before the
# dependency means a pip problem can no longer leave you with nothing.
# ---------------------------------------------------------------------------
echo "Installing to $SHAREDIR"
mkdir -p "$SHAREDIR" "$BINDIR"
install -m 0755 "$SRC/ai"           "$SHAREDIR/ai"
install -m 0755 "$SRC/smart_llm.py" "$SHAREDIR/smart_llm.py"
install -m 0755 "$SRC/tutor.py"     "$SHAREDIR/tutor.py"
install -m 0644 "$SRC/ai-keys.template" "$SHAREDIR/ai-keys.template"
[[ -f "$SRC/STUDENTS.md" ]] && install -m 0644 "$SRC/STUDENTS.md" "$SHAREDIR/STUDENTS.md"
[[ -f "$SRC/README.md"   ]] && install -m 0644 "$SRC/README.md"   "$SHAREDIR/README.md"
# providers.json is optional local tuning; ship it only if present
[[ -f "$SRC/providers.json" ]] && install -m 0644 "$SRC/providers.json" "$SHAREDIR/providers.json"

# Pin the interpreter for a shared install. With `#!/usr/bin/env python3` the
# command resolves to whatever python3 is first on PATH, so it would work for
# an instructor inside conda and fail for every student. An absolute shebang
# makes `ai` behave identically for everyone.
if [[ "$MODE" == "system" && -x "$STUDENT_PY" ]]; then
  sed -i "1s|^#!.*|#!$STUDENT_PY|" "$SHAREDIR/ai"
fi

ln -sf "$SHAREDIR/ai" "$BINDIR/ai"

# A first-run greeting so a student who types `ai` before adding keys is not lost.
if [[ "$MODE" == "system" ]]; then
  cat > /etc/profile.d/ai-toolkit.sh <<EOF
# AI toolkit: remind the user once if they have not set up their keys yet
if [ -n "\$PS1" ] && [ ! -f "\$HOME/.ai-keys" ] && [ -f $SHAREDIR/ai-keys.template ]; then
  echo "Set up your AI helper:  cp $SHAREDIR/ai-keys.template ~/.ai-keys && nano ~/.ai-keys"
fi
EOF
  chmod 0644 /etc/profile.d/ai-toolkit.sh
fi

OPENAI_OK=1
if ! has_openai "$TARGET_PY"; then
  echo
  echo "Installing the one dependency (openai) for $TARGET_PY..."
  install_openai "$TARGET_PY" || OPENAI_OK=0
fi
has_openai "$TARGET_PY" || OPENAI_OK=0

# ---------------------------------------------------------------------------
# Next steps, always printed, whatever happened above.
# ---------------------------------------------------------------------------
echo
echo "================================================================="
if [[ "$MODE" == "system" ]]; then
  echo " Installed for EVERY user on this machine."
else
  echo " Installed for $(id -un) ONLY  ($PREFIX)."
fi
echo "================================================================="
echo

STEP=1
if [[ $OPENAI_OK -eq 1 ]]; then
  echo "Checked: $TARGET_PY can import openai$( [[ "$MODE" == "system" ]] && echo " (visible to all users)" )."
  echo
else
  echo "!! NOT FINISHED - the 'openai' package is still missing, so 'ai'"
  echo "!! will fail with \"No module named 'openai'\"."
  echo
  echo "$STEP. Install it (copy and paste both lines):"
  echo "     sudo apt update && sudo apt install -y python3-pip"
  echo "     sudo $TARGET_PY -m pip install --break-system-packages openai"
  echo
  STEP=$((STEP + 1))
fi

if [[ ":$PATH:" != *":$BINDIR:"* ]]; then
  echo "$STEP. $BINDIR is NOT on your PATH, so 'ai' will say"
  echo "   'command not found'. Fix it with both lines - the second one"
  echo "   works in this shell straight away, no need to log out:"
  echo "     echo 'export PATH=\"$BINDIR:\$PATH\"' >> ~/.bashrc"
  echo "     export PATH=\"$BINDIR:\$PATH\""
  echo
  STEP=$((STEP + 1))
fi

echo "$STEP. Add your free API keys (each student does this once):"
echo "     cp $SHAREDIR/ai-keys.template ~/.ai-keys"
echo "     nano ~/.ai-keys        # paste your own keys"
echo "     chmod 600 ~/.ai-keys"
echo
STEP=$((STEP + 1))
echo "$STEP. Check it works:"
echo "     ai status"
echo "     ai why is the ls command useful"
echo

if [[ "$MODE" != "system" ]]; then
  echo "To give every student on this VM the 'ai' command instead, run:"
  echo "     $INSTALL_CMD"
  echo
fi

trap - ERR
exit 0
