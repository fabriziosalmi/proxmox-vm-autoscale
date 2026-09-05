---
title: Known limitations
description: A complete, honest catalogue of the known defects and design constraints in Proxmox VM Autoscale, with the impact of each and the workaround where one exists.
---

# Known limitations

Everything on this page is known and reproducible at the time of writing. It is here so you can decide what you are accepting before you run this against your infrastructure, rather than discovering it from a log line at 3am.

Items are grouped by whether they can bite you silently.

## Silent failure modes

These produce wrong behaviour without an obvious error.

### A `proxmox_host` typo skips the VM without a word

VMs are matched to hosts by exact string equality on `name`. A mismatch means the VM entry is never reached — no warning, no error, it simply never appears in the log.

**Workaround:** after any config change, confirm every managed VM appears in a `VM <id> current usage` line within one cycle.

## Documented-but-absent behaviour

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

### Single instance, no HA

One process, no leader election, no shared state. Two instances managing the same VM will fight, since neither sees the other's cooldowns.

### `ssh_password` silently overrides `ssh_key`

When both are present the password is used. The shipped example fills in both with placeholders.

## Security constraints

Covered in full in the [threat model](/security/); summarised here.

### Encrypted SSH keys are not supported

Ed25519, ECDSA, RSA and DSS key types all load, but there is no passphrase option, so the key file must be unencrypted.

**Workaround:** protect the key with file permissions and a `from=` restriction in `authorized_keys` instead.

### Host key trust is first-use, not pre-shared

The default `accept-new` policy records a node's host key the first time it is seen and refuses to connect if that key later changes. That closes the window on everything after the first connection, but the first connection itself is still trust-on-first-use: an attacker already in position at that moment would be recorded as legitimate.

**Workaround:** pre-populate `ssh_known_hosts` with `ssh-keyscan` before the first run and set `ssh_host_key_policy: strict`. See the [hardening guide](/security/hardening#verify-ssh-host-keys).

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

### A guest can still refuse a change the hypervisor accepted

`qm set` failures are now detected, but QEMU accepting a command is not the same as the guest honouring it. A missing balloon driver, or a kernel that refuses a CPU hot-unplug, produces a successful command and no change inside the guest.

**Workaround:** verify from inside the guest — `nproc`, `free -m` — when a change matters.

---

Found something not on this list? [Open an issue](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/new/choose). For anything with security impact, follow [responsible disclosure](/security/disclosure) instead.
