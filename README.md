# 🚀 Proxmox VM Autoscale
[![FOSSA Status](https://app.fossa.com/api/projects/git%2Bgithub.com%2Ffabriziosalmi%2Fproxmox-vm-autoscale.svg?type=shield)](https://app.fossa.com/projects/git%2Bgithub.com%2Ffabriziosalmi%2Fproxmox-vm-autoscale?ref=badge_shield)

## 🌟 Overview
**Proxmox VM Autoscale** is a dynamic scaling service that automatically adjusts virtual machine (VM) resources (CPU cores and RAM) on your Proxmox Virtual Environment (VE) based on real-time metrics and user-defined thresholds. This solution helps ensure efficient resource usage, optimizing performance and resource availability dynamically.

The service supports multiple Proxmox hosts via SSH connections and can be easily installed and managed as a **systemd** service for seamless automation.

## 📑 Table of Contents
- [Overview](#-overview)
- [Features](#-features)
- [Prerequisites](#-prerequisites)
- [Quick Start](#-quick-start)
- [Usage](#-usage)
- [Configuration](#️-configuration)
- [Gotify Notifications](#-gotify-notifications)
- [Development](#-development)
- [Troubleshooting](#-troubleshooting)
- [Contributing](#-contributing)
- [Architecture](ARCHITECTURE.md)
- [Security](SECURITY.md)
- [License](#-license)

> [!IMPORTANT]
> To enable scaling of VM resources, make sure NUMA and hotplug features are enabled:
> - **Enable NUMA**: VM > Hardware > Processors > Enable NUMA ☑️
> - **Enable CPU Hotplug**: VM > Options > Hotplug > CPU ☑️
> - **Enable Memory Hotplug**: VM > Options > Hotplug > Memory ☑️

## ✨ Features
- 🔄 **Auto-scaling of VM CPU and RAM** based on real-time resource metrics.
- 🛠️ **Configuration-driven** setup using an easy-to-edit YAML file.
- 🌐 **Multi-host support** via SSH (compatible with both password and key-based authentication).
- 📲 **Gotify Notifications** for alerting you whenever scaling actions are performed.
- ⚙️ **Systemd Integration** for effortless setup, management, and monitoring as a Linux service.
- 🔥 **Auto-Hotplug Configuration** - Automatically enables hotplug and NUMA on VMs for seamless live scaling.
- 💰 **Billing Support** - Track resource usage and generate billing reports for web hosting providers.

## 📋 Prerequisites
- 🖥️ **Proxmox VE** 6.0 or higher must be installed on the target hosts
- 🐍 **Python 3.6+** installed on the machine running the autoscale service
- 🔑 **SSH access** to Proxmox hosts (password or key-based authentication)
- 📦 **Python packages**: `paramiko`, `PyYAML`, `requests` (installed automatically)
- 💻 Basic familiarity with Proxmox `qm` commands and SSH configuration
- ⚙️ **NUMA and Hotplug** features enabled on target VMs (auto-configured by default, see below)

## 🤝 Contributing
Contributions are **more than** welcome! If you encounter a bug or have suggestions for improvement, please [open an issue](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/new/choose) or submit a pull request. See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

### Contributors
Code improvements by: **[Specimen67](https://github.com/Specimen67)**, **[brianread108](https://github.com/brianread108)**

### Want to scale LXC containers instead of VMs on Proxmox hosts?
To autoscale LXC containers on Proxmox hosts, check out [this related project](https://github.com/fabriziosalmi/proxmox-lxc-autoscale).

## 🚀 Quick Start

To install **Proxmox VM Autoscale**, execute the following `curl bash` command. This command will automatically clone the repository, execute the installation script, and set up the service for you:

```bash
bash <(curl -s https://raw.githubusercontent.com/fabriziosalmi/proxmox-vm-autoscale/main/install.sh)
```

🎯 **This installation script will:**
- Clone the repository into `/usr/local/bin/vm_autoscale`
- Copy all necessary files to the installation directory
- Install the required Python dependencies from `requirements.txt`
- Set up a **systemd unit file** to manage the autoscaling service

> [!NOTE]
> The service is enabled but not started automatically at the end of the installation. To start it manually, use the following command.

```bash
systemctl start vm_autoscale.service
```

> [!TIP]
> **Auto-Hotplug Configuration**: By default, the autoscaler will automatically enable hotplug and NUMA on managed VMs. If you prefer manual configuration, set `auto_configure_hotplug: false` in the config file.

> [!IMPORTANT]
> Make sure to review the official [Proxmox documentation](https://pve.proxmox.com/wiki/Hotplug_(qemu_disk,nic,cpu,memory)) for the hotplug feature requirements to enable scaling virtual machines on the fly.

## ⚡ Usage

### ▶️ Start/Stop the Service
To **start** the autoscaling service:

```bash
systemctl start vm_autoscale.service
```

To **stop** the service:

```bash
systemctl stop vm_autoscale.service
```

### 🔍 Check the Status
To view the service status:

```bash
systemctl status vm_autoscale.service
```

### 📜 Logs
Logs are saved to `/var/log/vm_autoscale.log`. You can monitor the logs in real-time using:

```bash
tail -f /var/log/vm_autoscale.log
```

Or by using `journalctl`:

```bash
journalctl -u vm_autoscale.service -f
```

## ⚙️ Configuration

The configuration file (`config.yaml`) is located at `/usr/local/bin/vm_autoscale/config.yaml`. This file contains settings for scaling thresholds, resource limits, Proxmox hosts, and VM information.

### Example Configuration
```yaml
# Thresholds that trigger scaling actions
scaling_thresholds:
  cpu:
    high: 80  # Scale up when CPU usage exceeds 80%
    low: 20   # Scale down when CPU usage falls below 20%
  ram:
    high: 85  # Scale up when RAM usage exceeds 85%
    low: 25   # Scale down when RAM usage falls below 25%

# Resource limits for VMs
scaling_limits:
  min_cores: 1        # Minimum CPU cores per VM
  max_cores: 8        # Maximum CPU cores per VM
  min_ram_mb: 1024    # Minimum RAM in MB (NUMA requires 1024+)
  max_ram_mb: 16384   # Maximum RAM in MB

check_interval: 300  # Check VM resources every 300 seconds (5 minutes)

# Proxmox host configuration
proxmox_hosts:
  - name: host1
    host: 192.168.1.10
    ssh_user: root
    ssh_password: your_password_here  # Or use ssh_key instead
    ssh_key: /path/to/ssh_key         # Optional: path to SSH private key
    ssh_port: 22                       # SSH port (default: 22)

# VMs to monitor and autoscale
virtual_machines:
  - vm_id: 101
    proxmox_host: host1
    scaling_enabled: true
    cpu_scaling: true  # Enable CPU autoscaling
    ram_scaling: true  # Enable RAM autoscaling

# Logging configuration
logging:
  level: INFO
  log_file: /var/log/vm_autoscale.log

# Host resource safety limits
host_limits:
  max_host_cpu_percent: 90  # Don't scale if host CPU > 90%
  max_host_ram_percent: 90  # Don't scale if host RAM > 90%

# Auto-configure hotplug and NUMA for live scaling
auto_configure_hotplug: true  # Set to false to disable auto-configuration

# Optional: Gotify notifications
gotify:
  enabled: false
  server_url: https://gotify.example.com
  app_token: your_gotify_app_token_here
  priority: 5

# Optional: Billing for web hosters
billing:
  enabled: false                       # Enable billing tracking
  billing_period_days: 30              # Billing period length
  cost_per_cpu_core_per_hour: 0.01     # Cost per CPU core per hour
  cost_per_gb_ram_per_hour: 0.005      # Cost per GB RAM per hour
  csv_output_dir: /var/log/vm_autoscale/billing/
  webhook_script: ""                   # Optional: custom billing script
  webhook_url: ""                      # Optional: POST to URL
```

### ⚙️ Configuration Details
- **`scaling_thresholds`**: Defines the CPU and RAM usage thresholds that trigger scaling actions (e.g., when CPU > 80%, scale up)
- **`scaling_limits`**: Specifies the **minimum** and **maximum** resources (CPU cores and RAM in MB) each VM can have
- **`check_interval`**: Time in seconds between resource checks (default: 300 seconds / 5 minutes)
- **`proxmox_hosts`**: Contains details of Proxmox hosts, including SSH credentials and connection settings
- **`virtual_machines`**: Lists the VMs to be managed by the autoscaling script, allowing per-VM scaling customization
- **`logging`**: Specifies the logging level and log file path for activity tracking and debugging
- **`auto_configure_hotplug`**: When enabled (default), automatically configures hotplug and NUMA on VMs for live scaling
- **`gotify`**: Configures **Gotify notifications** to send alerts when scaling actions are performed
- **`billing`**: Tracks resource changes for billing purposes (ideal for web hosters)
- **`alerts`**: Email notification settings (optional) for scaling events
- **`host_limits`**: Safety thresholds to prevent scaling when host resources are constrained

## 💰 Billing for Web Hosters

For bulk web hosters offering dynamic pricing, the billing feature tracks:
- **Resource changes**: CPU cores and RAM changes with timestamps
- **Uptime tracking**: VM start/stop events
- **Cost calculation**: Based on configured rates per CPU core and GB of RAM

When enabled, billing reports are generated as CSV files in the configured output directory. You can also configure a webhook script or URL to integrate with your billing system.

### Billing Report Contents
- VM ID and name
- Billing period dates
- Min/max/average CPU and RAM
- Uptime statistics and percentage
- List of all spec changes
- Calculated total cost

## 📲 Gotify Notifications
Gotify is used to send real-time notifications regarding scaling actions. Configure Gotify in the `config.yaml` file:
- **`enabled`**: Set to `true` to enable notifications
- **`server_url`**: URL of the Gotify server (e.g., `https://gotify.example.com`)
- **`app_token`**: Authentication token for accessing Gotify API
- **`priority`**: Notification priority level (1-10, where 10 is highest priority)

## 👨‍💻 Development

### 📐 Architecture
For a detailed understanding of the system architecture, components, and data flow, see [ARCHITECTURE.md](ARCHITECTURE.md).

### 🔧 Requirements
- **Python 3.6 or higher**
- Required Python Packages: `paramiko`, `requests`, `PyYAML`

### 📦 Installing Dependencies
To install dependencies manually:

```bash
pip3 install -r /usr/local/bin/vm_autoscale/requirements.txt
```

### 🐛 Running Manually
To run the script manually for debugging or testing:

```bash
python3 /usr/local/bin/vm_autoscale/autoscale.py
```

## 🔧 Troubleshooting

### Common Issues and Solutions

#### Service fails to start
- **Check logs**: `journalctl -u vm_autoscale.service -n 50`
- **Verify Python installation**: `python3 --version` (should be 3.6+)
- **Check configuration**: Ensure `/usr/local/bin/vm_autoscale/config.yaml` is valid YAML

#### SSH Connection Issues
- **Verify SSH credentials**: Test manual SSH connection to Proxmox host
- **Check SSH port**: Ensure `ssh_port` in config matches Proxmox host configuration
- **Key permissions**: SSH keys should have permissions 600 (`chmod 600 /path/to/key`)

#### VMs not scaling
- **Check VM is running**: `qm status <vm_id>` on Proxmox host
- **Verify hotplug is enabled**: Check VM Options > Hotplug settings in Proxmox UI
- **Check NUMA**: VM > Hardware > Processors > NUMA should be enabled
- **Review thresholds**: Ensure usage exceeds configured thresholds in `config.yaml`
- **Check cooldown period**: Default is 300 seconds between scaling actions

#### Gotify notifications not working
- **Verify server URL**: Ensure URL is accessible from the machine running the service
- **Check app token**: Verify token has correct permissions in Gotify
- **Review logs**: Check for notification errors in `/var/log/vm_autoscale.log`

#### High CPU/RAM but no scaling occurs
- **Check host limits**: Service won't scale if host resources exceed `max_host_cpu_percent` or `max_host_ram_percent`
- **Verify scaling limits**: VM may have reached `max_cores` or `max_ram_mb` limits
- **Check scaling_enabled**: Ensure `scaling_enabled: true` for the VM in config

### Getting Help
If you encounter issues not covered here:
1. Check the [GitHub Issues](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues) for similar problems
2. Review the full logs: `tail -f /var/log/vm_autoscale.log`
3. [Open a new issue](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/new/choose) with:
   - Your configuration (sanitized to remove credentials)
   - Relevant log excerpts
   - Proxmox version and VM configuration

### Others projects

If you like my projects, you may also be interested in these:

- [caddy-waf](https://github.com/fabriziosalmi/caddy-waf) Caddy WAF (Regex Rules, IP and DNS filtering, Rate Limiting, GeoIP, Tor, Anomaly Detection) 
- [patterns](https://github.com/fabriziosalmi/patterns) Automated OWASP CRS and Bad Bot Detection for Nginx, Apache, Traefik and HaProxy
- [blacklists](https://github.com/fabriziosalmi/blacklists) Hourly updated domains blacklist 🚫 
- [UglyFeed](https://github.com/fabriziosalmi/UglyFeed) Retrieve, aggregate, filter, evaluate, rewrite and serve RSS feeds using Large Language Models for fun, research and learning purposes 
- [proxmox-lxc-autoscale](https://github.com/fabriziosalmi/proxmox-lxc-autoscale) Automatically scale LXC containers resources on Proxmox hosts 
- [DevGPT](https://github.com/fabriziosalmi/DevGPT) Code together, right now! GPT powered code assistant to build projects in minutes
- [websites-monitor](https://github.com/fabriziosalmi/websites-monitor) Websites monitoring via GitHub Actions (expiration, security, performances, privacy, SEO)
- [caddy-mib](https://github.com/fabriziosalmi/caddy-mib) Track and ban client IPs generating repetitive errors on Caddy 
- [zonecontrol](https://github.com/fabriziosalmi/zonecontrol) Cloudflare Zones Settings Automation using GitHub Actions 
- [lws](https://github.com/fabriziosalmi/lws) linux (containers) web services
- [cf-box](https://github.com/fabriziosalmi/cf-box) cf-box is a set of Python tools to play with API and multiple Cloudflare accounts.
- [limits](https://github.com/fabriziosalmi/limits) Automated rate limits implementation for web servers 
- [dnscontrol-actions](https://github.com/fabriziosalmi/dnscontrol-actions) Automate DNS updates and rollbacks across multiple providers using DNSControl and GitHub Actions 
- [proxmox-lxc-autoscale-ml](https://github.com/fabriziosalmi/proxmox-lxc-autoscale-ml) Automatically scale the LXC containers resources on Proxmox hosts with AI
- [csv-anonymizer](https://github.com/fabriziosalmi/csv-anonymizer) CSV fuzzer/anonymizer
- [iamnotacoder](https://github.com/fabriziosalmi/iamnotacoder) AI code generation and improvement


### ⚠️ Disclaimer
> [!CAUTION]
> The author assumes no responsibility for any damage or issues that may arise from using this tool.

### 📜 License
This project is licensed under the **MIT License**. See the LICENSE file for complete details.


[![FOSSA Status](https://app.fossa.com/api/projects/git%2Bgithub.com%2Ffabriziosalmi%2Fproxmox-vm-autoscale.svg?type=large)](https://app.fossa.com/projects/git%2Bgithub.com%2Ffabriziosalmi%2Fproxmox-vm-autoscale?ref=badge_large)