---
title: Known limitations
description: A complete, honest catalogue of the known defects and design constraints in Proxmox VM Autoscale, with the impact of each and the workaround where one exists.
---

# Known limitations

Everything on this page is known and reproducible at the time of writing. It is here so you can decide what you are accepting before you run this against your infrastructure, rather than discovering it from a log line at 3am.

Items are grouped by whether they can bite you silently.

## Silent failure modes

These produce wrong behaviour without an obvious error.

### Metric parsing is format-sensitive

Guest usage comes from scraping a human-readable table:

```bash
pvesh get /cluster/resources | grep 'qemu/<vmid>' | awk -F '│' '{print $6, $15, $16}'
```

This depends on the box-drawing separator, on those exact column indices, and on `grep` matching only the intended row. Any of them can change between Proxmox versions.

**Impact:** when parsing fails, usage falls back to `0.0`. Zero is below every reasonable `low` threshold, so **a parse failure is indistinguishable from an idle guest** and walks every affected VM down to its minimum, one step per cycle.

**Detection:** `CPU usage not found in output.` / `RAM memory values not found in output.` in the log.

**Workaround:** verify the command by hand on your Proxmox version before deploying, and alert on those warnings. A `--output-format json` implementation would remove the whole class of problem.

### `grep 'qemu/101'` also matches VMID 1010

The VMID filter is a substring match, so a four-digit VMID beginning with your three-digit one matches too, and `awk` reads whichever row came first.

**Impact:** decisions made from another VM's usage.

**Workaround:** avoid VMID prefixes of each other among managed VMs.

### A `proxmox_host` typo skips the VM without a word

VMs are matched to hosts by exact string equality on `name`. A mismatch means the VM entry is never reached — no warning, no error, it simply never appears in the log.

**Workaround:** after any config change, confirm every managed VM appears in a `VM <id> current usage` line within one cycle.

### Command failures are reported as successes

`execute_command` returns output and exit status without raising on a non-zero status, and the callers that issue `qm set` discard the result. So:

```
[INFO] RAM balloon set to 4096 MB for VM 101 (hotplug applied).
```

is logged whether or not `qm set` succeeded, and whether or not the guest honoured it.

**Workaround:** verify with `qm config <vmid>` when a change matters.

### Per-VM `thresholds` are ignored

`config.yaml` shows a `thresholds:` block inside each `virtual_machines` entry. Nothing reads it — only the global `scaling_thresholds` is consulted.

**Impact:** you may believe a VM has bespoke thresholds when it does not.

**Workaround:** run a second instance with its own config for a VM that genuinely needs different numbers.

### Downtime is billed as uptime

`BillingTracker._calculate_resource_cost` accepts the uptime records and never uses them, despite an in-code comment saying cost is charged for uptime only. Compounding this, nothing in the service calls `record_vm_state_change`, so there are no uptime records to begin with — every report shows 100% uptime.

**Impact:** a VM powered off for a week is billed for that week at its last known spec.

**Workaround:** reconcile against your own uptime source before invoicing.

## Documented-but-absent behaviour

### Billing reports are not generated automatically

Enabling `billing` wires up exactly one thing: recording a spec change after each successful scaling action. `generate_period_report`, `export_csv`, `run_webhook`, `record_vm_state_change` and `set_vm_name` are public API that nothing calls.

**Impact:** no CSV appears, no webhook fires, no uptime is tracked.

**Workaround:** the [billing page](/guide/billing#generating-a-report) has a script that calls the API on a schedule.

### `logging` in `config.yaml` is inert by default

If `logging_config.json` exists it is loaded via `dictConfig` and the `logging` section of `config.yaml` is never consulted. The installer ships that file, so in a default install the YAML block does nothing.

**Workaround:** edit `logging_config.json`.

## Operational constraints

### Cooldown state is lost on restart

Cooldown timers live in process memory. `systemctl restart` clears them and the first cycle after can scale immediately. Repeated restarts remove rate limiting entirely.

### `check_interval` is a floor, not a period

Cycles are sequential and blocking: one SSH connection per VM, opened and closed, hosts processed one after another. With many VMs a cycle can exceed `check_interval`, at which point cycles simply run back to back. There is no parallelism and no scheduling guarantee.

### The host gate blocks scale-down too

A node above `max_host_cpu_percent` or `max_host_ram_percent` has *all* scaling suppressed on it, including shrinking idle guests — the one action that would give the node headroom back.

### Step sizes are not configurable

Fixed at 1 core and 512 MB per action, in `vm_manager.py`. Recovering from a large jump takes many cycles.

### Limits are global

`scaling_limits` applies to every VM. A 2-core web server and a 16-core database cannot have different ceilings in one instance.

### Single instance, no HA

One process, no leader election, no shared state. Two instances managing the same VM will fight, since neither sees the other's cooldowns.

### `ssh_port` is read unconditionally

`host['ssh_port']` is indexed directly rather than via `.get()`, so a host entry without that key raises `KeyError` and every VM on it fails. The shipped example config omits it on `host2`.

**Workaround:** set `ssh_port` on every host.

### `ssh_password` silently overrides `ssh_key`

When both are present the password is used. The shipped example fills in both with placeholders.

## Security constraints

Covered in full in the [threat model](/security/); summarised here.

### Only RSA SSH keys are supported

The key file is loaded specifically as an RSA key, so Ed25519 and ECDSA keys fail to load — despite `SECURITY.md` recommending Ed25519.

**Workaround:** `ssh-keygen -t rsa -b 4096`, or use password authentication.

### SSH host keys are accepted automatically

The client uses an auto-add policy: any host key is trusted on first contact and no pinning is performed. An attacker positioned between the service and a node can present their own key and receive your root credentials.

**Workaround:** the [hardening guide](/security/hardening#pin-ssh-host-keys) shows how to constrain this at the network and configuration level.

### Credentials are stored in plain text

`ssh_password`, `smtp_password` and `app_token` are read from `config.yaml` as-is. There is no secrets backend and no environment-variable support.

**Workaround:** mode `600`, root-owned, on an encrypted filesystem; prefer key authentication over passwords.

### The service runs as root, unconfined

The shipped unit sets `User=root` with no systemd sandboxing directives.

**Workaround:** [hardening drop-in](/security/hardening#confine-the-systemd-unit).

### VMIDs are interpolated into shell commands

`vm_id` from the config is formatted straight into command strings without validation. Since the config is administrator-controlled this is not remotely exploitable, but a malformed VMID produces malformed commands rather than a clear error.

## Project-level notes

### Release tags before v1.3.0 are not in chronological order

`v1.2.0` was published in December 2025 and `v0.1.1` in April 2026 — the two release lines were never reconciled. `v1.2.0` is now [documented retroactively](/reference/changelog) and numbering is monotonic from `v1.3.0` onwards, but the historical tags stay as they are.

### No dry-run mode

There is no way to see what the service *would* do. The closest approximation is `scaling_enabled: false` everywhere, which produces the monitoring output without acting.

### No metrics endpoint

No Prometheus exporter, no health endpoint, no structured events. Monitoring means parsing the log — see [operations](/guide/operations#monitoring-the-autoscaler-itself).

---

Found something not on this list? [Open an issue](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/new/choose). For anything with security impact, follow [responsible disclosure](/security/disclosure) instead.
