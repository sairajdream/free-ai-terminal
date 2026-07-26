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

PREFIX="/usr/local"
SHAREDIR=""
MODE="system"
UNINSTALL=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --user)      MODE="user"; PREFIX="$HOME/.local"; shift ;;
    --prefix)    PREFIX="$2"; MODE="custom"; shift 2 ;;
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help)   sed -n '2,12p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown option: $1"; exit 1 ;;
  esac
done

SHAREDIR="$PREFIX/share/ai-toolkit"
BINDIR="$PREFIX/bin"

# Where the source files are. When this script is piped from curl there is no
# directory to read them from, so fetch the repo instead.
REPO="${AI_TOOLKIT_REPO:-sairajdream/ai-terminal-helper}"
BRANCH="${AI_TOOLKIT_BRANCH:-main}"

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
  echo "Not running as root - installing just for you, in $PREFIX"
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
  echo "Removed $SHAREDIR and $BINDIR/ai"
  echo "Students' own ~/.ai-keys files were left alone."
  exit 0
fi

command -v python3 >/dev/null || { echo "python3 not found"; exit 1; }
python3 -c "import openai" 2>/dev/null || {
  echo "Installing the one dependency (openai)..."
  python3 -m pip install --quiet openai || python3 -m pip install --quiet --break-system-packages openai
}

echo "Installing to $SHAREDIR"
mkdir -p "$SHAREDIR" "$BINDIR"
install -m 0755 "$SRC/ai"          "$SHAREDIR/ai"
install -m 0755 "$SRC/smart_llm.py" "$SHAREDIR/smart_llm.py"
install -m 0755 "$SRC/tutor.py"     "$SHAREDIR/tutor.py"
install -m 0644 "$SRC/ai-keys.template" "$SHAREDIR/ai-keys.template"
[[ -f "$SRC/STUDENTS.md" ]] && install -m 0644 "$SRC/STUDENTS.md" "$SHAREDIR/STUDENTS.md"
[[ -f "$SRC/README.md"   ]] && install -m 0644 "$SRC/README.md"   "$SHAREDIR/README.md"
# providers.json is optional local tuning; ship it only if present
[[ -f "$SRC/providers.json" ]] && install -m 0644 "$SRC/providers.json" "$SHAREDIR/providers.json"

ln -sf "$SHAREDIR/ai" "$BINDIR/ai"

# A first-run greeting so a student who types `ai` before adding keys is not lost.
if [[ "$MODE" == "system" ]]; then
  cat > /etc/profile.d/ai-toolkit.sh <<'EOF'
# AI toolkit: remind the user once if they have not set up their keys yet
if [ -n "$PS1" ] && [ ! -f "$HOME/.ai-keys" ] && [ -f /usr/local/share/ai-toolkit/ai-keys.template ]; then
  echo "Set up your AI helper:  cp /usr/local/share/ai-toolkit/ai-keys.template ~/.ai-keys && nano ~/.ai-keys"
fi
EOF
  chmod 0644 /etc/profile.d/ai-toolkit.sh
fi

# The python3 a plain student gets is not necessarily the one running this
# script - a conda env is the classic trap. Check the one they will actually use.
STUDENT_PY="$(env -i PATH=/usr/local/bin:/usr/bin:/bin sh -c 'command -v python3' 2>/dev/null || true)"
if [[ -n "$STUDENT_PY" ]]; then
  if ! "$STUDENT_PY" -c "import openai" 2>/dev/null; then
    echo
    echo "Installing 'openai' into $STUDENT_PY (the interpreter students will use)..."
    "$STUDENT_PY" -m pip install --quiet openai 2>/dev/null \
      || "$STUDENT_PY" -m pip install --quiet --break-system-packages openai 2>/dev/null \
      || {
        echo
        echo "WARNING: could not install 'openai' for $STUDENT_PY."
        echo "         Students outside your conda environment will see"
        echo "         \"No module named 'openai'\". Fix with:"
        echo "             sudo $STUDENT_PY -m pip install --break-system-packages openai"
      }
  fi
  "$STUDENT_PY" -c "import openai" 2>/dev/null && echo "Checked: $STUDENT_PY can import openai."
fi

echo
echo "Done. Every user now has the 'ai' command."
echo
echo "Each student does this once:"
echo "    cp $SHAREDIR/ai-keys.template ~/.ai-keys"
echo "    nano ~/.ai-keys        # paste their own keys"
echo "    chmod 600 ~/.ai-keys"
echo "    ai status"
echo
if [[ ":$PATH:" != *":$BINDIR:"* ]]; then
  echo "NOTE: $BINDIR is not on your PATH yet. Add it with:"
  echo "    echo 'export PATH=\"$BINDIR:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
fi
exit 0
