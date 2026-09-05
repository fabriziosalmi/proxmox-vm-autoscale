# Architecture

> **Full version:** [Architecture on the documentation site](https://fabriziosalmi.github.io/proxmox-vm-autoscale/reference/architecture.html)
> — control flow, threading model, state, error handling and extension points.
> This file is the in-repository summary; the site is kept in sync with the code.

Five Python modules, roughly 1,500 lines. No framework, no database, no network
listener. The whole service is one blocking loop over SSH.

## Components

```
┌──────────────────────────────────────────────────────────────┐
│ autoscale.py                                                 │
│                                                              │
│  VMAutoscaler ......... config, main loop, decision routing  │
│  NotificationManager .. Gotify + SMTP fan-out                │
│  ConfigurationError ... raised on invalid startup config     │
└───────┬──────────────────────┬───────────────────┬───────────┘
        │                      │                   │
        ▼                      ▼                   ▼
┌───────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ vm_manager.py │   │ host_resource_   │   │ billing_tracker  │
│               │   │ checker.py       │   │ .py              │
│ VMResource-   │   │                  │   │                  │
│ Manager       │   │ HostResource-    │   │ BillingTracker   │
│               │   │ Checker          │   │ + dataclasses    │
│ per-VM state, │   │                  │   │                  │
│ limits,       │   │ node CPU/RAM     │   │ JSON state, CSV  │
│ cooldowns,    │   │ gate             │   │ reports          │
│ qm commands   │   │                  │   │                  │
└───────┬───────┘   └────────┬─────────┘   └──────────────────┘
        │                    │
        └────────┬───────────┘
                 ▼
        ┌──────────────────┐
        │ ssh_utils.py     │
        │                  │
        │ SSHClient        │
        │ connect / retry  │
        │ exec / close     │
        └────────┬─────────┘
                 │  SSH
                 ▼
        ┌──────────────────┐
        │ Proxmox VE node  │
        │ qm  ·  pvesh     │
        └──────────────────┘
```

| Module | Responsibility |
|---|---|
| `autoscale.py` | Configuration, the polling loop, routing decisions, notifications |
| `vm_manager.py` | The only module that mutates anything: limits, cooldowns, `qm set` |
| `host_resource_checker.py` | The host headroom gate, from `pvesh get /nodes/<n>/status` |
| `ssh_utils.py` | Paramiko wrapper: connect with backoff, execute, close |
| `billing_tracker.py` | Spec-change recording and costed period reports |

## The cycle

```
every check_interval seconds
└─ for each host → for each enabled VM on it
   ├─ open SSH connection
   ├─ GATE 1  is the guest running?        → no: skip
   ├─ GATE 2  does the host have headroom? → no: skip
   ├─ read CPU% and RAM% for the guest
   ├─ evaluate CPU thresholds → maybe scale
   ├─ evaluate RAM thresholds → maybe scale
   └─ close SSH connection
```

Both gates run before any usage is read, and both are hard stops for that VM in
that cycle.

## State

`VMResourceManager` instances are cached per VM for the lifetime of the process
and rebound to each cycle's SSH connection. That is what makes `scale_cooldown`
meaningful across cycles, and it keeps hotplug auto-configuration to one check
per VM. Cooldown timers are per resource and live in memory, so a restart clears
them. The only durable state the service writes is the billing JSON file.

## Tests

138 unit tests in `tests/`, run with `pytest`. SSH is mocked throughout, so
anything depending on real `pvesh` output format is not covered by CI.

```bash
python3 -m pytest tests/ -q
```
