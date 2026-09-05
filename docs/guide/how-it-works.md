---
title: How scaling decisions are made
description: The full decision path Proxmox VM Autoscale takes each cycle — gates, thresholds, step sizes, limits and per-resource cooldowns.
---

# How scaling decisions are made

Every decision this service makes comes from one pass of the logic below. There is no state carried between cycles other than the cooldown timers.

## The cycle

```
every check_interval seconds
│
└─ for each host in proxmox_hosts
   └─ for each VM in virtual_machines where proxmox_host == host.name
      │                                    and scaling_enabled == true
      │
      ├─ open SSH connection to the host
      │
      ├─ GATE 1  is the guest running?          qm status <id> --verbose
      │          └─ no  → skip this VM
      │
      ├─ GATE 2  does the host have headroom?   pvesh get /nodes/<n>/status
      │          └─ no  → skip this VM, log a warning
      │
      ├─ read CPU% and RAM% for the guest       pvesh get /cluster/resources
      │
      ├─ if cpu_scaling:  evaluate CPU thresholds → maybe scale
      ├─ if ram_scaling:  evaluate RAM thresholds → maybe scale
      │
      └─ close SSH connection
```

Both gates are checked **before** any usage is read, and both are hard stops for that VM in that cycle. A VM that is powered off costs one `qm status` call and nothing else.

## Gate 1 — is the guest running?

`qm status <vmid> --verbose` is parsed for the literal string `status: running`. Retried up to three times with an increasing delay, because a node under load occasionally times out. If all three attempts fail, the VM is treated as not running and skipped.

## Gate 2 — does the host have headroom?

Read from `pvesh get /nodes/$(hostname)/status --output-format json`:

- **CPU** — the node's `cpu` field, a fraction, multiplied by 100.
- **RAM** — `memory.used / memory.total × 100`.

::: info Why `used` and not `total - free`
`used` excludes reclaimable buff/cache and matches what the Proxmox web UI shows. An earlier version computed availability from `free + cached`, which made a node with a warm page cache look 90% full when it was really at 66%, silently suppressing all scaling. Fixed in [0.1.1](/reference/changelog).
:::

If either figure exceeds `host_limits.max_host_cpu_percent` or `max_host_ram_percent`, the whole VM is skipped — including scale **down**, which is arguably backwards, since shrinking a guest is exactly what you want on a saturated node. Worth knowing if your node sits near its ceiling.

## Reading guest usage

```bash
pvesh get /cluster/resources --output-format json
```

The service parses the JSON and picks the row where `type` is `qemu` and `vmid` equals the configured VMID exactly. From that row:

- **CPU** — the `cpu` field, a fraction of the guest's allocated CPUs, × 100.
- **RAM** — `mem ÷ maxmem × 100`, both byte counts.

::: info This used to be a table scrape
Earlier versions ran `pvesh get /cluster/resources | grep 'qemu/<vmid>' | awk -F '│' …`, which depended on box-drawing separators and fixed column positions — both of which move between Proxmox versions — and whose substring match also caught VMID `1010` when looking for `101`. Both problems are gone.
:::

::: warning An unreadable metric is skipped, not treated as zero
When a metric cannot be read the service reports it as **unavailable** and skips scaling that resource for the cycle:

```
[INFO]    VM 101 current usage - CPU: unavailable, RAM: 41.50%
[WARNING] VM 101: CPU usage unavailable; skipping CPU scaling.
```

Earlier versions substituted `0.0`, which sits below every sensible `low` threshold — so a failed read was indistinguishable from an idle guest and walked the VM down to its minimum, one step per cycle. That is the mechanism behind several historical "scaled down for no reason" reports.
:::

## The thresholds

CPU and RAM are evaluated independently, each against `scaling_thresholds`:

| Condition | Action |
|---|---|
| `usage > high` | Scale that resource **up** one step |
| `low ≤ usage ≤ high` | Nothing — this is the dead band |
| `usage < low` | Scale that resource **down** one step |

Comparisons are strict (`>` and `<`), so a value sitting exactly on a threshold does nothing.

::: tip Keep the dead band wide
The gap between `low` and `high` is your only defence against oscillation. With `low: 60` and `high: 70`, a guest hovering near 65% will bounce up and down forever, each flap costing a `qm set`. The shipped defaults (20/80 for CPU, 25/85 for RAM) are wide for good reason.
:::

CPU is always evaluated before RAM. If CPU scaling raises an exception the service logs it and still proceeds to RAM.

## Step sizes

Steps are fixed and not configurable:

| Resource | Step |
|---|---|
| CPU | 1 core / 1 vCPU |
| RAM | 512 MB |

One step per resource per cycle. Recovering from a large, sudden jump therefore takes several cycles: going from 2 to 8 cores needs six cycles, each separated by at least `scale_cooldown`.

## Limits

Steps are clamped by `scaling_limits`:

```yaml
scaling_limits:
  min_cores: 1
  max_cores: 8
  min_ram_mb: 1024
  max_ram_mb: 16384
```

At a boundary the corresponding direction becomes a no-op: `No CPU scaling required.` in the log, no notification, and — importantly — **no cooldown consumed**.

::: danger These limits were ignored before the fix
Until recently the code looked these values up under names that do not exist in `config.yaml`, so every installation ran on the hardcoded defaults 1–8 cores and 512–16384 MB regardless of what you wrote. If you are upgrading from an older version, your configured limits are about to start applying for the first time — re-read them before you upgrade. See the [changelog](/reference/changelog).
:::

## Cooldowns

`scale_cooldown` (default 300 s) is the minimum time between two scaling actions **on the same resource of the same VM**.

Three properties are worth stating precisely:

1. **CPU and RAM have separate timers.** A CPU change does not delay a RAM change. (They shared one timer before; that is what made RAM appear to stop scaling whenever CPU was active — [issue #30](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/30).)
2. **Only a real change starts the timer.** Crossing a threshold while already at a limit changes nothing and rate-limits nothing.
3. **Timers live in memory.** They survive between cycles, but a service restart clears them. After `systemctl restart`, the first cycle can scale immediately.

The effective interval between two changes to the same resource is therefore `max(check_interval, scale_cooldown)` rounded up to a cycle boundary. Setting `scale_cooldown` below `check_interval` has no effect.

## Applying the change

What actually reaches the hypervisor depends on the guest's hotplug state — the [hotplug and NUMA](/guide/hotplug) page covers this in full. Summarised:

| Guest state | CPU up | RAM change |
|---|---|---|
| Running, hotplug + NUMA on | `qm set -vcpus` (live), `-cores` if vCPUs are already at the core count | `qm set -balloon` (live) |
| Running, hotplug on, NUMA off | `-cores` + `-vcpus`, warning logged | `qm set -memory`, needs reboot |
| Running, no hotplug | `-cores` + `-vcpus`, warning logged | `qm set -memory`, needs reboot |
| Stopped | `-cores` + `-vcpus` | `qm set -memory` |

::: warning A logged success is not a confirmed change
The service logs `RAM balloon set to 4096 MB` after the command returns. It does not verify that the guest honoured it. A guest without a working balloon driver, or one whose kernel refuses a CPU hot-add, will report success in the log and change nothing. Check `qm config` and the guest itself when it matters.
:::

## After a change

- The action is logged at `INFO`.
- A notification goes out via every configured channel, priority 7 for scale-up and 5 for scale-down ([notifications](/guide/notifications)).
- If billing is enabled, the new spec is recorded with a timestamp ([billing](/guide/billing)).
- The cooldown timer for that resource starts.

Errors while processing a VM are caught per-VM: they are logged, sent as a priority-9 notification, and the loop moves on to the next VM. An unexpected error in the outer loop is logged at priority 10 and the service waits 60 seconds before retrying.
