#!/bin/sh
# Register the collector as a systemd user service.
#
#   sh collector/linux/install.sh [/path/to/python3]
#
# The committed unit in collector/systemd/ hardcodes a checkout path, which is
# wrong on most machines. This generates one against wherever the repository
# actually is, and creates the environment file the unit reads.

set -eu

collector=$(cd "$(dirname "$0")/.." && pwd)
python=${1:-$(command -v python3)}
unit="$HOME/.config/systemd/user/activity-collector.service"
config="$HOME/.config/activity-collector"

case "$("$python" -c 'import sys; print(sys.version_info >= (3, 11))')" in
  True) ;;
  *) echo "need python 3.11 or newer, $python is $("$python" -V 2>&1)" >&2; exit 1 ;;
esac

mkdir -p "$(dirname "$unit")" "$config"

if [ ! -f "$config/env" ]; then
  cat > "$config/env" <<'ENV'
# Generic device label, never a personal or employer name.
ACTIVITY_MACHINE_ID=personal-linux
# Leave the next two unset to spool locally without uploading anything.
# Comma-separate addresses to give one sink a backup, most preferred first.
#ACTIVITY_INGEST_URL=http://homeassistant.local:8788/api/ingest
#ACTIVITY_WRITE_TOKEN=
#ACTIVITY_INTERVAL_SECONDS=30
ENV
  chmod 600 "$config/env"
  echo "wrote $config/env, edit it before the collector uploads anything"
fi

cat > "$unit" <<UNIT
[Unit]
Description=Local privacy-preserving activity collector
After=graphical-session.target

[Service]
Type=simple
WorkingDirectory=$collector
Environment=PYTHONPATH=$collector/src
EnvironmentFile=-$config/env
ExecStart=$python -m activity_collector
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=default.target
UNIT

systemctl --user daemon-reload
systemctl --user enable --now activity-collector.service

echo "installed $unit"
echo "state:      systemctl --user status activity-collector"
echo "logs:       journalctl --user -u activity-collector -f"
echo "uninstall:  systemctl --user disable --now activity-collector && rm $unit"
