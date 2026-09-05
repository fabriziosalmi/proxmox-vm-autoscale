---
title: FAQ
description: Frequently asked questions about Proxmox VM Autoscale — scope, safety, tuning, LXC support, clustering and production readiness.
---

# FAQ

## Scope

### Does it work with LXC containers?

No. It drives `qm`, which is QEMU/KVM only. Use [proxmox-lxc-autoscale](https://github.com/fabriziosalmi/proxmox-lxc-autoscale) for containers, or [proxmox-lxc-autoscale-ml](https://github.com/fabriziosalmi/proxmox-lxc-autoscale-ml) for the ML-driven variant.

### Can it create or destroy VMs?

No. It only resizes VMs you list explicitly. No cloning, no migration, no provisioning.

### Can it scale disk or network?

No. CPU cores, vCPUs and memory only.

### Does it need an agent inside the guest?

No. Everything is read from the hypervisor. For memory changes to apply live the guest does need the **virtio balloon driver**, but that is a driver, not an agent.

### Does it use the Proxmox API?

No. It SSHes in and runs `qm` and `pvesh` as shell commands. There is no API token and no `pvesh` REST client — `pvesh` here is the CLI.

## Setup

### Must it run on a Proxmox node?

No. Anywhere with SSH access to your nodes works, and running it off-node means a node reboot does not take the autoscaler with it. The installer assumes on-node because that is the common case.

### Can one instance manage several nodes?

Yes — list them all under `proxmox_hosts`. They are processed sequentially in one cycle.

### Does it work on a Proxmox cluster?

It works on the nodes of a cluster, treating each as an independent host. It has no cluster awareness: no quorum check, no migration, no cluster-wide capacity view.

### Can I use an Ed25519 SSH key?

Not currently. The key file is loaded specifically as an RSA key. Use an RSA key or password authentication — see [known limitations](/reference/limitations#only-rsa-ssh-keys-are-supported).

### Does it need root on the Proxmox host?

In practice yes. Every action is `qm` or `pvesh`, which require privileges a normal user does not have. There is no least-privilege mode; the [threat model](/security/) is honest about what that means.

## Behaviour

### How fast does it react?

At worst one `check_interval` (default 300 s) before a spike is even noticed, then one step per `scale_cooldown`. Going from 2 to 8 cores takes six steps. This is not a service for reacting to a traffic spike in seconds.

### Can I make the steps bigger?

Not through configuration. Step sizes are fixed at 1 core and 512 MB in `vm_manager.py`.

### Can I set different thresholds per VM?

No. `config.yaml` shows a `thresholds:` block inside each VM entry, but the code reads only the global `scaling_thresholds`. The per-VM block is inert. See [known limitations](/reference/limitations#per-vm-thresholds-are-ignored).

### What happens if the service is restarted mid-cooldown?

The cooldown is forgotten. Timers are in memory only, so the first cycle after a restart can scale immediately.

### Does it scale VMs that are powered off?

No — a stopped guest is skipped before anything else happens.

### What if a VM's usage cannot be read?

It is treated as `0.0%`, which is below any sane `low` threshold, so the VM is scaled **down**. This is a genuine hazard, not a design choice — watch for `CPU usage not found in output` in the log.

### Does it undo its changes when stopped?

No. Guests stay wherever the last scaling action left them, including any `hotplug` and `numa` flags the service set.

## Tuning

### Good starting thresholds?

The shipped 20/80 for CPU and 25/85 for RAM are sensible. The important thing is the width of the gap, not the exact numbers — a narrow band causes oscillation.

### Why does my idle VM never scale down?

An idle guest is rarely at 0% CPU: background daemons, cron, monitoring agents typically keep it at 3–10%. If your `low` is 20 and the guest idles at 22, it never crosses. Watch a few cycles of `VM 101 current usage` lines and set `low` below the observed floor.

### Why does everything scale down at once after a restart?

Cooldowns are cleared on restart, so every VM is immediately eligible. If several are below their `low` threshold they all step down in the first cycle. That is expected — but if it happens on a busy fleet, check for the `usage not found` warnings first.

### Should `scale_cooldown` be shorter than `check_interval`?

There is no point. Decisions only happen at poll time, so the effective interval is `max(check_interval, scale_cooldown)`.

## Safety

### Can it take a host down?

`host_limits` is designed to prevent that: no VM on a node above its ceiling gets scaled. The gate also blocks scale-*down*, though, so a saturated node cannot shrink its idle guests either.

### Can it break a running VM?

Realistically, the two risks are memory and CPU removal. Ballooning memory away from a guest that is genuinely using it can push it into swap or trigger the OOM killer — keep `min_ram_mb` at a value the workload actually survives. Removing a vCPU is unreliable on some guests, Windows especially.

### Is there a dry-run mode?

No. If the service is running and a threshold is crossed, the command is executed. The nearest approximation is `scaling_enabled: false` on every VM, which produces the monitoring log lines with no actions.

### Is it safe for production?

It is a small single-process service with 118 unit tests, run by its author and by others across 302 stars and 23 forks. It is also alpha-versioned software that holds root credentials, has no dry-run, and has known defects documented on the [limitations](/reference/limitations) page. Read that page and the [threat model](/security/), pilot it on VMs you can afford to disturb, and decide for yourself.

## Project

### What Python version?

3.10 or newer. CI tests 3.10 through 3.12.

Python 3.9 was dropped once `requests` stopped supporting it: 3.9 reached end of life in October 2025, and the only Proxmox release shipping it — VE 7 — has been end of life since July 2024.

### Which version should I run?

The latest release, or `main`. Note that the tags are not in chronological order — `v1.2.0` predates `v0.1.1` despite the numbering. `v0.1.1` is the current release.

### How do I report a bug or a vulnerability?

Bugs: [GitHub issues](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/new/choose). Vulnerabilities: **not** as a public issue — see [reporting a vulnerability](/security/disclosure).

### Is commercial support available?

Yes. Paid support, custom development and consulting around infrastructure automation, hardening and monitoring: **fabrizio.salmi@gmail.com**.
