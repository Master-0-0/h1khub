#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/h1khub.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=H1KHub
Comment=数据库 / SSH / SFTP / Docker / AI 一体化工作台（复刻版）
Exec=$ROOT/scripts/start.sh
Icon=$ROOT/assets/h1khub.svg
Terminal=false
Categories=Development;Utility;Network;
StartupNotify=false
DESKTOP
chmod 644 "$APPS/h1khub.desktop"
echo "Installed: $APPS/h1khub.desktop"
