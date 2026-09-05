---
title: Python modules
description: API reference for the Proxmox VM Autoscale Python modules — classes, public methods, arguments and return values.
---

# Python modules

Useful if you are extending the service, calling `BillingTracker` from your own script, or reading the source. Paths are relative to `/usr/local/bin/vm_autoscale/`.

## `autoscale.py`

### `class VMAutoscaler`

The orchestrator.

```python
VMAutoscaler(config_path: str, logging_config_path: Optional[str] = None)
```

| Method | Signature | Notes |
|---|---|---|
| `run` | `() -> None` | Blocking main loop. Exits on `KeyboardInterrupt` |
| `process_vm` | `(host: dict, vm: dict) -> None` | One VM, one cycle. Catches and notifies its own errors |
| `_load_config` | `(config_path: str) -> dict` | Static. Raises `FileNotFoundError` or `ConfigurationError` |
| `_setup_logging` | `(path: Optional[str]) -> Logger` | JSON config wins over the YAML `logging` section |
| `_get_vm_manager` | `(ssh_client, vm_id) -> VMResourceManager` | Cached per VMID; rebinds the SSH client |
| `_handle_cpu_scaling` | `(vm_manager, vm_id, cpu_usage: float) -> None` | |
| `_handle_ram_scaling` | `(vm_manager, vm_id, ram_usage: float) -> None` | |
| `_record_billing_spec` | `(vm_manager, vm_id) -> None` | No-op when billing is disabled |

### `class NotificationManager`

```python
NotificationManager(config: dict, logger: logging.Logger)
```

Validates channel configuration in the constructor.

| Method | Signature | Notes |
|---|---|---|
| `send_notification` | `(message, priority: Optional[int] = None) -> None` | Fans out to all enabled channels; never raises |
| `send_gotify_notification` | `(message: str, priority=None) -> None` | Raises on HTTP failure |
| `send_smtp_notification` | `(message: str) -> None` | Raises on SMTP failure |
| `validate_notification_config` | `() -> None` | Raises `ConfigurationError` |
| `_format_message` | `(message) -> str` | Joins tuples, stringifies everything else |

### `class ConfigurationError(Exception)`

Raised for missing sections and incomplete channel configuration.

## `vm_manager.py`

### `class VMResourceManager`

```python
VMResourceManager(ssh_client, vm_id, config: dict)
```

Runs hotplug auto-configuration in the constructor when `auto_configure_hotplug` is true.

**Public methods**

| Method | Signature | Returns |
|---|---|---|
| `is_vm_running` | `(retries=3, delay=5) -> bool` | `False` if undeterminable after retries |
| `get_resource_usage` | `() -> tuple[float, float]` | `(cpu_pct, ram_pct)`; `(0.0, 0.0)` on any failure |
| `can_scale` | `(resource: str = "cpu") -> bool` | Read-only cooldown check |
| `scale_cpu` | `(direction: "up" \| "down") -> bool` | `True` only if a change was made |
| `scale_ram` | `(direction: "up" \| "down") -> bool` | `True` only if a change was made |

**Internal helpers worth knowing**

| Method | Purpose |
|---|---|
| `_mark_scaled(resource)` | Starts the cooldown for that resource |
| `_scaling_limit(key, legacy_key, default)` | `scaling_limits` → flat key → default |
| `_get_min_cores` / `_get_max_cores` | Resolved limits |
| `_get_min_ram` / `_get_max_ram` | Resolved limits, MB |
| `_get_current_cores` / `_get_current_vcpus` / `_get_current_ram` | Parsed from `qm config` |
| `_check_hotplug_enabled()` | `(cpu_hotplug, memory_hotplug)` |
| `_check_numa_enabled()` | `bool` |
| `_set_cores` / `_set_vcpus` / `_set_ram` | Issue `qm set` |
| `_parse_cpu_usage` / `_parse_ram_usage` | Regex over `pvesh` table output |

Failing getters return conservative defaults — `1` core, `512` MB — rather than raising. A transient SSH failure during a config read therefore looks like a very small VM.

## `ssh_utils.py`

### `class SSHClient`

```python
SSHClient(host, user, password=None, key_path=None, port=22)
```

| Method | Signature | Notes |
|---|---|---|
| `connect` | `() -> None` | Reuses an active transport. 5 attempts, backoff 1/2/4/8/16 s. Auth failures raise immediately |
| `execute_command` | `(command: str, timeout=30) -> tuple[str, str, int]` | `(stdout, stderr, exit_status)`. Retries with reconnect; **does not raise on non-zero exit** |
| `close` | `() -> None` | Idempotent |
| `is_connected` | `() -> bool` | |
| `__enter__` / `__exit__` | | Context-manager support |

Notes: the missing-host-key policy is auto-add, and `key_path` is loaded as an **RSA** key specifically. Both are covered in the [threat model](/security/).

## `host_resource_checker.py`

### `class HostResourceChecker`

```python
HostResourceChecker(ssh_client)
```

| Method | Signature | Notes |
|---|---|---|
| `check_host_resources` | `(max_cpu_pct, max_ram_pct) -> bool` | `True` when both are within limits. Raises on JSON or field errors |

RAM is `memory.used / memory.total`, matching the Proxmox web UI.

## `billing_tracker.py`

### `class BillingTracker`

```python
BillingTracker(config: dict, logger: logging.Logger)
```

Creates `csv_output_dir` and loads `billing_data.json` on construction.

| Method | Signature | Called by the service? |
|---|---|---|
| `record_spec_change` | `(vm_id, cpu_cores, ram_mb, timestamp=None) -> None` | **Yes** |
| `record_vm_state_change` | `(vm_id, state: "started" \| "stopped", timestamp=None) -> None` | No |
| `set_vm_name` | `(vm_id, vm_name) -> None` | No |
| `calculate_billing_period` | `(vm_id, period_start, period_end) -> BillingReport` | No |
| `export_csv` | `(report, output_path=None) -> str` | No |
| `run_webhook` | `(report) -> None` | No |
| `generate_period_report` | `(vm_id) -> Optional[BillingReport]` | No |

Every write persists the entire state file. See [billing](/guide/billing).

### Dataclasses

```python
@dataclass
class SpecChangeRecord:
    timestamp: datetime
    cpu_cores: int
    ram_mb: int

@dataclass
class StateChangeRecord:
    timestamp: datetime
    state: str            # "started" | "stopped"

@dataclass
class BillingReport:
    vm_id: str
    vm_name: str
    period_start: datetime
    period_end: datetime
    min_cpu_cores: int
    max_cpu_cores: int
    avg_cpu_cores: float
    min_ram_mb: int
    max_ram_mb: int
    avg_ram_mb: float
    total_uptime_hours: float
    total_downtime_hours: float
    uptime_percentage: float
    spec_changes: list[dict]
    total_cost: float
```

All three expose `to_dict()`; `BillingReport.to_dict()` is what webhooks receive.

## Importing from your own code

```python
import sys
sys.path.insert(0, "/usr/local/bin/vm_autoscale")

from ssh_utils import SSHClient
from vm_manager import VMResourceManager

config = {
    "auto_configure_hotplug": False,
    "scale_cooldown": 0,
    "scaling_limits": {"min_cores": 1, "max_cores": 8,
                       "min_ram_mb": 1024, "max_ram_mb": 16384},
}

with SSHClient(host="10.0.0.11", user="root",
               key_path="/root/.ssh/vm_autoscale_rsa") as ssh:
    vm = VMResourceManager(ssh, 101, config)
    print(vm.is_vm_running(), vm.get_resource_usage())
```

There is no installable package and no stable API contract — these are scripts on a path. Pin to a commit if you build on them.
