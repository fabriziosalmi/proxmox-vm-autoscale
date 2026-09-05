---
title: Billing tracking
description: How Proxmox VM Autoscale records spec changes for usage-based billing, exactly which parts run automatically, and how to generate a costed CSV report yourself.
---

# Billing tracking

If you resell Proxmox capacity, autoscaling turns a fixed monthly spec into a variable one, and you probably want to bill for what a customer actually consumed. The `BillingTracker` module records every spec change with a timestamp and can turn a period into a costed CSV report.

::: warning Reconcile before you invoice anyone
Reports are generated from what the service observed. If it was down, restarted, or could not reach a node, those gaps are not in the data. Check the figures against your own records before billing a customer.
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

## What the service does on its own

| Capability | Automatic? |
|---|---|
| Recording a spec change after each successful scaling action | **Yes** |
| Recording VM start/stop transitions | **Yes** |
| Persisting everything to `billing_data.json` | **Yes** |
| Generating a CSV report once a billing period elapses | **Yes** |
| Firing `webhook_script` / `webhook_url` with that report | **Yes** |
| Setting a human-readable VM name | No — call `set_vm_name` yourself |

The period clock starts the first time the service runs with billing enabled, and is persisted, so it survives restarts. The first period therefore ends `billing_period_days` after you switched billing on, not on a calendar boundary.

State transitions are recorded, not sampled: one entry when a VM stops, one when it starts again. A VM that stays up for a month adds a single record.

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

## Generating a report out of band

The service emits one automatically at the end of each period. To produce one on demand — a mid-period check, or a re-run after fixing a rate — run this on the machine hosting the service, as a user who can read `csv_output_dir`:

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

Cost is charged **only for the hours the VM was up**. A guest powered off for half the period pays for half of it, at whatever spec it held while running.

A VM that did not change spec during the period is still billed correctly: the last spec recorded before the period starts is carried in as the opening value. Earlier versions filtered strictly to the period and billed such a VM zero, which is the opposite of what a stable customer should see.

::: tip Where the numbers can still be wrong
The service bills from what it observed. If it was stopped for two days, those two days have no state records and are treated as uptime at the last known spec. Long outages of the autoscaler itself are worth reconciling by hand.

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
