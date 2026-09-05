---
title: Introduction
description: What Proxmox VM Autoscale does, what it deliberately does not do, and the assumptions it makes about your Proxmox environment.
---

# Introduction

Proxmox VM Autoscale is a long-running Python service that adjusts the CPU cores and RAM of Proxmox VE virtual machines based on how much of each they are actually using.

It runs as a systemd unit, connects to one or more Proxmox nodes over SSH, and drives the standard `qm` and `pvesh` command-line tools. There is no agent inside the guests and no Proxmox API token: everything happens through an SSH session as a user who can run `qm`.

## The loop, in one paragraph

Every `check_interval` seconds the service walks the VMs listed in `config.yaml`. For each one it confirms the guest is running, checks that the **host** still has headroom, reads the guest's current CPU and RAM usage, and compares those numbers against your thresholds. If usage is above the high mark it adds one core or 512 MB; if it is below the low mark it removes one. Limits and a per-resource cooldown bound how far and how fast that can go. Any actual change is logged and, if you configured it, pushed to Gotify or emailed.

That is the whole design. The rest of the documentation is about the details that decide whether it behaves well on your hardware.

## What it does

- **Vertical scaling of CPU cores and vCPUs** on a running or stopped guest.
- **Vertical scaling of RAM**, via the balloon device when hotplug and NUMA allow it, otherwise as a config change that needs a reboot.
- **Multi-host operation** — several Proxmox nodes in one config file, each with its own SSH credentials and port.
- **Host-level safety gating** so a busy node does not get pushed further.
- **Hotplug and NUMA auto-configuration**, optional, so guests become live-scalable without you touching each one by hand.
- **Gotify and SMTP notifications** on scaling actions and errors.
- **Spec-change recording** for usage-based billing, with a costed CSV report.

## What it does not do

Being explicit about this saves a lot of disappointment:

- **No horizontal scaling.** It never creates, clones, destroys or migrates a VM. It only resizes the ones you list.
- **No LXC containers.** Use the sibling project [proxmox-lxc-autoscale](https://github.com/fabriziosalmi/proxmox-lxc-autoscale) for those.
- **No disk, network or GPU scaling.** CPU cores, vCPUs and memory only.
- **No prediction or trend analysis.** Decisions come from a single instantaneous sample per cycle. There is no smoothing, no moving average and no seasonality — a one-off spike between two polls is invisible, and a spike caught by a poll is acted on immediately.
- **No dry-run mode.** If the service is running and a threshold is crossed, `qm set` is executed.
- **No high availability.** It is a single process. If it dies, systemd restarts it and the in-memory cooldown state starts over.

## Assumptions it makes

| Assumption | Why it matters |
|---|---|
| You can SSH to each Proxmox node as a user who may run `qm set` — in practice `root` | Every action is a shell command; there is no least-privilege mode today |
| Guests are QEMU/KVM VMs, not containers | Usage is read from `qemu/<vmid>` rows in `pvesh get /cluster/resources` |
| `pvesh` output format is stable on your Proxmox version | Usage parsing is text-based and version-sensitive — see [limitations](/reference/limitations#metric-parsing-is-format-sensitive) |
| Guests you want live-scaled have hotplug **and** NUMA on | Without both, memory changes need a reboot to apply |
| Load is bursty and uncorrelated across VMs | If everything peaks together, the host ceiling blocks scaling and you simply need more hardware |

## Requirements

- **Proxmox VE 6.0 or newer** on the target nodes.
- **Python 3.10 or newer** on the machine running the service. CI tests 3.10 through 3.12. (Python 3.9 was dropped once `requests` stopped supporting it; 3.9 itself reached end of life in October 2025.)
- **`paramiko`, `PyYAML`, `requests`** — see `requirements.txt`.
- **SSH reachability** from the service to every node in the config.

::: tip Where to run it
The service can run *on* a Proxmox node (simplest, and what the installer assumes) or on a separate Linux box that has SSH access to all of them. Running it off-node means a node reboot does not take the autoscaler down with it — but it also means the autoscaler's credentials live somewhere else that must be protected just as well.
:::

## Next steps

- [Install it](/guide/installation) — installer, or manual setup if you would rather not pipe a script into bash.
- [Get one VM scaling](/guide/quick-start) — the smallest useful configuration, and how to prove it works.
- [Understand the decision logic](/guide/how-it-works) — thresholds, step sizes, limits, cooldowns.
