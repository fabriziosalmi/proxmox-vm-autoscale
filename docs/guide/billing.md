---
title: Billing tracking
description: How Proxmox VM Autoscale records spec changes for usage-based billing, exactly which parts run automatically, and how to generate a costed CSV report yourself.
---

# Billing tracking

If you resell Proxmox capacity, autoscaling turns a fixed monthly spec into a variable one, and you probably want to bill for what a customer actually consumed. The `BillingTracker` module records every spec change with a timestamp and can turn a period into a costed CSV report.

::: warning Read this before you invoice anyone
Only part of this feature is wired into the running service. Enabling `billing` records data; it does **not** produce reports on its own, and the cost calculation currently ignores downtime. The [what is automatic](#what-is-automatic-and-what-is-not) section is precise about the boundary. Reconcile against your own records before billing a customer.
:::

## Configuration

```yaml
billing:
  enabled: true
  billing_period_days: 30
  cost_per_cpu_core_per_hour: 0.01
  cost_per_gb_ram_per_hour: 0.005
  csv_output_dir: /var/log/vm_autoscale/billing/
  webhook_script: ""
  webhook_url: ""
```

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Instantiates the tracker at startup |
| `billing_period_days` | `30` | Length of a period, counted backwards from now |
| `cost_per_cpu_core_per_hour` | `0.01` | Currency-agnostic; you decide the unit |
| `cost_per_gb_ram_per_hour` | `0.005` | Divided by 1024 internally to cost per MB |
| `csv_output_dir` | `/var/log/vm_autoscale/billing/` | Created at startup; also holds the state file |
| `webhook_script` | `""` | Executable receiving the report as JSON on stdin |
| `webhook_url` | `""` | Endpoint receiving the report as a JSON `POST` |

## What is automatic, and what is not

| Capability | Automatic? |
|---|---|
| Recording a spec change after each successful scaling action | **Yes** |
| Persisting records to `billing_data.json` | **Yes** |
| Recording VM start/stop events | **No** — nothing in the service calls it |
| Generating a CSV report | **No** — nothing in the service calls it |
| Firing `webhook_script` / `webhook_url` | **No** — only reachable via report generation |
| Setting a human-readable VM name | **No** |

So with `enabled: true` you get a growing `billing_data.json` full of spec changes, and nothing else, until you invoke the reporting yourself. `record_vm_state_change`, `generate_period_report`, `export_csv`, `run_webhook` and `set_vm_name` are all public API — they are simply not called by `autoscale.py`. See [known limitations](/reference/limitations#billing-reports-are-not-generated-automatically).

## The data file

```
/var/log/vm_autoscale/billing/billing_data.json
```

```json
{
  "spec_changes": {
    "101": [
      { "timestamp": "2026-09-01T09:14:22.104512", "cpu_cores": 2, "ram_mb": 4096 },
      { "timestamp": "2026-09-01T11:02:07.883210", "cpu_cores": 3, "ram_mb": 4096 }
    ]
  },
  "state_changes": {},
  "vm_names": {}
}
```

::: warning It grows without bound
The whole file is rewritten on every record, and nothing prunes old entries. A busy fleet accumulates one entry per scaling action forever, and each write serialises the entire history. Archive and truncate it periodically:

```bash
sudo systemctl stop vm_autoscale.service
sudo mv /var/log/vm_autoscale/billing/billing_data.json \
        /var/backups/billing_data-$(date +%F).json
sudo systemctl start vm_autoscale.service
```

Timestamps are naive local time (`datetime.now()`), with no timezone and no DST handling. Reports spanning a DST boundary will be off by an hour.
:::

## Generating a report

Run this on the machine hosting the service, as a user who can read `csv_output_dir`:

```python
#!/usr/bin/env python3
"""Generate a billing CSV for every configured VM for the current period."""
import logging
import sys

import yaml

sys.path.insert(0, "/usr/local/bin/vm_autoscale")
from billing_tracker import BillingTracker

logging.basicConfig(level=logging.INFO)

with open("/usr/local/bin/vm_autoscale/config.yaml") as fh:
    config = yaml.safe_load(fh)

tracker = BillingTracker(config, logging.getLogger("billing"))

for vm in config["virtual_machines"]:
    report = tracker.generate_period_report(str(vm["vm_id"]))
    if report:
        print(f"VM {report.vm_id}: {report.total_cost:.4f} "
              f"({report.period_start:%Y-%m-%d} → {report.period_end:%Y-%m-%d})")
```

`generate_period_report` calculates the period, writes the CSV, and fires the webhook if one is configured. Store the script **outside** `/usr/local/bin/vm_autoscale/` — the installer deletes that directory on reinstall.

Run it monthly:

```
0 2 1 * * root /usr/local/bin/vm-autoscale-billing.py >> /var/log/vm_autoscale/billing/cron.log 2>&1
```

## The CSV

```
billing_101_20260806_20260905.csv
```

Sections: summary (VMID, name, period), resource statistics (min/max/average cores and RAM), uptime statistics, total cost, then the full list of spec changes.

Note that "min/max/average" are computed across **recorded change events**, not weighted by time. Ten scale-ups in one hour and one steady week at the resulting spec average out to something that is not the average spec. The `total_cost` figure *is* time-weighted; the statistics are not.

## How cost is calculated

Each spec is charged for the wall-clock interval it was in effect:

```
cost = Σ (cores × cost_per_cpu_core_per_hour × hours_at_that_spec)
     + Σ (ram_mb × (cost_per_gb_ram_per_hour ÷ 1024) × hours_at_that_spec)
```

The first recorded spec is treated as being in effect from `period_start`, and the last one runs to `period_end`.

::: danger Downtime is billed as uptime
The cost calculation does not consult the uptime records — and since nothing records VM state changes in the first place, uptime is reported as 100% regardless. A VM powered off for a week is billed for that week at its last known spec. This is a real defect, not a pricing policy; see [known limitations](/reference/limitations#downtime-is-billed-as-uptime).
:::

Also: a VM that never scaled during a period has no spec records for it, and is billed **zero** — not "the spec it sat at the whole time". Usage-based billing here means *change*-based recording, so a customer on a stable spec produces no data.

## Webhooks

Both fire from `generate_period_report`, after the CSV is written.

**Script** — executed with the report JSON on stdin, 60-second timeout:

```bash
#!/bin/bash
# /usr/local/bin/vm-autoscale-billing-hook.sh
jq -r '"\(.vm_id),\(.total_cost),\(.period_end)"' >> /var/lib/billing/ledger.csv
```

```yaml
billing:
  webhook_script: /usr/local/bin/vm-autoscale-billing-hook.sh
```

The path must exist and be executable, otherwise it is skipped silently.

**URL** — a JSON `POST` with a 30-second timeout:

```yaml
billing:
  webhook_url: https://billing.example.com/api/usage
```

There is no authentication header, no signature and no retry. Put it behind something that authenticates by source IP or mTLS, and do not treat delivery as guaranteed.

## Report fields

| Field | Type | Notes |
|---|---|---|
| `vm_id` | string | |
| `vm_name` | string | `VM-<id>` unless `set_vm_name` was called |
| `period_start` / `period_end` | ISO 8601 | Naive local time |
| `min_cpu_cores`, `max_cpu_cores`, `avg_cpu_cores` | number | Across change events, not time-weighted |
| `min_ram_mb`, `max_ram_mb`, `avg_ram_mb` | number | Same caveat |
| `total_uptime_hours`, `total_downtime_hours`, `uptime_percentage` | number | Currently always full uptime |
| `spec_changes` | array | Every record in the period |
| `total_cost` | number | Time-weighted, rounded to 4 decimals |
