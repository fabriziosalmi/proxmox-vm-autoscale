---
title: Configuration reference
description: Every key in the Proxmox VM Autoscale config.yaml — type, default, whether it is required, and what the code actually does with it.
---

# Configuration reference

Location: `/usr/local/bin/vm_autoscale/config.yaml`. The path is hardcoded in `autoscale.py`'s `main()`.

The file is read once at startup. There is no reload signal — restart the service after editing.

::: danger This file holds credentials
`ssh_password`, `smtp_password` and `app_token` are stored in plain text. The file must be root-owned and mode `600`. Verify with `ls -l`, and see the [hardening guide](/security/hardening).
:::

## Required sections

Startup fails unless all four of these are present:

```
scaling_thresholds
scaling_limits
proxmox_hosts
virtual_machines
```

Missing any of them:

```
CRITICAL Failed to start VM Autoscaler:
         Missing required configuration sections: scaling_limits
```

Note the validator only checks that the **keys exist** — not that their contents make sense.

## `scaling_thresholds`

Usage percentages that trigger a scaling decision. Global; not overridable per VM.

```yaml
scaling_thresholds:
  cpu:
    high: 80
    low: 20
  ram:
    high: 85
    low: 25
```

| Key | Type | Required | Notes |
|---|---|---|---|
| `cpu.high` | number | when `cpu_scaling` is used | Scale up above this |
| `cpu.low` | number | when `cpu_scaling` is used | Scale down below this |
| `ram.high` | number | when `ram_scaling` is used | Scale up above this |
| `ram.low` | number | when `ram_scaling` is used | Scale down below this |

Comparisons are strict, so a value exactly on a threshold does nothing. Keep the gap between `low` and `high` wide — it is your only protection against oscillation.

## `scaling_limits`

Hard bounds on what any VM may be scaled to. Global.

```yaml
scaling_limits:
  min_cores: 1
  max_cores: 8
  min_ram_mb: 1024
  max_ram_mb: 16384
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `min_cores` | int | `1` | |
| `max_cores` | int | `8` | |
| `min_ram_mb` | int | `512` | Keep at `1024` or above — NUMA misbehaves below 1 GB |
| `max_ram_mb` | int | `16384` | |

Limits apply to **every** VM. Per-VM limits are not supported; run separate instances if you need them.

::: warning Behaviour change
Older versions read these values under names that do not exist in this file, so the defaults above were enforced regardless of what you configured. If you are upgrading, your limits are about to take effect for the first time. Flat top-level `min_cores` / `max_cores` / `min_ram` / `max_ram` keys are still accepted as a fallback for legacy configs.
:::

## Timing

```yaml
check_interval: 300
scale_cooldown: 300
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `check_interval` | int (s) | `300` | Time between polling cycles |
| `scale_cooldown` | int (s) | `300` | Minimum time between two changes to the same resource on the same VM. **Not present in the shipped config.yaml** — add it if you want a value other than the default |

CPU and RAM have independent cooldown timers. Only an actual change starts one. Timers are in memory and are lost on restart. Setting `scale_cooldown` below `check_interval` has no effect.

## `proxmox_hosts`

```yaml
proxmox_hosts:
  - name: pve1
    host: 192.168.1.10
    ssh_user: root
    ssh_password: your_password_here
    ssh_key: /root/.ssh/id_rsa
    ssh_port: 22
```

| Key | Type | Required | Notes |
|---|---|---|---|
| `name` | string | yes | Referenced by `virtual_machines[].proxmox_host`; must match exactly |
| `host` | string | yes | Hostname or IP |
| `ssh_user` | string | yes | Needs privileges to run `qm set` — in practice `root` |
| `ssh_password` | string | one of the two | **Takes precedence over `ssh_key` when both are set** |
| `ssh_key` | path | one of the two | Ed25519, ECDSA, RSA and DSS all supported. The key must be **unencrypted** — there is no passphrase option |
| `ssh_port` | int | **yes** | There is no default in practice: `host['ssh_port']` is indexed directly rather than via `.get()`, so omitting the key raises `KeyError` and every VM on that host fails. Set it to `22` explicitly |

::: warning Two footguns in the shipped example
The example config fills in **both** `ssh_password` and `ssh_key` with placeholders. Password wins, so a half-edited config authenticates with the literal string `your_password_here`. Also, `host2` in the example omits `ssh_port` — and the code reads that key unconditionally, so the host raises a `KeyError`. Set `ssh_port` on every host.
:::

Connections are retried five times with exponential backoff (1, 2, 4, 8, 16 s). Authentication failures are **not** retried.

## `virtual_machines`

```yaml
virtual_machines:
  - vm_id: 101
    proxmox_host: pve1
    scaling_enabled: true
    cpu_scaling: true
    ram_scaling: true
```

| Key | Type | Required | Notes |
|---|---|---|---|
| `vm_id` | int or string | yes | Proxmox VMID. Both types work; strings are used as-is in shell commands |
| `proxmox_host` | string | yes | Must match a `proxmox_hosts[].name`. **A mismatch silently skips the VM** |
| `scaling_enabled` | bool | no | Defaults to `false` — omitting it means the VM is never processed |
| `cpu_scaling` | bool | no | Defaults to `false` |
| `ram_scaling` | bool | no | Defaults to `false` |
| `thresholds` | map | — | **Ignored.** Present in the example config but never read |

## `host_limits`

```yaml
host_limits:
  max_host_cpu_percent: 90
  max_host_ram_percent: 90
```

| Key | Type | Required | Notes |
|---|---|---|---|
| `max_host_cpu_percent` | number | yes | No default; a missing key raises `KeyError` per VM |
| `max_host_ram_percent` | number | yes | Same |

Both gates block scale-**down** as well as scale-up. See [host safety limits](/guide/host-limits).

## `auto_configure_hotplug`

```yaml
auto_configure_hotplug: true
```

| Type | Default | Notes |
|---|---|---|
| bool | `true` | When on, the service issues `qm set -hotplug cpu,memory,network,disk,usb -numa 1` on guests missing them |

Runs once per VM per service lifetime. NUMA changes need a guest reboot. See [hotplug and NUMA](/guide/hotplug#auto-configure-hotplug).

## `logging`

```yaml
logging:
  level: INFO
  log_file: /var/log/vm_autoscale.log
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `level` | string | `INFO` | |
| `log_file` | path | `/var/log/vm_autoscale.log` | |

::: info `logging_config.json` overrides this
If `/usr/local/bin/vm_autoscale/logging_config.json` exists it is loaded via `dictConfig` and **this whole section is ignored**. Since the installer ships that file, the `logging` block in `config.yaml` is inert in a default installation. Edit the JSON instead — see [operations](/guide/operations#log-levels).
:::

## `gotify`

```yaml
gotify:
  enabled: false
  server_url: https://gotify.example.com
  app_token: your_gotify_app_token_here
  priority: 5
```

| Key | Type | Required | Notes |
|---|---|---|---|
| `enabled` | bool | yes | |
| `server_url` | url | when enabled | Trailing slash stripped automatically |
| `app_token` | string | when enabled | Application token |
| `priority` | int | no | Default `5`; per-event priorities override it |

Validated at startup. See [notifications](/guide/notifications#gotify).

## `alerts` (SMTP)

```yaml
alerts:
  email_enabled: false
  email_recipient: admin@example.com
  smtp_server: smtp.example.com
  smtp_port: 587
  smtp_user: your_smtp_user
  smtp_password: your_smtp_password
```

| Key | Type | Required | Notes |
|---|---|---|---|
| `email_enabled` | bool | yes | |
| `smtp_server` | string | when enabled | |
| `smtp_user` | string | when enabled | Also the `From` address |
| `email_recipient` | string or list | when enabled | |
| `smtp_port` | int | no | Default `587` |
| `smtp_password` | string | no | Empty string skips `login()` |

`starttls()` is always called. See [notifications](/guide/notifications#email-smtp).

## `billing`

```yaml
billing:
  enabled: false
  billing_period_days: 30
  cost_per_cpu_core_per_hour: 0.01
  cost_per_gb_ram_per_hour: 0.005
  csv_output_dir: /var/log/vm_autoscale/billing/
  webhook_script: ""
  webhook_url: ""
```

| Key | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `false` | Only spec **recording** is automatic |
| `billing_period_days` | int | `30` | |
| `cost_per_cpu_core_per_hour` | float | `0.01` | |
| `cost_per_gb_ram_per_hour` | float | `0.005` | |
| `csv_output_dir` | path | `/var/log/vm_autoscale/billing/` | Created at startup; holds `billing_data.json` |
| `webhook_script` | path | `""` | Only fires from report generation |
| `webhook_url` | url | `""` | Only fires from report generation |

See [billing tracking](/guide/billing) for what is and is not automatic.

## Complete example

```yaml
scaling_thresholds:
  cpu: { high: 80, low: 20 }
  ram: { high: 85, low: 25 }

scaling_limits:
  min_cores: 1
  max_cores: 8
  min_ram_mb: 1024
  max_ram_mb: 16384

check_interval: 300
scale_cooldown: 600
auto_configure_hotplug: false

proxmox_hosts:
  - name: pve1
    host: 10.0.0.11
    ssh_user: root
    ssh_key: /root/.ssh/vm_autoscale_rsa
    ssh_port: 22
  - name: pve2
    host: 10.0.0.12
    ssh_user: root
    ssh_key: /root/.ssh/vm_autoscale_rsa
    ssh_port: 22

virtual_machines:
  - { vm_id: 101, proxmox_host: pve1, scaling_enabled: true,  cpu_scaling: true,  ram_scaling: true  }
  - { vm_id: 102, proxmox_host: pve1, scaling_enabled: true,  cpu_scaling: true,  ram_scaling: false }
  - { vm_id: 201, proxmox_host: pve2, scaling_enabled: false, cpu_scaling: true,  ram_scaling: true  }

host_limits:
  max_host_cpu_percent: 85
  max_host_ram_percent: 90

logging:
  level: INFO
  log_file: /var/log/vm_autoscale.log

gotify:
  enabled: true
  server_url: https://gotify.internal.example
  app_token: REPLACE_ME
  priority: 5

alerts:
  email_enabled: false

billing:
  enabled: false
```

## Validating before restart

```bash
python3 -c "import yaml; yaml.safe_load(open('/usr/local/bin/vm_autoscale/config.yaml'))" \
  && echo "YAML OK"
```

This catches syntax errors only. Semantic problems — a `proxmox_host` that matches nothing, a missing `ssh_port` — surface at runtime.
