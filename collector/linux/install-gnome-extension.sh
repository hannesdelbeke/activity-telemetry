#!/bin/bash
set -eu

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UUID="activity-collector@hannesdelbeke.github.io"
DEST="$HOME/.local/share/gnome-shell/extensions/$UUID"

if ! command -v gnome-extensions >/dev/null 2>&1; then
    echo "error: gnome-extensions not found. Install gnome-shell-extensions or run on a GNOME system." >&2
    exit 1
fi

echo "Installing GNOME Shell extension to $DEST"
mkdir -p "$DEST"
cp "$SCRIPT_DIR/gnome-extension/metadata.json" "$DEST/"
cp "$SCRIPT_DIR/gnome-extension/extension.js" "$DEST/"

echo "Enabling extension $UUID"
gnome-extensions enable "$UUID" || true

echo ""
echo "Installation complete."
echo ""
echo "On Wayland, you must log out and log back in for the extension to load."
echo "On X11, you can restart the shell with Alt+F2, then type 'r' and press Enter."
