#!/bin/bash
# =============================================================================
# wuji-hand-studio — macOS launcher            scripts/run-macos.command
#
# Usage
#   chmod +x scripts/run-macos.command
#   ./scripts/run-macos.command [--self-check] [--render-check] [...app args]
#   (or simply double-click the file in Finder)
#
# What it does
#   1. Locates the clone this file lives in (safe with spaces / Unicode paths).
#   2. Finds a Python 3.10–3.13 interpreter on PATH, in standard Homebrew
#      locations, or in python.org framework locations — and verifies the
#      version BEFORE touching anything.
#   3. Creates / reuses ONLY <clone>/.venv-macos.
#   4. Installs requirements.txt only when its SHA-256 or the interpreter
#      identity changed (stamp file stored inside the venv).
#   5. exec's src/desktop.py, forwarding every CLI argument unchanged.
#
# What it never does
#   No Homebrew/Python/system install. No sudo. No permission grants. No
#   Gatekeeper/quarantine changes. No SSH key creation. No auto-connect and no
#   hardware start. It never `source`s any file (no activate scripts).
#
# Hardware note
#   The robot-hand SDK/CLI officially targets Ubuntu 22.04+. This launcher
#   starts the local loopback UI / MuJoCo preview only. It neither provides nor
#   implies native macOS hand control.
#
# Environment toggles (all optional, all read-only knobs for this script)
#   WUJI_PYTHON=/path/to/python3.12   pin the base interpreter (still verified)
#   WUJI_FORCE_INSTALL=1              reinstall requirements even if stamp matches
#   WUJI_SKIP_INSTALL=1               never touch the network (offline runs)
#   WUJI_DRY_RUN=1                    prepare everything, print plan, do not launch
#   WUJI_NO_PAUSE=1                   do not wait for Return on error (CI)
#   WUJI_ALLOW_NON_DARWIN=1           allow running the script off macOS (testing)
#
# Exit codes
#   0 ok · 10 bad layout/environment · 11 no supported Python · 12 venv rejected
#   13 venv creation failed · 14 dependency install failed · 15 launch failed
# =============================================================================

set -u
set -o pipefail

SCHEMA_VERSION="1"
VENV_NAME=".venv-macos"
PY_MIN_MINOR=10
PY_MAX_MINOR=13
APP_REL="src/desktop.py"
REQ_REL="requirements.txt"
NL=$'\n'

# ---------------------------------------------------------------- output ----
if [ -t 2 ] && [ -z "${NO_COLOR:-}" ]; then
    C_B=$'\033[1;34m'; C_Y=$'\033[1;33m'; C_R=$'\033[1;31m'; C_G=$'\033[1;32m'; C_0=$'\033[0m'
else
    C_B=''; C_Y=''; C_R=''; C_G=''; C_0=''
fi

info() { printf '%s %s\n' "${C_B}[wuji]${C_0}" "$*" >&2; }
good() { printf '%s %s\n' "${C_G}[wuji]${C_0}" "$*" >&2; }
warn() { printf '%s %s\n' "${C_Y}[wuji][warn]${C_0}" "$*" >&2; }
oops() { printf '%s %s\n' "${C_R}[wuji][error]${C_0}" "$*" >&2; }

hold_window() {
    # Keeps a Finder-launched Terminal window readable after a failure.
    if [ -t 0 ] && [ -z "${WUJI_NO_PAUSE:-}" ]; then
        printf '\n%s' "Press Return to close / 按回车键关闭…… " >&2
        IFS= read -r _dummy || true
    fi
}

die() {
    # die <exit-code> <message...>
    local code="$1"; shift
    oops "$*"
    hold_window
    exit "$code"
}

# ----------------------------------------------------------- path helpers ---
abs_dir() {
    # Physical absolute path of an existing directory (no GNU realpath needed).
    ( cd -P -- "$1" >/dev/null 2>&1 && pwd -P ) || return 1
}

path_inside() {
    # path_inside <candidate> <root>  -> 0 when candidate is root or below it
    case "$1" in
        "$2")   return 0 ;;
        "$2"/*) return 0 ;;
        *)      return 1 ;;
    esac
}

resolve_self_dir() {
    # Follows symlink chains with plain POSIX readlink (no readlink -f).
    local src="" dir="" target="" hops=0
    src="${BASH_SOURCE[0]:-$0}"
    while [ -L "$src" ]; do
        hops=$((hops + 1))
        if [ "$hops" -gt 40 ]; then return 1; fi
        dir=$(cd -P -- "$(dirname -- "$src")" >/dev/null 2>&1 && pwd -P) || return 1
        target=$(readlink -- "$src") || return 1
        case "$target" in
            /*) src="$target" ;;
            *)  src="$dir/$target" ;;
        esac
    done
    ( cd -P -- "$(dirname -- "$src")" >/dev/null 2>&1 && pwd -P ) || return 1
}

# ------------------------------------------------------------ probe code ----
PY_PROBE='import sys
try:
    import platform, importlib.util as _u
except Exception:
    raise SystemExit(3)
v = sys.version_info
venv_ok = "1" if _u.find_spec("venv") else "0"
pip_ok = "1" if _u.find_spec("ensurepip") else "0"
sys.stdout.write("%d %d %s %s %s %s\n" % (v[0], v[1], platform.machine(), venv_ok, pip_ok, sys.executable))'

VENV_PROBE='import sys, os, platform
sys.stdout.write("%d.%d\n" % sys.version_info[:2])
sys.stdout.write(os.path.realpath(sys.prefix) + "\n")
sys.stdout.write(os.path.realpath(sys.base_prefix) + "\n")
sys.stdout.write(platform.machine() + "\n")'

ID_PROBE='import sys, os, platform
sys.stdout.write("%s|%d.%d.%d|%s|%s\n" % (
    sys.implementation.name,
    sys.version_info[0], sys.version_info[1], sys.version_info[2],
    platform.machine(), os.path.realpath(sys.base_prefix)))'

# ------------------------------------------------------------- platform -----
if [ "$(uname -s 2>/dev/null || echo unknown)" != "Darwin" ]; then
    if [ -z "${WUJI_ALLOW_NON_DARWIN:-}" ]; then
        die 10 "This launcher is for macOS. (bash -n syntax checks work anywhere; set WUJI_ALLOW_NON_DARWIN=1 to override.)"
    fi
    warn "Not running on macOS — continuing because WUJI_ALLOW_NON_DARWIN=1."
fi

command -v shasum >/dev/null 2>&1 || die 10 "'shasum' not found in PATH; it ships with macOS."

# -------------------------------------------------------------- locate ------
SCRIPT_DIR=$(resolve_self_dir) || die 10 "Cannot resolve the location of this script."
PROJECT_ROOT=$(abs_dir "$SCRIPT_DIR/..") || die 10 "Cannot resolve the project root above: $SCRIPT_DIR"

APP_ENTRY="$PROJECT_ROOT/$APP_REL"
REQ_FILE="$PROJECT_ROOT/$REQ_REL"
VENV_DIR="$PROJECT_ROOT/$VENV_NAME"
STAMP_FILE="$VENV_DIR/.wuji-deps-stamp"

[ -f "$APP_ENTRY" ] || die 10 "Missing $APP_REL under: $PROJECT_ROOT (run this script from inside the clone)."
[ -f "$REQ_FILE" ]  || die 10 "Missing $REQ_REL under: $PROJECT_ROOT"

info "Project root : $PROJECT_ROOT"
info "Virtualenv   : $VENV_DIR"

# ------------------------------------------------- environment hygiene ------
# Nothing outside this process is modified; we only stop foreign settings from
# leaking into our venv. No activate script is ever sourced.
unset VIRTUAL_ENV PYTHONHOME PYTHONPATH PYTHONSTARTUP
unset PIP_TARGET PIP_PREFIX PIP_USER PIP_ROOT_USER_ACTION
export PYTHONNOUSERSITE=1
export PIP_DISABLE_PIP_VERSION_CHECK=1
export PIP_NO_INPUT=1
export PIP_REQUIRE_VIRTUALENV=1   # hard guard: pip may only install into a venv

HOST_ARCH=$(uname -m 2>/dev/null || echo unknown)

# ------------------------------------------------- interpreter discovery ----
CANDIDATES=()
SEEN="$NL"

add_candidate() {
    local p="${1:-}"
    [ -n "$p" ] || return 0
    case "$SEEN" in *"$NL$p$NL"*) return 0 ;; esac
    SEEN="$SEEN$p$NL"
    CANDIDATES+=("$p")
}

collect_candidates() {
    local v="" prefix="" brew=""
    if [ -n "${WUJI_PYTHON:-}" ]; then add_candidate "$WUJI_PYTHON"; return; fi
    for v in 3.13 3.12 3.11 3.10; do
        add_candidate "$(command -v "python$v" 2>/dev/null || true)"
    done
    for brew in "${HOMEBREW_PREFIX:-}" /opt/homebrew /usr/local; do
        [ -n "$brew" ] || continue
        for v in 3.13 3.12 3.11 3.10; do
            add_candidate "$brew/bin/python$v"
            add_candidate "$brew/opt/python@$v/bin/python$v"
        done
    done
    for v in 3.13 3.12 3.11 3.10; do
        add_candidate "/Library/Frameworks/Python.framework/Versions/$v/bin/python$v"
    done
    add_candidate "$(command -v python3 2>/dev/null || true)"
}

PY_BASE=""
PY_BASE_VER=""
PY_BASE_MACH=""

find_interpreter() {
    local c="" out="" maj="" min="" mach="" hasvenv="" haspip="" exe=""
    collect_candidates
    if [ "${#CANDIDATES[@]}" -eq 0 ]; then return 1; fi
    for c in "${CANDIDATES[@]}"; do
        [ -x "$c" ] || continue
        if [ "$c" = "/usr/bin/python3" ]; then
            # Apple's stub is 3.9.x (unsupported) and may pop the Command Line
            # Tools installer. Skipped on purpose; we never install anything.
            continue
        fi
        out=$("$c" -I -c "$PY_PROBE" 2>/dev/null) || continue
        maj=""; min=""; mach=""; hasvenv=""; haspip=""; exe=""
        IFS=' ' read -r maj min mach hasvenv haspip exe <<EOF
$out
EOF
        case "$maj$min" in ''|*[!0-9]*) continue ;; esac
        [ "$maj" -eq 3 ] || continue
        [ "$min" -ge "$PY_MIN_MINOR" ] || continue
        [ "$min" -le "$PY_MAX_MINOR" ] || continue
        [ "$hasvenv" = "1" ] || { warn "Skipping $c — the 'venv' module is unavailable."; continue; }
        [ "$haspip" = "1" ]  || { warn "Skipping $c — the 'ensurepip' module is unavailable."; continue; }
        PY_BASE="$c"
        PY_BASE_VER="$maj.$min"
        PY_BASE_MACH="$mach"
        return 0
    done
    return 1
}

# ------------------------------------------------------ venv validation -----
VENV_PY=""
V_VER=""; V_PREFIX=""; V_BASE=""; V_MACH=""

validate_existing_venv() {
    local real="" out=""
    if [ -L "$VENV_DIR" ]; then
        oops "$VENV_NAME is a symlink; a symlinked environment is never reused."
        return 1
    fi
    if [ ! -d "$VENV_DIR" ]; then
        oops "$VENV_NAME exists but is not a directory."
        return 1
    fi
    real=$(abs_dir "$VENV_DIR") || { oops "Cannot resolve $VENV_NAME."; return 1; }
    if ! path_inside "$real" "$PROJECT_ROOT"; then
        oops "$VENV_NAME resolves outside this clone: $real"
        return 1
    fi
    [ -f "$VENV_DIR/pyvenv.cfg" ] || { oops "$VENV_NAME has no pyvenv.cfg (not a virtual environment)."; return 1; }
    VENV_PY="$VENV_DIR/bin/python3"
    [ -x "$VENV_PY" ] || { oops "$VENV_NAME/bin/python3 is missing or not executable."; return 1; }

    out=$("$VENV_PY" -c "$VENV_PROBE" 2>/dev/null) || {
        oops "$VENV_NAME/bin/python3 cannot run (its base interpreter may have been removed or upgraded)."
        return 1
    }
    V_VER=""; V_PREFIX=""; V_BASE=""; V_MACH=""
    { IFS= read -r V_VER; IFS= read -r V_PREFIX; IFS= read -r V_BASE; IFS= read -r V_MACH; } <<EOF
$out
EOF
    if [ "$V_PREFIX" = "$V_BASE" ]; then
        oops "$VENV_NAME is not an isolated virtual environment (sys.prefix == sys.base_prefix)."
        return 1
    fi
    if [ "$V_PREFIX" != "$VENV_DIR" ]; then
        oops "$VENV_NAME points at an environment outside this clone: $V_PREFIX"
        return 1
    fi
    local vmin="${V_VER#*.}"
    case "$vmin" in ''|*[!0-9]*) oops "Unreadable Python version in $VENV_NAME: $V_VER"; return 1 ;; esac
    if [ "${V_VER%%.*}" != "3" ] || [ "$vmin" -lt "$PY_MIN_MINOR" ] || [ "$vmin" -gt "$PY_MAX_MINOR" ]; then
        oops "$VENV_NAME uses Python $V_VER, outside the supported 3.$PY_MIN_MINOR–3.$PY_MAX_MINOR range."
        return 1
    fi
    return 0
}

reject_venv_and_exit() {
    oops "Existing .venv-macos is invalid; it has been left unchanged. Rename it and run again."
    hold_window
    exit 12
}

create_venv() {
    find_interpreter || die 11 "No supported Python found (need 3.$PY_MIN_MINOR–3.$PY_MAX_MINOR). Install one yourself (e.g. Homebrew python@3.12 or a python.org installer); this script never installs software."
    good "Base interpreter: $PY_BASE (Python $PY_BASE_VER, $PY_BASE_MACH)"
    if [ "$HOST_ARCH" = "arm64" ] && [ "$PY_BASE_MACH" != "arm64" ]; then
        warn "This Mac is arm64 but the interpreter reports '$PY_BASE_MACH' (likely Rosetta). Prefer a native arm64 Python for MuJoCo wheels."
    fi
    info "Creating $VENV_NAME …"
    if ! "$PY_BASE" -I -m venv "$VENV_DIR"; then
        die 13 "Virtual environment creation failed; partial files were left for inspection."
    fi
    VENV_PY="$VENV_DIR/bin/python3"
    [ -x "$VENV_PY" ] || die 13 "Virtual environment created but $VENV_NAME/bin/python3 is missing."
}

if [ -e "$VENV_DIR" ] || [ -L "$VENV_DIR" ]; then
    if validate_existing_venv; then
        good "Reusing $VENV_NAME (Python $V_VER, $V_MACH)"
    else
        reject_venv_and_exit
    fi
else
    create_venv
fi

# --------------------------------------------------- dependency stamping ----
compute_stamp() {
    local req_hash="" py_id=""
    req_hash=$(shasum -a 256 -- "$REQ_FILE" | cut -c1-64) || return 1
    [ -n "$req_hash" ] || return 1
    py_id=$("$VENV_PY" -c "$ID_PROBE" 2>/dev/null) || return 1
    printf 'schema=%s\nrequirements_sha256=%s\npython=%s\n' "$SCHEMA_VERSION" "$req_hash" "$py_id"
}

NEW_STAMP=$(compute_stamp) || die 14 "Could not fingerprint $REQ_REL / the interpreter."
OLD_STAMP=""
if [ -f "$STAMP_FILE" ]; then
    OLD_STAMP=$(cat -- "$STAMP_FILE" 2>/dev/null || true)
fi

NEED_INSTALL=1
if [ "$NEW_STAMP" = "$OLD_STAMP" ] && [ "${WUJI_FORCE_INSTALL:-}" != "1" ]; then
    NEED_INSTALL=0
fi

if [ "$NEED_INSTALL" -eq 0 ]; then
    good "Dependencies up to date (stamp match) — skipping install."
elif [ "${WUJI_SKIP_INSTALL:-}" = "1" ]; then
    warn "WUJI_SKIP_INSTALL=1 — requirements changed but no install will be attempted."
else
    info "Installing $REQ_REL into $VENV_NAME (network access required) …"
    rm -f "$STAMP_FILE"   # never leave a stale 'installed' marker behind
    if ! "$VENV_PY" -m pip install --upgrade pip; then
        warn "Could not upgrade pip inside the venv; continuing with the bundled pip."
    fi
    if ! "$VENV_PY" -m pip install -r "$REQ_FILE"; then
        printf '\n' >&2
        oops "Dependency installation failed. The virtual environment is left as-is for inspection."
        info "Common causes: no network, no wheel for this Python/arch, or a partial download."
        info "Retry with:  WUJI_FORCE_INSTALL=1 \"$0\""
        info "For a clean setup, rename .venv-macos and run again."
        hold_window
        exit 14
    fi
    printf '%s\n' "$NEW_STAMP" > "$STAMP_FILE" || warn "Installed successfully but the stamp file could not be written; the next run will reinstall."
    good "Dependencies installed."
fi

# ----------------------------------------------------------------- launch ---
export VIRTUAL_ENV="$VENV_DIR"
PATH="$VENV_DIR/bin:$PATH"; export PATH
export PYTHONUNBUFFERED=1

cd "$PROJECT_ROOT" || die 15 "Could not change directory to $PROJECT_ROOT"

if [ "$#" -gt 0 ]; then
    info "App arguments: $*"
else
    info "App arguments: (none)"
fi

if [ "${WUJI_DRY_RUN:-}" = "1" ]; then
    good "Dry run complete — environment is ready; $APP_REL was NOT launched."
    info "python : $VENV_PY"
    info "stamp  : $STAMP_FILE"
    exit 0
fi

good "Launching $APP_REL …  (the app owns all runtime state; no hardware is contacted by this script)"
exec "$VENV_PY" "$APP_ENTRY" "$@"

# Unreachable: exec replaced this shell.
die 15 "exec failed for $APP_ENTRY"
