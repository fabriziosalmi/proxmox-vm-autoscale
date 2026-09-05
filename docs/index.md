---
layout: home
title: Proxmox VM Autoscale
titleTemplate: Right-size Proxmox VMs automatically
description: Threshold-based CPU and RAM autoscaling for Proxmox VE virtual machines — a systemd service that drives qm over SSH, with hotplug support, host safety limits and Gotify/SMTP notifications.

hero:
  name: Proxmox VM Autoscale
  text: Right-size your VMs while they run
  tagline: A small systemd service that watches CPU and RAM on your Proxmox VE guests and adjusts cores and memory through qm over SSH — with hotplug, host safety limits and notifications.
  image:
    src: /logo-mark.svg
    alt: Proxmox VM Autoscale
  actions:
    - theme: brand
      text: Get started
      link: /guide/
    - theme: alt
      text: Installation
      link: /guide/installation
    - theme: alt
      text: View on GitHub
      link: https://github.com/fabriziosalmi/proxmox-vm-autoscale

features:
  - icon: 📈
    title: Threshold-based scaling
    details: Scale cores and RAM up past a high-water mark and back down below a low one. One step per decision, bounded by limits you set, rate-limited by a per-resource cooldown.
    link: /guide/how-it-works
    linkText: How decisions are made
  - icon: ⚡
    title: Live changes where possible
    details: With hotplug and NUMA enabled, vCPU and balloon memory changes apply to a running guest. Where they cannot, the service says so in the log instead of pretending.
    link: /guide/hotplug
    linkText: Hotplug and NUMA
  - icon: 🛡️
    title: Host safety first
    details: Every scaling decision is gated on the host itself having headroom. If the node is above your CPU or RAM ceiling, nothing scales up on it.
    link: /guide/host-limits
    linkText: Host safety limits
  - icon: 🔔
    title: Notifications that mean something
    details: Gotify push and SMTP email on real scaling actions and on errors — not on every poll. Both optional, both independent.
    link: /guide/notifications
    linkText: Configure notifications
  - icon: 🧾
    title: Usage tracking for hosters
    details: Records every spec change with a timestamp and turns a period into a costed CSV report. Useful if you bill customers for what they actually consumed.
    link: /guide/billing
    linkText: Billing tracking
  - icon: 🔐
    title: Root SSH, treated seriously
    details: The service holds credentials for root on your hypervisors. The security section documents exactly what that exposes and how to narrow it.
    link: /security/
    linkText: Threat model
---

## Is this for you?

This service suits a homelab or a small fleet where a handful of VMs have **bursty, uncorrelated load** and you would rather not over-provision all of them for the peak. It reads usage from the hypervisor, compares it against thresholds you set, and moves one step at a time.

It is deliberately not a cluster scheduler. It does not migrate guests, does not create or destroy VMs, and does not model future load — see [what it does not do](/guide/#what-it-does-not-do) before you build anything on top of it.

```bash
# Install on a Proxmox node (or any Linux host with SSH access to one)
bash <(curl -s https://raw.githubusercontent.com/fabriziosalmi/proxmox-vm-autoscale/main/install.sh)

# Point it at your hosts and VMs, then start it
sudo nano /usr/local/bin/vm_autoscale/config.yaml
sudo systemctl start vm_autoscale.service
journalctl -u vm_autoscale.service -f
```

::: warning Read this before running it in production
The service authenticates to your Proxmox hosts as **root** and issues `qm set` against live guests. Read the [threat model](/security/) and the [known limitations](/reference/limitations) first — and start with `dry_run: true`, which evaluates everything and changes nothing.
:::
