# Proxmox VM Autoscale

## Overview
Proxmox VM Autoscale is a service that automatically scales Proxmox virtual machine (VM) resources (CPU cores, RAM) based on user-defined thresholds and conditions. It ensures efficient allocation of resources, dynamically adjusting VM parameters to optimize load and availability.

The service is designed to operate across multiple Proxmox hosts, connecting via SSH, and can be easily installed and managed as a system service.

## Features
- Auto-scaling of Proxmox VM CPU and RAM resources based on real-time usage.
- Configuration-driven via a YAML file.
- Handles multiple Proxmox hosts using SSH (supports password and key-based authentication).
- Gotify notifications for alerting when scaling actions are taken.
- Systemd integration for easy service management.

## Prerequisites
- Proxmox VE installed on the target machines.
- Python 3.x installed on the Proxmox host(s).
- Basic understanding of Proxmox `pct` commands and SSH.

## Installation

### Step 1: Install Dependencies
Run the following commands to install the necessary system-level dependencies:

```bash
apt update
apt install -y python3 python3-pip python3-paramiko python3-yaml python3-requests python3-cryptography git
```

### Step 2: Install Using `curl bash`
To easily install Proxmox VM Autoscale, you can use a `curl bash` command, which will automatically clone the repository, run the installation script, and set up the service for you.

```bash
bash <(curl -s https://raw.githubusercontent.com/fabriziosalmi/proxmox-vm-autoscale/main/install.sh)
```

This script will:
- Clone the repository into `/usr/local/bin/vm_autoscale`.
- Copy all necessary files to the installation directory.
- Install Python dependencies.
- Set up a systemd unit file to manage the autoscaling service.

### Step 3: Enable the Service
Enable the autoscale service using systemctl:

```bash
systemctl enable vm_autoscale.service
```

> **Note**: The service is enabled but not started automatically at the end of installation. You can start it manually using the command below.

## Usage

### Start/Stop the Service
To start the autoscaling service:

```bash
sudo systemctl start vm_autoscale.service
```

To stop the service:

```bash
sudo systemctl stop vm_autoscale.service
```

### Check the Status
To check the status of the service:

```bash
systemctl status vm_autoscale.service
```

### Logs
Logs are saved to `/var/log/vm_autoscale.log`. You can monitor logs using:

```bash
tail -f /var/log/vm_autoscale.log
```

or using `journalctl`:

```bash
journalctl -u vm_autoscale.service -f
```

## Configuration

The configuration file (`config.yaml`) is located in `/usr/local/bin/vm_autoscale/config.yaml`. This file defines the scaling thresholds, resource limits, host details, and VM information.

### Example Configuration
```yaml
scaling_thresholds:
  cpu:
    high: 80
    low: 20
  ram:
    high: 85
    low: 25

scaling_limits:
  min_cores: 1
  max_cores: 8
  min_ram_mb: 512
  max_ram_mb: 16384

check_interval: 60

proxmox_hosts:
  - name: host1
    host: 192.168.1.10
    ssh_user: root
    ssh_password: your_password_here
    ssh_key: /path/to/ssh_key

virtual_machines:
  - vm_id: 101
    proxmox_host: host1
    scaling_enabled: true
    cpu_scaling: true
    ram_scaling: true

logging:
  level: INFO
  log_file: /var/log/vm_autoscale.log

gotify:
  enabled: true
  server_url: https://gotify.example.com
  app_token: your_gotify_app_token_here
  priority: 5
```

### Configuration Details
- **scaling_thresholds**: Defines the CPU and RAM usage percentages that trigger scaling actions.
- **scaling_limits**: Specifies the minimum and maximum resources each VM can have.
- **proxmox_hosts**: Contains the details of Proxmox hosts to connect to.
- **virtual_machines**: List of VMs that are managed by the autoscale script.
- **logging**: Specifies the logging level and log file path.
- **gotify**: Configures notifications for Gotify.

## Gotify Notifications
Gotify is used for sending real-time notifications when scaling actions occur. You can configure Gotify in the `config.yaml` file:
- **enabled**: Set to `true` to enable notifications.
- **server_url**: URL of the Gotify server.
- **app_token**: Authentication token for Gotify.
- **priority**: Notification priority level (1-10).

## Development
### Requirements
- Python 3.x
- `paramiko`, `requests`, `PyYAML`

### Running Manually
To run the script manually for debugging purposes:

```bash
python3 /usr/local/bin/vm_autoscale/autoscale.py
```

### Contributing
Feel free to submit issues or pull requests on GitHub. Contributions are always welcome!

## License
This project is licensed under the MIT License. See the LICENSE file for more information.

## Support
If you encounter any issues, please submit them via [GitHub Issues](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues).
