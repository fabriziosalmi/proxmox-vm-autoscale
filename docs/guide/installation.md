---
title: Installation
description: Install Proxmox VM Autoscale with the installer script or by hand, including what the installer changes on your system and how to uninstall it.
---

# Installation

Two routes: the installer script, or a manual install. The manual route is longer but you see every change, which matters for a service that will hold root credentials.

## What gets installed

Either way you end up with the same layout:

| Path | Contents | Mode |
|---|---|---|
| `/usr/local/bin/vm_autoscale/` | Python sources, `config.yaml`, `logging_config.json` | `755` |
| `/usr/local/bin/vm_autoscale/config.yaml` | Your hosts, VMs, credentials | **`600`, root-owned** |
| `/etc/vm_autoscale/config.yaml.backup` | Copy of the previous config, kept across reinstalls | `600` in `700` dir |
| `/etc/systemd/system/vm_autoscale.service` | systemd unit | `644` |
| `/var/log/vm_autoscale.log` | Service log | created on first run |

## Option A — installer script

```bash
bash <(curl -s https://raw.githubusercontent.com/fabriziosalmi/proxmox-vm-autoscale/main/install.sh)
```

::: danger You are piping a remote script into a root shell
This fetches whatever is on `main` at that moment and runs it as root, with no signature and no checksum. That is a real supply-chain exposure, not a theoretical one. If you would rather not, [download and read it first](#reviewing-the-installer) or use the [manual install](#option-b-manual-install).
:::

The script must run as root. It will:

1. Create `/etc/vm_autoscale/` (mode `700`) and back up any existing `config.yaml` into it.
2. `apt-get install` `python3`, `curl`, `bash`, `git`, `python3-paramiko`, `python3-yaml`, `python3-requests`, `python3-cryptography`.
3. **Delete** `/usr/local/bin/vm_autoscale/` if it exists, then clone the repository into it.
4. Restore your backed-up `config.yaml` over the shipped example.
5. `pip3 install -r requirements.txt` — best effort; see [below](#if-pip3-install-fails).
6. Set permissions — including locking `config.yaml` to mode `600`, because it holds your SSH password.
7. Install `vm_autoscale.service` from the repository and `systemctl enable` it — **without starting it**.

::: warning Step 3 removes the install directory
Anything you put inside `/usr/local/bin/vm_autoscale/` that is not `config.yaml` — a custom webhook script, a patched module — is deleted on every reinstall. Keep such files elsewhere and reference them by absolute path.
:::

### Reviewing the installer

```bash
curl -fsSL https://raw.githubusercontent.com/fabriziosalmi/proxmox-vm-autoscale/main/install.sh -o install.sh
less install.sh
sudo bash install.sh
```

### If `pip3 install` fails

On Proxmox VE 8 and other Debian 12+ systems, pip refuses to install into the system Python:

```
error: externally-managed-environment
```

This is expected and **not fatal**: the `apt-get` step above already installed `paramiko`, `PyYAML` and `requests` as system packages, so the pip step is redundant there. The installer prints a notice and carries on.

If you would rather have an isolated environment, use the [virtualenv variant](#running-in-a-virtualenv) below.

## Option B — manual install

```bash
sudo git clone https://github.com/fabriziosalmi/proxmox-vm-autoscale \
  /usr/local/bin/vm_autoscale
cd /usr/local/bin/vm_autoscale

# Dependencies — system packages on Debian/Proxmox
sudo apt-get install -y python3-paramiko python3-yaml python3-requests

# Lock down the config before you put credentials in it
sudo chown root:root config.yaml
sudo chmod 600 config.yaml

# Install the unit shipped in the repository
sudo install -m 644 -o root -g root vm_autoscale.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable vm_autoscale.service
```

::: info One unit, one source
This is the same file the installer script installs. Earlier versions of the installer generated a second unit inline, which had drifted from the tracked one — it was missing `RestartSec`, so a crash-looping service restarted as fast as systemd allowed. There is now a single definition.
:::

The unit hardcodes `/usr/bin/python3` and `/usr/local/bin/vm_autoscale`. If you install elsewhere, edit it to match.

### Running in a virtualenv

```bash
sudo python3 -m venv /usr/local/bin/vm_autoscale/.venv
sudo /usr/local/bin/vm_autoscale/.venv/bin/pip install -r \
  /usr/local/bin/vm_autoscale/requirements.txt
```

Then point the unit at it:

```ini
# /etc/systemd/system/vm_autoscale.service.d/venv.conf
[Service]
ExecStart=
ExecStart=/usr/local/bin/vm_autoscale/.venv/bin/python /usr/local/bin/vm_autoscale/autoscale.py
```

```bash
sudo systemctl daemon-reload
```

## Configure before you start

The service is enabled but **not started** after installation, on purpose. Edit the config first:

```bash
sudo nano /usr/local/bin/vm_autoscale/config.yaml
```

At minimum you need `proxmox_hosts` and `virtual_machines` filled in with real values. See [your first scaling VM](/guide/quick-start) for the smallest working example, and the [configuration reference](/reference/configuration) for every key.

```bash
sudo systemctl start vm_autoscale.service
sudo systemctl status vm_autoscale.service
```

## Upgrading

Re-running the installer preserves `config.yaml` through the backup/restore cycle. For a manual install:

```bash
cd /usr/local/bin/vm_autoscale
sudo cp config.yaml /etc/vm_autoscale/config.yaml.backup
sudo git pull
sudo systemctl restart vm_autoscale.service
```

Read the [changelog](/reference/changelog) before upgrading — behaviour changes are listed there, including one that makes previously-ignored config values start taking effect.

## Uninstalling

```bash
sudo systemctl stop vm_autoscale.service
sudo systemctl disable vm_autoscale.service
sudo rm /etc/systemd/system/vm_autoscale.service
sudo systemctl daemon-reload

sudo rm -rf /usr/local/bin/vm_autoscale
sudo rm -rf /etc/vm_autoscale          # contains your config backup
sudo rm -f  /var/log/vm_autoscale.log
```

Uninstalling does **not** revert changes made to your VMs. Guests left with `hotplug` and `numa: 1` set by the autoscaler keep those settings, and cores and memory stay wherever the last scaling action left them. Check `qm config <vmid>` on anything that matters.
