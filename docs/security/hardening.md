---
title: Hardening guide
description: Concrete steps to reduce the exposure of a Proxmox VM Autoscale deployment — file permissions, SSH configuration, systemd confinement and log handling.
---

# Hardening guide

Ordered roughly by effort-to-benefit. Everything here is optional; none of it is done for you beyond the file permissions the installer sets.

## Verify config permissions

Do this first, and after every install or upgrade.

```bash
ls -l /usr/local/bin/vm_autoscale/config.yaml
# want: -rw------- 1 root root
```

If it shows anything broader:

```bash
sudo chown root:root /usr/local/bin/vm_autoscale/config.yaml
sudo chmod 600 /usr/local/bin/vm_autoscale/config.yaml
sudo chown -R root:root /etc/vm_autoscale
sudo chmod 700 /etc/vm_autoscale
sudo chmod 600 /etc/vm_autoscale/config.yaml.backup
```

::: danger If you installed before the permissions fix
The installer's recursive `chmod 755` left the config world-readable, meaning every local user could read your Proxmox root password. Fix the permissions **and rotate that password and any SMTP credentials** — assume they were exposed for as long as the file was readable.
:::

## Use a dedicated SSH key

Do not reuse an existing admin key.

```bash
sudo ssh-keygen -t ed25519 -f /root/.ssh/vm_autoscale_ed25519 -N "" \
  -C "vm-autoscale@$(hostname -f)"
sudo chmod 600 /root/.ssh/vm_autoscale_ed25519
```

```bash
sudo ssh-copy-id -i /root/.ssh/vm_autoscale_ed25519.pub root@10.0.0.11
```

```yaml
proxmox_hosts:
  - name: pve1
    host: 10.0.0.11
    ssh_user: root
    ssh_key: /root/.ssh/vm_autoscale_ed25519
    ssh_port: 22
    # ssh_password intentionally absent — it would override the key
```

::: warning The key must be unencrypted
Ed25519, ECDSA, RSA and DSS key types all load. There is no passphrase option, though, so an encrypted key cannot be used — protect it with file permissions instead.
:::

Remove `ssh_password` entirely — leaving it set means the key is ignored.

## Restrict what the key can do

You cannot get to true least privilege — the service genuinely needs `qm set` — but you can bound the key.

```
# /root/.ssh/authorized_keys on each Proxmox node
from="10.0.0.5",no-agent-forwarding,no-port-forwarding,no-X11-forwarding,no-pty ssh-rsa AAAA...
```

`from=` is the useful part: the key becomes worthless from anywhere but the autoscaler's address. `no-pty` prevents interactive use while still allowing command execution.

A forced command is tempting but impractical here, since the service issues several different commands with variable arguments. A wrapper script that whitelists `qm status|config|set` and `pvesh get` for specific VMIDs is possible if you want to invest in it.

## Pin SSH host keys

The client trusts any host key it is offered and does not pin. You cannot change that from configuration, so mitigate around it:

- **Segment the network.** Put management SSH on a dedicated VLAN reachable only from the autoscaler.
- **Use addresses, not names.** `host: 10.0.0.11` removes DNS from the attack path.
- **Pre-populate `known_hosts`** so at least an interactive `ssh` from that box fails loudly if a key changes:

  ```bash
  ssh-keyscan -H 10.0.0.11 | sudo tee -a /root/.ssh/known_hosts
  ```

  The service will not consult it, but it gives you a tripwire.
- **Alert on host key changes** with a periodic `ssh-keyscan` diff.

## Confine the systemd unit

The shipped unit runs as root with no sandboxing. A drop-in adds meaningful confinement without touching the packaged file:

```bash
sudo systemctl edit vm_autoscale.service
```

```ini
[Service]
# Filesystem
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths=/var/log /var/log/vm_autoscale
ReadOnlyPaths=/usr/local/bin/vm_autoscale

# Privileges
NoNewPrivileges=true
RestrictSUIDSGID=true
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectKernelLogs=true
ProtectControlGroups=true
ProtectClock=true
ProtectHostname=true
RestrictNamespaces=true
RestrictRealtime=true
LockPersonality=true
MemoryDenyWriteExecute=true

# Network — outbound only, no unusual address families
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

# Restart behaviour
RestartSec=10
```

```bash
sudo systemctl daemon-reload
sudo systemctl restart vm_autoscale.service
sudo systemctl status vm_autoscale.service
systemd-analyze security vm_autoscale.service
```

::: warning Test this
`ProtectSystem=strict` makes the whole filesystem read-only except `ReadWritePaths`. If you changed `log_file` or `csv_output_dir`, add those paths. Watch the first cycle after applying it.
:::

Running the service on a Proxmox node itself limits how far you can go — `qm` needs access that heavy confinement would break. The directives above are safe because the service only *SSHes out*; it does not run `qm` locally.

## Protect the log

The log names your nodes and VMs and reveals capacity patterns.

```bash
sudo chmod 640 /var/log/vm_autoscale.log
sudo chown root:adm /var/log/vm_autoscale.log
```

Rotate it — nothing does by default:

```
# /etc/logrotate.d/vm_autoscale
/var/log/vm_autoscale.log {
    weekly
    rotate 8
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
    create 0640 root adm
}
```

`copytruncate` is required: the service holds the file open for its lifetime.

For an audit trail, ship the journal somewhere append-only:

```
# /etc/rsyslog.d/50-vm-autoscale.conf
if $programname == 'python3' and $msg contains 'vm_autoscale' then @@logs.internal.example:514
```

## Firewall the outbound path

The service needs exactly three kinds of outbound connection: SSH to your nodes, HTTPS to Gotify, SMTP to your relay. Nothing else.

```bash
# nftables sketch — adapt to your ruleset
nft add rule inet filter output ip daddr 10.0.0.0/24 tcp dport 22 accept
nft add rule inet filter output ip daddr 10.0.1.20   tcp dport 443 accept
nft add rule inet filter output ip daddr 10.0.1.25   tcp dport 587 accept
```

Egress filtering is what turns "the autoscaler was compromised" into "the autoscaler was compromised and could only talk to three known hosts".

## Keep dependencies current

```bash
pip3 install pip-audit
pip-audit -r /usr/local/bin/vm_autoscale/requirements.txt
```

Dependabot watches the repository weekly. On your host you have to run the upgrade yourself:

```bash
pip3 install --upgrade paramiko PyYAML requests
sudo systemctl restart vm_autoscale.service
```

## Do not install by piping curl into bash

```bash
# Fetch, read, then run
curl -fsSL https://raw.githubusercontent.com/fabriziosalmi/proxmox-vm-autoscale/main/install.sh \
  -o /tmp/install.sh
less /tmp/install.sh
sudo bash /tmp/install.sh
```

Better still, pin to a release rather than tracking `main`:

```bash
sudo git clone --branch v1.3.0 --depth 1 \
  https://github.com/fabriziosalmi/proxmox-vm-autoscale /usr/local/bin/vm_autoscale
```

## Rotate credentials

Nothing rotates automatically. On a schedule that suits you:

```bash
# New key
sudo ssh-keygen -t ed25519 -f /root/.ssh/vm_autoscale_ed25519.new -N ""
sudo ssh-copy-id -i /root/.ssh/vm_autoscale_ed25519.new.pub root@10.0.0.11
# Update config.yaml, restart, verify a full cycle, then remove the old key
# from authorized_keys on every node
```

Rotate immediately if `config.yaml` was ever readable by a non-root user, if the host was compromised, or if someone with access has left.

## Checklist

- [ ] `config.yaml` is `600`, root-owned
- [ ] `/etc/vm_autoscale` is `700`, backup inside is `600`
- [ ] Key authentication, dedicated key, `ssh_password` removed
- [ ] `from=` restriction on the key in `authorized_keys`
- [ ] Management SSH on a segmented network
- [ ] systemd hardening drop-in applied and verified
- [ ] Log permissions set and rotation configured
- [ ] Outbound firewall rules in place
- [ ] `pip-audit` clean
- [ ] Installed from a pinned release, not `curl | bash`
- [ ] Credential rotation scheduled
