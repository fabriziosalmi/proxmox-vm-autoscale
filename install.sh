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
# Note: vm_autoscale.service hardcodes /usr/bin/python3 and the paths above.
# If you change INSTALL_DIR, edit the unit file to match.

# Ensure the script is run as root
if [ "$EUID" -ne 0 ]; then
    echo "ERROR: Please run this script as root."
    exit 1
fi

# Create backup directory if it doesn't exist
if [ ! -d "$BACKUP_DIR" ]; then
    echo "Creating backup directory..."
    mkdir -p "$BACKUP_DIR" || { echo "ERROR: Failed to create backup directory"; exit 1; }
    chmod 700 "$BACKUP_DIR" || { echo "ERROR: Failed to set permissions on $BACKUP_DIR"; exit 1; }
fi

# Backup existing config.yaml if it exists
if [ -f "$CONFIG_FILE" ]; then
    echo "Backing up existing config.yaml to $BACKUP_FILE..."
    cp "$CONFIG_FILE" "$BACKUP_FILE" || { echo "ERROR: Failed to backup config.yaml"; exit 1; }
    chmod 600 "$BACKUP_FILE" || { echo "ERROR: Failed to restrict permissions on $BACKUP_FILE"; exit 1; }
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

# Install Python dependencies.
# The apt step above already provides paramiko, PyYAML and requests as system
# packages, so this is a best-effort top-up. On Debian 12+ and Proxmox VE 8 pip
# refuses to touch the system interpreter (PEP 668,
# "externally-managed-environment"); treating that as fatal would abort an
# otherwise complete installation.
if [ -f "$REQUIREMENTS_FILE" ]; then
    echo "Installing Python dependencies..."
    if ! pip3 install -r "$REQUIREMENTS_FILE"; then
        echo "NOTICE: pip could not install into the system interpreter."
        echo "        This is expected on Proxmox VE 8 / Debian 12+ (PEP 668)."
        echo "        Continuing: the required packages were installed via apt."
    fi
else
    echo "WARNING: Requirements file not found. Skipping Python dependency installation."
fi

# Set permissions
echo "Setting permissions for installation directory..."
chmod -R 755 "$INSTALL_DIR" || { echo "ERROR: Failed to set permissions on $INSTALL_DIR"; exit 1; }

# config.yaml holds SSH and SMTP credentials in plain text: it must never be
# world-readable, so it is locked down after the recursive chmod above.
chmod 700 "$BACKUP_DIR" || { echo "ERROR: Failed to set permissions on $BACKUP_DIR"; exit 1; }
if [ -f "$CONFIG_FILE" ]; then
    chown root:root "$CONFIG_FILE" || { echo "ERROR: Failed to set owner on $CONFIG_FILE"; exit 1; }
    chmod 600 "$CONFIG_FILE" || { echo "ERROR: Failed to restrict permissions on $CONFIG_FILE"; exit 1; }
fi
if [ -f "$BACKUP_FILE" ]; then
    chown root:root "$BACKUP_FILE" || { echo "ERROR: Failed to set owner on $BACKUP_FILE"; exit 1; }
    chmod 600 "$BACKUP_FILE" || { echo "ERROR: Failed to restrict permissions on $BACKUP_FILE"; exit 1; }
fi

# Install the systemd unit shipped in the repository.
# It is deliberately the same file that is version-controlled and documented,
# rather than a copy generated here: the two had drifted, and the generated one
# was missing RestartSec, so a crash-looping service restarted as fast as
# systemd allowed.
echo "Installing the systemd service file..."
if [ ! -f "$INSTALL_DIR/$SERVICE_FILE" ]; then
    echo "ERROR: $SERVICE_FILE not found in $INSTALL_DIR"
    exit 1
fi

install -m 644 -o root -g root "$INSTALL_DIR/$SERVICE_FILE" "/etc/systemd/system/$SERVICE_FILE" \
    || { echo "ERROR: Failed to install systemd service file at /etc/systemd/system/$SERVICE_FILE"; exit 1; }

# Reload systemd, enable the service, and ensure it's not started
echo "Reloading systemd and enabling the service..."
systemctl daemon-reload || { echo "ERROR: Failed to reload systemd"; exit 1; }
systemctl enable "$SERVICE_FILE" || { echo "ERROR: Failed to enable the service"; exit 1; }

# Post-installation instructions
echo "Installation complete. The service is enabled but not started."
echo "To start the service, use: sudo systemctl start $SERVICE_FILE"
echo "Logs can be monitored using: journalctl -u $SERVICE_FILE -f"
echo "Config backup location: $BACKUP_FILE"
echo "NOTE: $CONFIG_FILE is mode 600 (root only) because it stores SSH and SMTP credentials."
