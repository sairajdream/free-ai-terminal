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

# One place to register temporary files. Several EXIT traps would overwrite each
# other and leak whatever the earlier one was meant to remove.
CLEANUP=()
cleanup() { local p; for p in "${CLEANUP[@]:-}"; do [[ -n "$p" ]] && rm -rf "$p"; done; }
trap cleanup EXIT

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
  TMP="$(mktemp -d)"; CLEANUP+=("$TMP")
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

# Everything apt says gets kept here. Hiding it was a mistake: when apt failed
# the user was told "still missing" with no clue whether it was a dpkg lock, a
# dead mirror or a broken dependency.
FAILLOG="$(mktemp)"; CLEANUP+=("$FAILLOG")

# apt-get, but retried: on a freshly booted VM unattended-upgrades usually
# holds the dpkg lock for the first minute or two.
apt_try() {   # $@ = apt-get arguments
  local attempt
  for attempt in 1 2 3; do
    if DEBIAN_FRONTEND=noninteractive apt-get "$@" >>"$FAILLOG" 2>&1; then
      return 0
    fi
    if grep -qi 'could not get lock\|is another process using it\|dpkg frontend lock' "$FAILLOG"; then
      echo "  another process holds the apt lock; waiting 20s (try $attempt of 3)..."
      sleep 20
    else
      return 1
    fi
  done
  return 1
}

# Debian and Ubuntu ship python3 without pip. Try to add it rather than dying
# with "No module named pip", which is what used to happen here.
bootstrap_pip() {   # $1 = interpreter
  local py="$1"
  have_pip "$py" && return 0
  echo "  pip is missing for $py, trying to add it..."
  "$py" -m ensurepip --default-pip >>"$FAILLOG" 2>&1 || true
  have_pip "$py" && return 0
  if [[ $EUID -ne 0 ]]; then
    echo "  cannot install pip without root."
    return 1
  fi
  command -v apt-get >/dev/null 2>&1 || { echo "  no apt-get on this system."; return 1; }
  echo "  installing python3-pip with apt (this can take a minute)..."
  apt_try update -qq || echo "  'apt-get update' failed; trying the install anyway..."
  apt_try install -y -qq python3-pip || echo "  'apt-get install python3-pip' failed."
  have_pip "$py"
}

# A private virtualenv is the preferred home for the dependency. Installing into
# the system python means fighting whatever the distribution already put there:
# on Ubuntu 24.04 pip refuses to replace Debian's typing_extensions ("RECORD
# file not found"), and PEP 668 marks the whole interpreter externally managed.
# A venv sidesteps all of it, and touches nothing apt owns.
make_venv() {   # $1 = base interpreter, $2 = venv path
  local py="$1" dir="$2"
  "$py" -m venv "$dir" >>"$FAILLOG" 2>&1 && return 0
  # Debian and Ubuntu split venv into its own package.
  if [[ $EUID -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
    local pyver
    pyver="$("$py" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    echo "  installing python3-venv with apt..."
    apt_try update -qq || true
    apt_try install -y -qq python3-venv ${pyver:+"python$pyver-venv"} || true
    rm -rf "$dir"
    "$py" -m venv "$dir" >>"$FAILLOG" 2>&1 && return 0
  fi
  rm -rf "$dir"
  return 1
}

# Sets RUNTIME_PY to an interpreter that can import openai, or returns 1.
RUNTIME_PY=""
provide_openai() {   # $1 = interpreter students would otherwise use
  local py="$1" venv="$SHAREDIR/venv"

  # Already there (a distro package, or a previous run)? Nothing to do.
  if has_openai "$py"; then RUNTIME_PY="$py"; return 0; fi

  # Reuse a venv from an earlier install rather than rebuilding it.
  if [[ -x "$venv/bin/python3" ]] && "$venv/bin/python3" -c "import openai" 2>/dev/null; then
    RUNTIME_PY="$venv/bin/python3"; return 0
  fi

  echo "  setting up a private environment in $venv..."
  if make_venv "$py" "$venv"; then
    if "$venv/bin/python3" -m pip install --quiet --upgrade openai >>"$FAILLOG" 2>&1 \
       && "$venv/bin/python3" -c "import openai" 2>/dev/null; then
      chmod -R a+rX "$venv"
      RUNTIME_PY="$venv/bin/python3"; return 0
    fi
    echo "  the private environment did not work; falling back to $py."
  else
    echo "  could not create a private environment; falling back to $py."
  fi

  # Fall back to installing into the interpreter itself.
  if bootstrap_pip "$py"; then
    # PIP_USER=0 stops pip quietly installing into ~/.local when a shared
    # install is what was asked for.
    local opt
    for opt in "" "--break-system-packages" "--break-system-packages --ignore-installed"; do
      # shellcheck disable=SC2086
      if PIP_USER=0 "$py" -m pip install --quiet $opt openai >>"$FAILLOG" 2>&1 && has_openai "$py"; then
        RUNTIME_PY="$py"; return 0
      fi
    done
  fi

  # Some distributions package the module itself, which skips pip entirely.
  if [[ $EUID -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
    echo "  trying the distro package python3-openai instead..."
    if apt_try install -y -qq python3-openai && has_openai "$py"; then
      RUNTIME_PY="$py"; return 0
    fi
  fi
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

echo
echo "Setting up the one dependency (openai)..."
OPENAI_OK=1
provide_openai "$TARGET_PY" || OPENAI_OK=0

# Pin the interpreter. With `#!/usr/bin/env python3` the command resolves to
# whatever python3 is first on PATH, so it would work for an instructor inside
# conda and fail for every student. An absolute shebang pointing at the
# interpreter we just verified makes `ai` behave identically for everyone.
PINNED_PY="${RUNTIME_PY:-$TARGET_PY}"
if [[ -x "$PINNED_PY" ]] && { [[ "$MODE" == "system" ]] || [[ "$PINNED_PY" == "$SHAREDIR/venv/bin/python3" ]]; }; then
  sed -i "1s|^#!.*|#!$PINNED_PY|" "$SHAREDIR/ai"
fi

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
  echo "Checked: 'ai' runs on $PINNED_PY and can import openai."
  if [[ "$PINNED_PY" == "$SHAREDIR/venv/bin/python3" ]]; then
    echo "         (a private environment, so nothing apt or conda owns was touched)"
  fi
  echo
else
  echo "!! NOT FINISHED - the 'openai' package is still missing, so 'ai'"
  echo "!! will fail with \"No module named 'openai'\"."
  echo
  if [[ -s "$FAILLOG" ]]; then
    echo "What actually went wrong (last 12 lines):"
    echo "-----------------------------------------------------------------"
    tail -n 12 "$FAILLOG" | sed 's/^/  /'
    echo "-----------------------------------------------------------------"
    echo
  fi
  echo "$STEP. Build the private environment by hand so you see any error:"
  echo "     sudo apt update && sudo apt install -y python3-venv"
  echo "     sudo $TARGET_PY -m venv $SHAREDIR/venv"
  echo "     sudo $SHAREDIR/venv/bin/python3 -m pip install openai"
  echo "     sudo chmod -R a+rX $SHAREDIR/venv"
  echo "     sudo sed -i '1s|^#!.*|#!$SHAREDIR/venv/bin/python3|' $SHAREDIR/ai"
  echo
  echo "   If apt says a lock is held, wait a minute and try again -"
  echo "   Debian, Ubuntu and Mint all run their own updater for a while"
  echo "   after booting. If apt cannot reach a mirror, check the network."
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
