---
title: Host safety limits
description: How host_limits prevents Proxmox VM Autoscale from pushing an already-saturated node further, and the edge cases in how the gate is applied.
---

# Host safety limits

Autoscaling that ignores the hypervisor is a way to overcommit a node until everything on it suffers. `host_limits` is the guard against that.

```yaml
host_limits:
  max_host_cpu_percent: 90
  max_host_ram_percent: 90
```

## How the check works

Before any VM on a node is evaluated, the service asks the node about itself:

```bash
pvesh get /nodes/$(hostname)/status --output-format json
```

and computes:

- **CPU** — the `cpu` field (a fraction of total capacity) × 100.
- **RAM** — `memory.used / memory.total × 100`.

If either exceeds its ceiling, that VM is skipped for this cycle:

```
[WARNING] Host CPU usage exceeds maximum allowed limit: 94.10% > 90%
[WARNING] Host pve1 resources maxed out. Skipping scaling.
```

The check runs **per VM**, not once per host, so a saturated node produces one warning pair per configured VM per cycle. Noisy, but it also means the reading is fresh for each decision.

## Two behaviours worth knowing

### The gate blocks scale-down too

The check sits before the usage read, so a node above its ceiling blocks *all* scaling on it — including shrinking an idle guest, which is exactly the action that would give the node back some headroom.

If your nodes routinely sit near the ceiling, consider raising `max_host_ram_percent`. Proxmox hosts with ZFS ARC or a large page cache legitimately run at high memory utilisation.

### `used` is not `total - free`

RAM is computed from the node's `used` field, which excludes reclaimable buff/cache and matches the Proxmox web UI. Before 0.1.1 the calculation used `free + cached` as available memory, so a node with a warm cache reported ~90% when the UI showed ~66% — and every scaling action on it was suppressed. If you are on an older version and nothing ever scales, that is very likely the cause.

## Choosing the ceilings

| Setting | Reasonable range | Notes |
|---|---|---|
| `max_host_cpu_percent` | 80–90 | Proxmox reports load across all cores; brief 100% spikes are normal, and the sample is instantaneous |
| `max_host_ram_percent` | 85–95 | With ZFS, ARC counts as used. Check `arc_summary` before deciding this is a real ceiling |

Both are required keys — there is no default. A missing `host_limits` section raises a `KeyError` on the first VM processed, surfacing as `Error processing VM ...` in the log.

## Multi-node behaviour

`host_limits` is global, not per host: the same two numbers apply to every node in `proxmox_hosts`. If one node is much smaller or much busier than the rest, you cannot express that today — run a second instance with its own config and unit.

(Per-VM *scaling* limits are supported; it is the per-node ceilings that are still global. See [configuration](/reference/configuration#virtual-machines).)

## What is not checked

- **Storage.** Disks are never scaled, so free space is not consulted. A full storage pool will not stop CPU or RAM scaling.
- **Ballooning pressure across guests.** Each VM is considered on its own; the service does not reason about total committed memory versus physical memory.
- **Node quorum or cluster state.** A node that has lost quorum still answers `pvesh get /nodes/.../status`, and scaling proceeds.
- **Other consumers.** Anything running on the node outside Proxmox's accounting is invisible.
