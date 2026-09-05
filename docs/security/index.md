---
title: Threat model
description: What Proxmox VM Autoscale exposes, what an attacker gains at each level of access, and which risks are inherent to the design versus fixable defects.
---

# Threat model

This service holds credentials for `root` on your hypervisors and executes commands against live guests. That is a high-value target. This page states plainly what it exposes, so you can decide where to run it and what to protect.

## What the service can do

Given its credentials, the service can — and does — run arbitrary `qm` and `pvesh` commands as root on every node in its config. In practice it only issues `qm status`, `qm config`, `qm set` and `pvesh get`, but nothing constrains it to those. **Anyone who can modify `config.yaml`, `autoscale.py` or the module files can run any command as root on every configured hypervisor.**

That is the core of the model. Everything else follows from it.

## Assets

| Asset | Where it lives | Exposure if compromised |
|---|---|---|
| Proxmox root SSH password or key | `config.yaml`, plain text | Full control of every configured node, and therefore every guest on them |
| SMTP credentials | `config.yaml`, plain text | Mail relay abuse, spoofed mail from your domain |
| Gotify application token | `config.yaml`, plain text | Push notifications to your operators; useful for social engineering |
| SSH private key | Path referenced in `config.yaml` | Same as the password |
| The service code | `/usr/local/bin/vm_autoscale/` | Arbitrary root execution on all nodes at the next cycle |
| Log file | `/var/log/vm_autoscale.log` | VMIDs, node names, usage patterns, capacity — reconnaissance |
| Billing data | `billing_data.json` | Customer capacity history |

## Attacker positions

### Unprivileged local user on the autoscaler host

**Before the recent fix**, the installer's recursive `chmod 755` left `config.yaml` world-readable — so any local user could simply read the Proxmox root password. If you installed before that change, [check and fix it now](/security/hardening#verify-config-permissions).

After the fix, `config.yaml` is mode `600` and root-owned. What remains readable to a local user:

- `/var/log/vm_autoscale.log`, unless you restrict it — node names, VMIDs, capacity
- The service code itself (mode `755`), which reveals the config path and structure
- Process arguments via `/proc`, which show the script path but no credentials

### Network attacker between the service and a node

Host keys are now verified. Under the default `accept-new` policy the service records a node's key on first contact and **refuses to connect if it later changes**, so an attacker who interposes after that first connection is blocked and logged rather than handed root credentials.

What remains is the first connection itself: trust-on-first-use means an attacker already in position at that moment is recorded as legitimate. Set `ssh_host_key_policy: strict` and pre-populate `ssh_known_hosts` with `ssh-keyscan` to close that too — see [hardening](/security/hardening#verify-ssh-host-keys).

Before this was configurable the client accepted any key on every connection and remembered nothing, which offered no protection at all. `ssh_host_key_policy: auto` reproduces that behaviour if you need it.

### Root on the autoscaler host

Total compromise of every configured hypervisor. There is no additional boundary: the credentials are readable, the code is modifiable, and the next cycle executes whatever it now says.

Treat the autoscaler host as being in the **same trust zone as the hypervisors themselves**. Running it on a general-purpose jump box that other people use is a mistake.

### Someone who can open a pull request

Merged code runs as root on hypervisors at the next service restart. The installer clones `main` directly, so anything merged reaches installations that reinstall. Review contributions with that in mind.

### Compromise of the installation path

```bash
bash <(curl -s https://raw.githubusercontent.com/.../main/install.sh)
```

This executes whatever is on `main` at that moment, as root, with no signature and no checksum. A compromised GitHub account, a malicious merge, or a TLS interception all lead to root execution. It is the recommended install method in the README, and it is a real supply-chain exposure. Read the script first, or [install manually](/guide/installation#option-b-manual-install).

## Design-level exposures

These are properties of how the service works. They are not going to be fixed by tightening a permission.

| Exposure | Why it exists | Can you mitigate it? |
|---|---|---|
| **Root SSH required** | Every action is a `qm` command | Not today; there is no least-privilege mode |
| **Credentials in plain text** | No secrets backend | File permissions and disk encryption only |
| **First-use host key trust** | `accept-new` records a key on first contact | `strict` plus a pre-populated `ssh_known_hosts` |
| **Service runs unconfined as root** | Shipped unit sets `User=root` with no sandboxing | Yes — [systemd drop-in](/security/hardening#confine-the-systemd-unit) |
| **VMIDs interpolated into shell strings** | No validation on `vm_id` | Config is admin-controlled, so not remotely exploitable |
| **No audit trail beyond the log** | No structured events | Ship the log somewhere append-only |

## What is not a risk

Worth stating, to keep attention where it belongs:

- **No network listener.** The service binds nothing. It cannot be reached from outside; it only makes outbound connections.
- **No inbound API, no web UI, no authentication surface.**
- **No guest agent.** Nothing is installed inside your VMs.
- **Outbound connections are limited** to SSH to configured nodes, HTTPS to your Gotify server, and SMTP to your relay.
- **No telemetry.** The service phones nothing home. See [privacy](/privacy).

## Dependencies

Three direct dependencies: `paramiko`, `PyYAML`, `requests`. All eight historical Dependabot alerts on this repository — mostly in `cryptography`, Paramiko's transitive dependency — are resolved. Dependabot runs weekly on pip.

Keep them current:

```bash
pip3 install --upgrade paramiko PyYAML requests
pip3 install pip-audit && pip-audit
```

## Recommended posture

1. Run it on a dedicated host, or on a Proxmox node itself — not on a shared jump box.
2. Use key authentication with a dedicated RSA key used for nothing else.
3. Keep management SSH on a segmented network.
4. Apply the [systemd hardening drop-in](/security/hardening#confine-the-systemd-unit).
5. Verify `config.yaml` is `600` and root-owned after every install or upgrade.
6. Ship logs off-host if you need an audit trail.
7. Subscribe to releases so security fixes reach you.

Full step-by-step: the [hardening guide](/security/hardening). To report something: [responsible disclosure](/security/disclosure).
