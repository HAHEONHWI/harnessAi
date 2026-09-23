#!/usr/bin/env bash
# Installs the latest HarnessLoop release from GitHub:
#   curl -fsSL https://raw.githubusercontent.com/HAHEONHWI/harnessAi/main/scripts/install.sh | bash
set -euo pipefail

repo="${AI_HARNESS_REPO:-HAHEONHWI/harnessAi}"
install_dir="${INSTALL_DIR:-/Applications}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

curl -fsSL -o "$tmp/app.zip" "https://github.com/$repo/releases/latest/download/HarnessLoop-macOS.zip"
ditto -x -k "$tmp/app.zip" "$tmp"
rm -rf "$install_dir/HarnessLoop.app" "$install_dir/AI Harness.app"  # also drop the pre-rename app
mv "$tmp/HarnessLoop.app" "$install_dir/"
# The app is ad-hoc signed, not notarized; clear the download quarantine so Gatekeeper allows it.
xattr -dr com.apple.quarantine "$install_dir/HarnessLoop.app" 2>/dev/null || true

engine_dir="$HOME/.ai-harness/bin"
mkdir -p "$engine_dir"
install -m 755 "$install_dir/HarnessLoop.app/Contents/Resources/harness.py" "$engine_dir/harness.py"
"$engine_dir/harness.py" init
printf 'Installed %s/HarnessLoop.app\n' "$install_dir"
