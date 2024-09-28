# 🚀 Proxmox VM Autoscale

## 🌟 Overview
**Proxmox VM Autoscale** is a service that dynamically adjusts your Proxmox virtual machine (VM) resources (CPU cores, RAM) based on real-time metrics and user-defined thresholds. It ensures efficient use of available resources, dynamically scaling up or down to optimize performance and resource availability.

This service operates across multiple Proxmox hosts, connects via SSH, and can be easily installed and managed as a systemd service for seamless automation.

## ✨ Features
- 🔄 **Auto-Scaling of VM CPU and RAM** based on real-time resource usage.
- 🛠️ **Configuration-Driven** via an easy-to-edit YAML file.
- 🌐 **Multiple Host Management** using SSH (supports password and key-based authentication).
- 📲 **Gotify Notifications** for alerting whenever scaling actions are performed.
- ⚙️ **Systemd Integration** for easy setup, management, and monitoring as a Linux service.

## 📋 Prerequisites
- 🖥️ **Proxmox VE** installed on the target machines.
- 🐍 **Python 3.x** installed on the Proxmox host(s).
- 💻 Basic understanding of Proxmox `qm` commands and SSH is recommended.

## 🚀 Quick Start

To install **Proxmox VM Autoscale** easily, run the following `curl bash` command. This command will automatically clone the repository, execute the installation script, and set up the service for you.

```bash
bash <(curl -s https://raw.githubusercontent.com/fabriziosalmi/proxmox-vm-autoscale/main/install.sh)
```

🎯 **This script will:**
- Clone the repository into `/usr/local/bin/vm_autoscale`.
- Copy all necessary files to the installation directory.
- Install Python dependencies.
- Set up a **systemd unit file** to manage the autoscaling service.


> **💡 Note**: The service is enabled but not started automatically at the end of installation. Start it manually using the command below.

> [!IMPORTANT]
> VM hotplug feature in Proxmox requirements: to scale virtual machines on the fly please check the official [Proxmox documentation](https://pve.proxmox.com/wiki/Hotplug_(qemu_disk,nic,cpu,memory) to meet the needed requirements.

## ⚡ Usage

### ▶️ Start/Stop the Service
To **start** the autoscaling service:

```bash
sudo systemctl start vm_autoscale.service
```

To **stop** the service:

```bash
sudo systemctl stop vm_autoscale.service
```

### 🔍 Check the Status
To check the status of the service:

```bash
sudo systemctl status vm_autoscale.service
```

### 📜 Logs
Logs are saved to `/var/log/vm_autoscale.log`. You can monitor the logs in real time using:

```bash
tail -f /var/log/vm_autoscale.log
```

or using `journalctl`:

```bash
journalctl -u vm_autoscale.service -f
```

## ⚙️ Configuration

The configuration file (`config.yaml`) is located at `/usr/local/bin/vm_autoscale/config.yaml`. This file defines the scaling thresholds, resource limits, host details, and VM information.

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

check_interval: 60  # Check every 60 seconds

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

### ⚙️ Configuration Details
- **`scaling_thresholds`**: Defines the CPU and RAM usage percentages that will trigger scaling actions (e.g., when CPU > 80%, scale up).
- **`scaling_limits`**: Specifies the **minimum** and **maximum** resources (cores, RAM) each VM can have.
- **`proxmox_hosts`**: Contains the details of Proxmox hosts to connect to, including SSH credentials.
- **`virtual_machines`**: A list of VMs that will be managed by the autoscale script. Allows per-VM customization of scaling.
- **`logging`**: Specifies the logging level and log file path for tracking activity and debugging.
- **`gotify`**: Configures **Gotify notifications** to alert when scaling actions are performed.

## 📲 Gotify Notifications
Gotify is used for sending real-time notifications about scaling actions. You can configure Gotify in the `config.yaml` file:
- **`enabled`**: Set to `true` to enable notifications.
- **`server_url`**: The URL of the Gotify server.
- **`app_token`**: The authentication token for accessing Gotify.
- **`priority`**: Notification priority level (1-10).

## 👨‍💻 Development

### 🔧 Requirements
- **Python 3.x**
- Required Python Packages: `paramiko`, `requests`, `PyYAML`

### 🐛 Running Manually
To run the script manually for debugging or testing purposes:

```bash
python3 /usr/local/bin/vm_autoscale/autoscale.py
```

### 🤝 Contributing
Contributions are welcome! If you find a bug or have an idea for improvement, please submit an issue or a pull request on [GitHub](https://github.com/fabriziosalmi/proxmox-vm-autoscale).

## 📜 License
This project is licensed under the **MIT License**. See the LICENSE file for full details.

## 💬 Support
If you encounter any issues, please report them via [GitHub Issues](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues). Your feedback helps me make Proxmox VM Autoscale better for everyone! 🌟
