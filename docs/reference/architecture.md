---
title: Architecture
description: How Proxmox VM Autoscale is put together — components, control flow, threading model, state, and the extension points that exist.
---

# Architecture

Five Python modules, roughly 1,500 lines, no framework, no database, no network listener. The whole service is one blocking loop over SSH.

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
│               │   │ Checker          │   │ SpecChangeRecord │
│ per-VM state, │   │                  │   │ StateChangeRec.  │
│ limits,       │   │ node CPU/RAM     │   │ BillingReport    │
│ cooldowns,    │   │ gate             │   │                  │
│ qm commands   │   │                  │   │ JSON state, CSV  │
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

## Control flow

```
main()
└─ VMAutoscaler(config_path, logging_config_path)
   ├─ _load_config()          validate the four required sections
   ├─ _setup_logging()        logging_config.json wins over config.yaml
   ├─ NotificationManager()   validate channels, fail fast
   └─ BillingTracker()        only when billing.enabled

VMAutoscaler.run()
└─ while True:
   └─ for host in proxmox_hosts:
      └─ for vm in virtual_machines where vm.proxmox_host == host.name
                                     and vm.scaling_enabled:
         └─ process_vm(host, vm)
            ├─ SSHClient(...).connect()
            ├─ _get_vm_manager(ssh, vm_id)    cached per VM
            ├─ vm_manager.is_vm_running()          → gate 1
            ├─ HostResourceChecker(...).check()    → gate 2
            ├─ vm_manager.get_resource_usage()
            ├─ _handle_cpu_scaling()   if cpu_scaling
            ├─ _handle_ram_scaling()   if ram_scaling
            └─ ssh_client.close()      always, in finally
      sleep(check_interval)
```

## Per-module notes

### `autoscale.py`

`VMAutoscaler` owns configuration, the loop and the routing of decisions; it does not talk to Proxmox itself. `_handle_cpu_scaling` and `_handle_ram_scaling` compare a usage figure against thresholds and call into the VM manager, then notify and record billing when the manager reports an actual change.

`_get_vm_manager` caches one `VMResourceManager` per VMID for the lifetime of the process and rebinds it to each cycle's SSH client. That cache is what makes `scale_cooldown` meaningful across cycles and what keeps hotplug auto-configuration to a single check per VM.

`NotificationManager` validates its configuration in the constructor, so a broken notification setup stops the service at startup rather than at the first interesting event.

### `vm_manager.py`

`VMResourceManager` is the only module that mutates anything. It holds:

- `scale_cooldown` and one timestamp per resource (`cpu`, `ram`)
- a `threading.Lock` guarding those timestamps
- the config, for limit lookups

`can_scale(resource)` is a pure read; `_mark_scaled(resource)` starts a timer and is called only after a command has been issued. Limits resolve through `_scaling_limit`, which prefers the `scaling_limits` section and falls back to legacy flat keys.

### `ssh_utils.py`

`SSHClient` wraps Paramiko. Connection: up to five attempts with exponential backoff, except authentication failures which raise immediately. Command execution: up to five attempts, closing and reconnecting between them, returning `(stdout, stderr, exit_status)`.

It is also a context manager, though `process_vm` uses explicit `connect()` / `close()` in a `try`/`finally` instead.

### `host_resource_checker.py`

One method. Runs `pvesh get /nodes/$(hostname)/status --output-format json`, parses it, compares against the two ceilings, returns a boolean. Raises on JSON errors and missing fields — which surfaces as a per-VM `Error processing VM ...` rather than stopping the service.

### `metrics.py`

A small Prometheus registry and an HTTP endpoint served from a daemon thread,
using only `http.server` — a client library is not worth a new dependency for
one endpoint on a service whose install story is three apt packages. Disabled
by default and localhost-bound when enabled. A metric that could not be read
has its series **removed** rather than set to zero.

### `billing_tracker.py`

Three dataclasses and a tracker. State is a single JSON file rewritten in full on every record. The main loop asks it once per cycle whether a billing period has elapsed and, when one has, generates a costed CSV per VM and fires any configured webhook.

## Threading and concurrency

Effectively single-threaded, apart from the optional metrics endpoint, which
serves from its own daemon thread against a lock-guarded registry. `threading.Lock` in `VMResourceManager` guards the cooldown timestamps, but nothing spawns a thread: hosts and VMs are processed sequentially, and each SSH command blocks.

Consequences:

- A slow or unreachable node stalls every VM behind it in the cycle. Five connection attempts with backoff is ~31 seconds; command retries add more.
- Total cycle time grows linearly with the number of VMs.
- `check_interval` is a floor on the gap between cycles, not a period.

## State

| State | Lives in | Survives a restart? |
|---|---|---|
| Configuration | `config.yaml`, read once | n/a |
| Cooldown timestamps | Process memory | **No** |
| Cached VM managers | Process memory | No |
| SSH connections | Per VM, per cycle | No |
| Billing spec changes | `billing_data.json` | Yes |
| Logs | `/var/log/vm_autoscale.log` + journal | Yes |

The only durable state the service writes is the billing file.

## Error handling

Three layers:

1. **Per operation** — SSH connect and exec retry with backoff. Metric parse failures return `0.0` rather than raising, which is the source of the [silent scale-down](/reference/limitations#metric-parsing-is-format-sensitive) hazard.
2. **Per VM** — `process_vm` wraps everything in `try`/`except`. A failure is logged, notified at priority 9, and the loop moves to the next VM. CPU and RAM scaling are additionally wrapped separately, so a CPU failure does not prevent the RAM evaluation.
3. **Per cycle** — the main loop catches anything unexpected, notifies at priority 10, sleeps 60 seconds and continues. `KeyboardInterrupt` exits cleanly.

The process only exits on a startup failure (`sys.exit(1)`) or `SIGINT`.

## Extension points

Realistic places to add behaviour without restructuring:

| Goal | Where |
|---|---|
| A new notification channel | Add a `send_*` method to `NotificationManager` and a branch in `send_notification` |
| Different scaling arithmetic | `VMResourceManager.scale_cpu` / `scale_ram` |
| More metrics | `get_resource_usage` and the `_parse_*` helpers |
| Per-VM thresholds | `_handle_cpu_scaling` / `_handle_ram_scaling` already receive the VM dict's siblings; the plumbing to pass it is small |
| Scheduled billing reports | Call `BillingTracker.generate_period_report` from the main loop on a period boundary |

## Tests

`tests/` holds 201 unit tests across nine files, run with `pytest`. SSH is mocked throughout, so there is no integration test against a real Proxmox node — but metrics now come from `pvesh --output-format json`, and the tests exercise that parsing against realistic payloads rather than a scraped table.

```bash
python3 -m pytest tests/ -q
```

CI runs the suite on Python 3.10–3.12 and `shellcheck -S warning` on `install.sh`.
