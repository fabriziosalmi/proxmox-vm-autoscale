---
title: Troubleshooting
description: Diagnose the common failure modes of Proxmox VM Autoscale — service won't start, SSH failures, VMs that never scale, usage stuck at zero, and changes that don't reach the guest.
---

# Troubleshooting

Start here, in order: is the service running, is it connecting, is it reading usage, is it deciding to scale, does the change reach the guest.

```bash
systemctl is-active vm_autoscale.service
journalctl -u vm_autoscale.service -n 50 --no-pager
tail -50 /var/log/vm_autoscale.log
```

## The service will not start

```bash
journalctl -u vm_autoscale.service -n 50 --no-pager | grep -i critical
```

### `Missing required configuration sections: ...`

One of `scaling_thresholds`, `scaling_limits`, `proxmox_hosts`, `virtual_machines` is absent. All four are mandatory even if you are not using them meaningfully.

### `Configuration file not found at /usr/local/bin/vm_autoscale/config.yaml`

The path is hardcoded. If you installed elsewhere, either symlink it or edit `main()` in `autoscale.py`.

### `Gotify is enabled but configuration is incomplete`

`gotify.enabled: true` with `server_url` or `app_token` missing. Same shape of error for email:

```
Email alerts are enabled but missing configuration: smtp_server, smtp_user
```

### YAML syntax errors

```bash
python3 -c "import yaml; yaml.safe_load(open('/usr/local/bin/vm_autoscale/config.yaml'))"
```

Tabs are the usual culprit — YAML forbids them for indentation.

### Permission denied writing the log

The service needs write access to `logging_config.json`'s `filename` (default `/var/log/vm_autoscale.log`). Running as root this is fine; if you changed `User=`, it is probably not.

## SSH failures

### `Authentication failed for 192.168.1.10`

Not retried — credentials are wrong, or the key was rejected. Test the exact same path by hand:

```bash
ssh -p 22 -i /root/.ssh/id_rsa root@192.168.1.10 'qm list'
```

### Key authentication fails but the key is fine

Ed25519, ECDSA, RSA and DSS keys all load. The usual remaining cause is an **encrypted** key: there is no passphrase option, so the file must be unencrypted.

```bash
grep -q ENCRYPTED /root/.ssh/id_ed25519 && echo "encrypted — will not load"
```

The error names the file and the reason:

```
Could not load private key /root/.ssh/id_ed25519: ...
```

Generate an unencrypted key for the service and protect it with file permissions instead:

```bash
ssh-keygen -t ed25519 -f /root/.ssh/vm_autoscale_ed25519 -N ""
ssh-copy-id -i /root/.ssh/vm_autoscale_ed25519.pub root@192.168.1.10
```

### `Failed to connect to 192.168.1.10 after 5 attempts`

Five attempts with exponential backoff (1, 2, 4, 8, 16 s) — roughly 31 seconds before giving up. Network, firewall, or `sshd` not listening on `ssh_port`.

```bash
nc -vz 192.168.1.10 22
```

### Both password and key configured

If `ssh_password` is set it wins; `ssh_key` is ignored entirely. Remove or blank the password to force key auth. The shipped example config has **both** filled in with placeholders, so leaving it half-edited means the placeholder password is what gets used.

### Too many SSH sessions

A connection is opened and closed per VM per cycle. With many VMs and a short `check_interval`, you can hit `MaxStartups` or `MaxSessions` on the node. Raise `check_interval`, or raise the limits in `/etc/ssh/sshd_config`.

## A VM never scales

Work down this list:

**1. Is it enabled?**

```yaml
scaling_enabled: true
cpu_scaling: true
ram_scaling: true
```

**2. Does `proxmox_host` match a host `name` exactly?** A mismatch means the VM entry is never reached — and there is no warning for it. Case and whitespace matter.

**3. Is the guest running?**

```
[INFO] VM 101 is not running. Skipping scaling.
```

**4. Is the host blocking it?**

```
[WARNING] Host pve1 resources maxed out. Skipping scaling.
```

Raise `host_limits`, or accept that the node is genuinely full. On ZFS hosts, ARC counts as used memory — check `arc_summary` before concluding the ceiling is real.

**5. Is usage actually crossing a threshold?**

```
[INFO] VM 101 current usage - CPU: 45.2%, RAM: 62.1%
```

45% is inside a 20–80 dead band. Nothing is wrong.

**6. Is it already at a limit?**

```
[INFO] No CPU scaling required.
```

Check `qm config 101` against your `scaling_limits`.

**7. Is it in cooldown?** No log line is emitted for this. Look at the timestamp of the last `Scaled ...` line for that VM and compare against `scale_cooldown`.

## Usage reads as "unavailable"

```
[INFO]    VM 101 current usage - CPU: unavailable, RAM: unavailable
[WARNING] VM 101: no usage metrics available this cycle. Skipping scaling
          rather than treating the VM as idle.
```

Scaling is skipped for that cycle, so nothing bad happens — but nothing scales either. Run the query by hand on the node:

```bash
pvesh get /cluster/resources --output-format json | jq '.[] | select(.vmid == 101)'
```

You want a `qemu` row with `cpu`, `mem` and `maxmem`. The log says which part failed:

| Log line | Cause |
|---|---|
| `could not parse cluster resources as JSON` | `pvesh` returned something else — check the command runs as your `ssh_user` |
| `not present in cluster resources` | Wrong VMID, or the guest lives in another cluster |
| `no 'cpu' field in cluster resources` | Unexpected payload shape for your Proxmox version |
| `maxmem is 0` | The guest reports no memory ceiling |

::: info This used to be dangerous
Before the switch to JSON, an unreadable metric was reported as `0.0`, which reads as "completely idle" and scaled the VM down one step per cycle until it hit its minimum. It is now reported as unavailable and skipped.
:::

## The change does not reach the guest

### RAM changes but the guest does not see it

```
[WARNING] VM 101 has memory hotplug enabled but NUMA is disabled.
          Memory changes will require a reboot.
```

Enable NUMA and **reboot the guest** — NUMA is a topology change and does not apply live.

If both are enabled and it still does not take effect, the balloon driver is missing or wedged inside the guest:

```bash
# inside the guest
lsmod | grep virtio_balloon
dmesg | grep -i balloon
```

### CPU count does not change inside the guest

Scaling `cores` requires a reboot; only `vcpus` is live. Look at which one the log said it changed:

```
[WARNING] VM 101: Increased cores to 5 (requires reboot for full effect)
          and vCPUs to 5 (hotplug applied).
```

Removing a vCPU is also unreliable — Windows guests in particular often refuse. QEMU accepts the command and the service reports success either way.

### `qm set` fails but the log says it succeeded

Command results are not checked for a non-zero exit status before the success line is written. Confirm on the node:

```bash
qm config 101 | grep -E 'cores|vcpus|memory|balloon'
```

## It scales up and down constantly

The dead band between `low` and `high` is too narrow for how the workload actually moves.

```yaml
scaling_thresholds:
  cpu:
    high: 85
    low: 15     # a wide band absorbs normal variation
```

Also raise `scale_cooldown`. Remember the effective interval is `max(check_interval, scale_cooldown)`, so a cooldown below the poll interval does nothing.

Repeated `systemctl restart` also defeats the cooldown, since the timers are in memory.

## Notifications do not arrive

```bash
grep -i notification /var/log/vm_autoscale.log
```

`Failed to send notification through any channel` includes the original message and the per-channel errors. Test Gotify independently:

```bash
curl -X POST "https://gotify.example.com/message" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -F "title=test" -F "message=test"
```

For SMTP, remember `starttls()` is always called — a relay that does not advertise STARTTLS will fail. Leave `smtp_password` empty to skip authentication on an IP-authenticated relay.

Also: notifications only fire on **actual changes**. A threshold crossed while at a limit or in cooldown sends nothing, by design.

## Billing produces no CSV

Expected. Report generation is not wired into the service — only spec recording is. See [billing](/guide/billing#what-is-automatic-and-what-is-not) for the script that generates one.

## Getting help

Before opening an issue, collect:

```bash
python3 --version
pveversion
systemctl status vm_autoscale.service --no-pager
journalctl -u vm_autoscale.service -n 100 --no-pager
qm config <vmid>
```

Then [open an issue](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/new/choose) with your config **with every credential removed** — `ssh_password`, `smtp_password`, `app_token`, host addresses if they are sensitive.
