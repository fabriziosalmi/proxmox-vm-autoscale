---
title: Hotplug and NUMA
description: Why Proxmox VM Autoscale needs hotplug and NUMA to change a running VM, what applies live, what needs a reboot, and what auto_configure_hotplug changes on your guests.
---

# Hotplug and NUMA

Whether a scaling action takes effect immediately or waits for the next reboot is decided entirely by two guest settings. This page explains which, why, and what the service does about them.

## The two settings

```bash
qm set <vmid> -hotplug cpu,memory,network,disk,usb
qm set <vmid> -numa 1
```

Or in the web UI: **VM → Options → Hotplug** and **VM → Hardware → Processors → Enable NUMA**.

| Setting | Enables | Applies without reboot? |
|---|---|---|
| `hotplug: cpu` | Adding/removing **vCPUs** on a running guest | Yes, the hotplug flag itself does |
| `hotplug: memory` | Memory changes on a running guest | Yes |
| `numa: 1` | Memory hotplug to actually function | **No** — needs a guest reboot |

The trap is the last row. NUMA is part of the virtual machine topology, so turning it on does nothing until the guest is power-cycled. A VM with `hotplug: memory` but `numa: 0` looks configured and is not.

## What applies live

| Change | Live with hotplug + NUMA | Otherwise |
|---|---|---|
| **vCPU count** (`-vcpus`) | Yes | n/a |
| **Core count** (`-cores`) | **No — always needs a reboot** | Needs a reboot |
| **RAM** (`-balloon`) | Yes | n/a |
| **RAM** (`-memory`) | Needs a reboot | Needs a reboot |

`cores` is the number of CPUs the virtual motherboard has sockets for; `vcpus` is how many of them are plugged in right now. Only the second can change on a running guest, and it can never exceed the first.

## How the service uses this

### Scaling CPU up

```
if running and hotplug:cpu
    if vcpus < cores      → qm set -vcpus (vcpus + 1)          # live
    else                  → qm set -cores (cores + 1)          # needs reboot
                            qm set -vcpus (vcpus + 1)          # live, warned
else
    qm set -cores, qm set -vcpus                               # warning logged
```

So the first few scale-ups on a guest with headroom (say 4 cores, 2 vCPUs) are genuinely live. Once vCPUs catch up with cores, each further step raises the core count too — and that part only materialises after a reboot. The log says so explicitly:

```
[WARNING] VM 101: Increased cores to 5 (requires reboot for full effect)
          and vCPUs to 5 (hotplug applied).
```

::: tip Give guests headroom in `cores`
Provision a guest with more `cores` than it needs and fewer `vcpus`, e.g. `cores: 8, vcpus: 2`. Every scale-up inside that range is fully live, and you never hit the reboot boundary.
:::

### Scaling CPU down

vCPUs are reduced first, which takes effect immediately. The core count is then also reduced, but only when it would stay at or above both the new vCPU count and `min_cores`. That part waits for a reboot.

::: warning CPU hot-unplug depends on the guest OS
Removing a vCPU is far less reliable than adding one. Linux generally copes; some workloads pinned to a CPU do not; Windows guests frequently refuse. QEMU accepts the command either way and the service logs success. Verify with `nproc` inside a guest you care about.
:::

### Scaling RAM

With hotplug **and** NUMA on a running guest, the service sets the **balloon** value:

```bash
qm set <vmid> -balloon 4096
```

The balloon driver inside the guest inflates or deflates to reach the target, so memory changes without a reboot. In every other case the service falls back to `-memory`, which only applies on next boot, and logs a warning saying so.

::: warning The balloon driver has to be present and working
Ballooning needs `virtio_balloon` in the guest. Without it — most bare Windows installs before the VirtIO drivers are installed, some minimal container-style images — the command succeeds at the QEMU level and nothing happens inside the guest. Reclaiming memory from a guest that is genuinely using it can also drive it into swap or trigger the OOM killer.
:::

### Why `min_ram_mb` should stay at 1024

NUMA-enabled guests behave badly with very little memory; the shipped config sets `min_ram_mb: 1024` specifically for that. The step size is 512 MB, so a floor of 1024 also keeps the scale-down path from landing on awkward values.

## `auto_configure_hotplug`

```yaml
auto_configure_hotplug: true    # default
```

When enabled, the first time the service handles a VM it inspects `qm config` and, if hotplug or NUMA is missing, issues:

```bash
qm set <vmid> -hotplug cpu,memory,network,disk,usb -numa 1
```

Then it logs:

```
[INFO] VM 101: Enabling hotplug for cpu,memory,network,disk,usb
[INFO] VM 101: Enabling NUMA for memory hotplug support
[INFO] VM 101: Hotplug configuration updated.
       Note: NUMA changes require a VM restart to take effect.
```

### What to know before leaving it on

- **It modifies your VM configuration without asking.** On a fleet you did not build yourself, that may not be welcome.
- **It enables hotplug for `network,disk,usb` too**, not just CPU and memory. That is broader than the autoscaler needs.
- **NUMA will not work until you reboot the guest.** Until then memory scaling silently falls back to `-memory` + reboot. The service tells you once, in the log, at the moment it makes the change.
- **It runs once per VM per service lifetime.** After the fix that caches VM managers across cycles, this is a single check at startup rather than two extra `qm config` calls per VM on every poll.
- **Failures are non-fatal.** If `qm set` fails the service logs a warning and carries on with whatever the guest currently supports.

Set it to `false` if you would rather configure guests deliberately:

```yaml
auto_configure_hotplug: false
```

## Verifying a guest is genuinely live-scalable

```bash
# On the node
qm config 101 | grep -E 'hotplug|numa|cores|vcpus|memory|balloon'
```

You want to see `hotplug:` containing both `cpu` and `memory`, and `numa: 1`.

```bash
# Inside the guest — has it been rebooted since numa was enabled?
lscpu | grep -i numa
dmesg | grep -i balloon
```

If `numa: 1` is in the VM config but `lscpu` shows no NUMA node, the guest has not been rebooted since the change and memory hotplug is not actually available yet.
