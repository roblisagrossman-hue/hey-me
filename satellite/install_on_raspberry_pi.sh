#!/bin/bash
# Installs the Hey Me listener on a Raspberry Pi (or any Debian/Ubuntu Linux box)
# and makes it start automatically. Run:  bash install_on_raspberry_pi.sh
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
echo "== Installing system parts =="
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip libportaudio2 mpv espeak-ng
echo "== Installing Python parts =="
python3 -m venv "$DIR/venv"
"$DIR/venv/bin/pip" install --upgrade pip
"$DIR/venv/bin/pip" install -r "$DIR/requirements.txt"
echo "== Making Hey Me start on boot =="
sudo tee /etc/systemd/system/heyme.service > /dev/null <<EOT
[Unit]
Description=Hey Me listener
After=network-online.target sound.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python $DIR/hey_me_satellite.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u)

[Install]
WantedBy=multi-user.target
EOT
sudo systemctl daemon-reload
sudo systemctl enable --now heyme
echo
echo "Done! Hey Me is running. To watch what it's doing:  journalctl -u heyme -f"
echo "After changing settings in hey_me_satellite.py:     sudo systemctl restart heyme"
