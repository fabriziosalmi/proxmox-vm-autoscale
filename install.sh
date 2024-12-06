#!/bin/bash
# Install script for Proxmox VM Autoscale project
# Repository: https://github.com/fabriziosalmi/proxmox-vm-autoscale

# Variables
INSTALL_DIR="/usr/local/bin/vm_autoscale"
BACKUP_DIR="/etc/vm_autoscale"  # New separate backup directory
REPO_URL="https://github.com/fabriziosalmi/proxmox-vm-autoscale"
SERVICE_FILE="vm_autoscale.service"
CONFIG_FILE="$INSTALL_DIR/config.yaml"
BACKUP_FILE="$BACKUP_DIR/config.yaml.backup"  # Updated backup location
REQUIREMENTS_FILE="$INSTALL_DIR/requirements.txt"
PYTHON_CMD="/usr/bin/python3"

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run this script as root."
    exit 1
fi

# Create backup directory if it doesn't exist
if [ ! -d "$BACKUP_DIR" ]; then
    echo "Creating backup directory..."
    mkdir -p "$BACKUP_DIR" || { echo "ERROR: Failed to create backup directory"; exit 1; }
fi

# Backup existing config.yaml if it exists
if [ -f "$CONFIG_FILE" ]; then
    echo "Backing up existing config.yaml to $BACKUP_FILE..."
    cp "$CONFIG_FILE" "$BACKUP_FILE" || { echo "ERROR: Failed to backup config.yaml"; exit 1; }
fi

# Install necessary dependencies
echo "Installing necessary dependencies..."
apt-get update || { echo "ERROR: Failed to update package lists"; exit 1; }
apt-get install -y python3 curl bash git python3-paramiko python3-yaml python3-requests python3-cryptography \
    || { echo "ERROR: Failed to install required packages"; exit 1; }

# Clone the repository
echo "Cloning the repository..."
if [ -d "$INSTALL_DIR" ]; then
    echo "Removing existing installation directory..."
    rm -rf "$INSTALL_DIR" || { echo "ERROR: Failed to remove existing directory $INSTALL_DIR"; exit 1; }
fi

git clone "$REPO_URL" "$INSTALL_DIR" || { echo "ERROR: Failed to clone the repository from $REPO_URL"; exit 1; }

# Restore backup if it exists
if [ -f "$BACKUP_FILE" ]; then
    echo "Restoring config.yaml from backup..."
    cp "$BACKUP_FILE" "$CONFIG_FILE" || { echo "ERROR: Failed to restore config.yaml from backup"; exit 1; }
fi

# Install Python dependencies
if [ -f "$REQUIREMENTS_FILE" ]; then
    echo "Installing Python dependencies..."
    pip3 install -r "$REQUIREMENTS_FILE" || { echo "ERROR: Failed to install Python dependencies"; exit 1; }
else
    echo "WARNING: Requirements file not found. Skipping Python dependency installation."
fi

# Set permissions
echo "Setting permissions for installation directory..."
chmod -R 755 "$INSTALL_DIR" || { echo "ERROR: Failed to set permissions on $INSTALL_DIR"; exit 1; }
chmod -R 755 "$BACKUP_DIR" || { echo "ERROR: Failed to set permissions on $BACKUP_DIR"; exit 1; }

# Create the systemd service file
echo "Creating the systemd service file..."
cat <<EOF > /etc/systemd/system/$SERVICE_FILE
[Unit]
Description=Proxmox VM Autoscale Service
After=network.target

[Service]
ExecStart=$PYTHON_CMD $INSTALL_DIR/autoscale.py
WorkingDirectory=$INSTALL_DIR
Restart=always
User=root
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

if [ $? -ne 0 ]; then
    echo "ERROR: Failed to create systemd service file at /etc/systemd/system/$SERVICE_FILE"
    exit 1
fi

# Reload systemd, enable the service, and ensure it's not started
echo "Reloading systemd and enabling the service..."
systemctl daemon-reload || { echo "ERROR: Failed to reload systemd"; exit 1; }
systemctl enable "$SERVICE_FILE" || { echo "ERROR: Failed to enable the service"; exit 1; }

# Post-installation instructions
echo "Installation complete. The service is enabled but not started."
echo "To start the service, use: sudo systemctl start $SERVICE_FILE"
echo "Logs can be monitored using: journalctl -u $SERVICE_FILE -f"
echo "Config backup location: $BACKUP_FILE"
