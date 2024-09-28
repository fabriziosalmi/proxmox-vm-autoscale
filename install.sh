#!/bin/bash

# Install script for Proxmox VM Autoscale project
# Repository: https://github.com/fabriziosalmi/proxmox-vm-autoscale

# Variables
INSTALL_DIR="/usr/local/bin/vm_autoscale"
REPO_URL="https://github.com/fabriziosalmi/proxmox-vm-autoscale"
SERVICE_FILE="vm_autoscale.service"

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "Please run as root"
    exit 1
fi

# Install necessary dependencies
echo "Installing necessary dependencies..."
apt-get update
apt-get install -y python3 curl bash git python3-paramiko python3-yaml python3-requests python3-cryptography

# Clone the repository
echo "Cloning the repository..."
if [ -d "$INSTALL_DIR" ]; then
    echo "Removing existing installation directory..."
    rm -rf "$INSTALL_DIR"
fi

git clone "$REPO_URL" "$INSTALL_DIR"

# Install Python dependencies
echo "Installing Python dependencies..."
pip3 install -r "$INSTALL_DIR/requirements.txt"

# Set permissions
echo "Setting permissions..."
chmod -R 755 "$INSTALL_DIR"

# Create the systemd service file
echo "Creating the systemd service file..."
cat <<EOF > /etc/systemd/system/$SERVICE_FILE
[Unit]
Description=Proxmox VM Autoscale Service
After=network.target

[Service]
ExecStart=/usr/bin/python3 $INSTALL_DIR/autoscale.py
WorkingDirectory=$INSTALL_DIR
Restart=always
User=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd, enable the service, and ensure it's not started
echo "Reloading systemd, enabling the service..."
systemctl daemon-reload
systemctl enable $SERVICE_FILE

# Post-installation instructions
echo "Installation complete. The service is enabled but not started."
echo "To start the service, use: sudo systemctl start $SERVICE_FILE"
echo "Logs can be monitored using: journalctl -u $SERVICE_FILE -f"

