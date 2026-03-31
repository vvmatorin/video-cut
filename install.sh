#!/usr/bin/env bash
set -euo pipefail

# ── Install & run video-cut on macOS ──────────────────────────────────────────

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "→ $*"; }

[[ "$(uname)" == "Darwin" ]] || die "This script is for macOS only."

# Homebrew
if ! command -v brew &>/dev/null; then
    info "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    eval "$(/opt/homebrew/bin/brew shellenv 2>/dev/null || /usr/local/bin/brew shellenv)"
fi

# System dependencies: mpv (provides libmpv) and ffmpeg (provides ffprobe)
for pkg in mpv ffmpeg; do
    if ! brew list "$pkg" &>/dev/null; then
        info "Installing $pkg..."
        brew install "$pkg"
    else
        info "$pkg already installed."
    fi
done

# uv (Python project manager)
if ! command -v uv &>/dev/null; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Python environment & dependencies
cd "$(dirname "$0")"
info "Syncing Python dependencies..."
uv sync --frozen

# Run
info "Launching video-cut..."
exec uv run python main.py
