# Proxmox VM Autoscale

[![CI](https://github.com/fabriziosalmi/proxmox-vm-autoscale/actions/workflows/ci.yml/badge.svg)](https://github.com/fabriziosalmi/proxmox-vm-autoscale/actions/workflows/ci.yml)
[![Docs](https://github.com/fabriziosalmi/proxmox-vm-autoscale/actions/workflows/docs.yml/badge.svg)](https://fabriziosalmi.github.io/proxmox-vm-autoscale/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)
[![FOSSA Status](https://app.fossa.com/api/projects/git%2Bgithub.com%2Ffabriziosalmi%2Fproxmox-vm-autoscale.svg?type=shield)](https://app.fossa.com/projects/git%2Bgithub.com%2Ffabriziosalmi%2Fproxmox-vm-autoscale?ref=badge_shield)

Automatically adjust the CPU cores and RAM of Proxmox VE virtual machines based on how much of each they are actually using.

A systemd service that connects to your Proxmox nodes over SSH and drives the standard `qm` and `pvesh` tools. No agent inside the guests, no API token.

📖 **[Documentation](https://fabriziosalmi.github.io/proxmox-vm-autoscale/)** — installation, configuration reference, troubleshooting, security model and known limitations.

## How it works

Every `check_interval` seconds, for each VM you list: confirm the guest is running, check that the **host** still has headroom, read the guest's CPU and RAM usage, and compare it against your thresholds. Above the high mark it adds one core or 512 MB; below the low mark it removes one. Limits and a per-resource cooldown bound how far and how fast.

With hotplug and NUMA enabled on a guest, vCPU and balloon memory changes apply live. Where they cannot, the service says so in the log rather than pretending.

→ [How scaling decisions are made](https://fabriziosalmi.github.io/proxmox-vm-autoscale/guide/how-it-works.html)

## What it does not do

- **No horizontal scaling** — it never creates, clones, destroys or migrates a VM
- **No LXC containers** — see [proxmox-lxc-autoscale](https://github.com/fabriziosalmi/proxmox-lxc-autoscale)
- **No disk, network or GPU scaling** — CPU cores, vCPUs and memory only
- **No prediction** — one instantaneous sample per cycle, no smoothing or trend analysis

## Requirements

- Proxmox VE 6.0 or newer on the target nodes
- Python 3.10 or newer (CI tests 3.10 – 3.12)
- SSH access to each node as a user who can run `qm set` — in practice `root`
- `paramiko`, `PyYAML`, `requests`

> [!IMPORTANT]
> For changes to apply to a **running** guest, enable both:
> - **NUMA** — VM → Hardware → Processors → Enable NUMA
> - **Hotplug** — VM → Options → Hotplug → CPU and Memory
>
> NUMA is a topology change and needs a guest reboot to take effect. The service can configure both for you with `auto_configure_hotplug: true`.

## Install

```bash
bash <(curl -s https://raw.githubusercontent.com/fabriziosalmi/proxmox-vm-autoscale/main/install.sh)
```

> [!CAUTION]
> This pipes a remote script into a root shell, unsigned and unverified. Read it first, or follow the [manual install](https://fabriziosalmi.github.io/proxmox-vm-autoscale/guide/installation.html#option-b-manual-install).

The service is enabled but **not started**, so you can configure it first:

```bash
sudo nano /usr/local/bin/vm_autoscale/config.yaml
sudo systemctl start vm_autoscale.service
journalctl -u vm_autoscale.service -f
```

## Minimal configuration

```yaml
scaling_thresholds:
  cpu: { high: 80, low: 20 }
  ram: { high: 85, low: 25 }

scaling_limits:
  min_cores: 1
  max_cores: 8
  min_ram_mb: 1024      # NUMA misbehaves below 1 GB
  max_ram_mb: 16384

check_interval: 300
scale_cooldown: 300

proxmox_hosts:
  - name: pve1
    host: 10.0.0.11
    ssh_user: root
    ssh_key: /root/.ssh/vm_autoscale_ed25519
    ssh_port: 22                           # required, no default

virtual_machines:
  - { vm_id: 101, proxmox_host: pve1, scaling_enabled: true, cpu_scaling: true, ram_scaling: true }

host_limits:
  max_host_cpu_percent: 90
  max_host_ram_percent: 90
```

Gotify and SMTP notifications, billing tracking and hotplug auto-configuration are optional. The [configuration reference](https://fabriziosalmi.github.io/proxmox-vm-autoscale/reference/configuration.html) covers every key and what the code actually does with it.

> [!WARNING]
> `config.yaml` holds your Proxmox SSH credentials in plain text. It must be root-owned and mode `600` — the installer does this. See the [hardening guide](https://fabriziosalmi.github.io/proxmox-vm-autoscale/security/hardening.html).

## Try it without letting it act

```yaml
dry_run: true
```

Everything is evaluated and nothing is changed: no `qm set` is issued, the log records what would have happened, and notifications carry a `[DRY RUN]` prefix.

```
[dry-run] VM 101: would run `qm set 101 -vcpus 3`
```

## Monitoring

An optional Prometheus endpoint, off by default and bound to localhost when on:

```yaml
metrics:
  enabled: true
  bind: 127.0.0.1
  port: 9808
```

Exports cycle count and duration, per-VM CPU/RAM and running state, scaling actions by resource and direction, failures, and per-node utilisation. A metric that could not be read is **absent** rather than zero.

## Documentation

| | |
|---|---|
| [Getting started](https://fabriziosalmi.github.io/proxmox-vm-autoscale/guide/) | Installation, first scaling VM, how decisions are made |
| [Configuration reference](https://fabriziosalmi.github.io/proxmox-vm-autoscale/reference/configuration.html) | Every `config.yaml` key |
| [Troubleshooting](https://fabriziosalmi.github.io/proxmox-vm-autoscale/guide/troubleshooting.html) | SSH failures, VMs that never scale, usage stuck at 0% |
| [Known limitations](https://fabriziosalmi.github.io/proxmox-vm-autoscale/reference/limitations.html) | What is broken or missing, and the workarounds |
| [Threat model](https://fabriziosalmi.github.io/proxmox-vm-autoscale/security/) | What running this as root exposes |

## Contributing

Bug reports, fixes and features are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). For anything beyond a bug fix, open an issue first.

Security issues go through [private disclosure](SECURITY.md), never a public issue.

Code contributions from **[Specimen67](https://github.com/Specimen67)** and **[brianread108](https://github.com/brianread108)**.

## Commercial support

Paid support, custom development and consulting — infrastructure automation, hardening, monitoring and detection: **fabrizio.salmi@gmail.com**

## Related

- [proxmox-lxc-autoscale](https://github.com/fabriziosalmi/proxmox-lxc-autoscale) — the same idea for LXC containers
- [proxmox-lxc-autoscale-ml](https://github.com/fabriziosalmi/proxmox-lxc-autoscale-ml) — LXC scaling driven by a model rather than thresholds

More projects at [github.com/fabriziosalmi](https://github.com/fabriziosalmi?tab=repositories).

## License

MIT — see [LICENSE](LICENSE).

> [!CAUTION]
> This software resizes live virtual machines using credentials for `root` on your hypervisors. The author assumes no responsibility for any damage or disruption arising from its use. Pilot it on VMs you can afford to disturb.
