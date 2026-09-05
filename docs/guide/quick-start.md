---
title: Your first scaling VM
description: The smallest working config.yaml for Proxmox VM Autoscale, plus how to verify that a VM really scales instead of assuming it does.
---

# Your first scaling VM

The goal here is one VM, scaling, with proof. Get that working before you add the rest of the fleet.

## Step 1 — pick a VM you can disturb

You want a guest where an extra core appearing or a gigabyte disappearing will not ruin anyone's afternoon. Note its VMID (`qm list`) and the node it lives on.

## Step 2 — prepare the guest

Live scaling needs both hotplug and NUMA. You can set them by hand once:

```bash
qm set 101 -hotplug cpu,memory,network,disk,usb
qm set 101 -numa 1
```

Then **reboot the guest**. NUMA is a machine-topology change; it does not apply to a running VM. You can also let the service do this for you with `auto_configure_hotplug: true` — see [hotplug and NUMA](/guide/hotplug) for the trade-offs.

Verify:

```bash
qm config 101 | grep -E 'hotplug|numa|cores|vcpus|memory|balloon'
```

## Step 3 — minimal config

Replace the shipped `config.yaml` with something this small while you are testing:

```yaml
# /usr/local/bin/vm_autoscale/config.yaml

scaling_thresholds:
  cpu:
    high: 80
    low: 20
  ram:
    high: 85
    low: 25

scaling_limits:
  min_cores: 1
  max_cores: 4
  min_ram_mb: 1024      # NUMA misbehaves below 1 GB — do not lower this
  max_ram_mb: 8192

check_interval: 60      # short, only while you are testing
scale_cooldown: 120     # short, only while you are testing

proxmox_hosts:
  - name: pve1
    host: 192.168.1.10
    ssh_user: root
    ssh_key: /root/.ssh/id_rsa
    ssh_port: 22

virtual_machines:
  - vm_id: 101
    proxmox_host: pve1
    scaling_enabled: true
    cpu_scaling: true
    ram_scaling: true

host_limits:
  max_host_cpu_percent: 90
  max_host_ram_percent: 90

auto_configure_hotplug: false   # you did it by hand in step 2

logging:
  level: DEBUG                  # while testing
  log_file: /var/log/vm_autoscale.log
```

::: warning `ssh_key` must be an RSA key
Key authentication currently loads the file as an RSA key specifically. An Ed25519 or ECDSA key will fail to load and the connection will fall back to failing. See [known limitations](/reference/limitations#only-rsa-ssh-keys-are-supported). Use a password, or generate an RSA key, until that is fixed.
:::

`proxmox_host` in each VM entry must match a `name` in `proxmox_hosts` exactly — a mismatch means the VM is silently never processed.

## Step 4 — run it in the foreground first

Do not start the systemd unit yet. Run it by hand so you can see everything:

```bash
sudo systemctl stop vm_autoscale.service
sudo python3 /usr/local/bin/vm_autoscale/autoscale.py
```

Within one `check_interval` you should see lines like:

```
[INFO] Host CPU Usage: 12.40%, Host RAM Usage: 61.20%
[INFO] VM 101 is running.
[INFO] VM 101 current usage - CPU: 3.2%, RAM: 41.5%
[INFO] No CPU scaling required.
```

If instead you see `Error processing VM 101 on host pve1: ...`, stop and fix that before going further — [troubleshooting](/guide/troubleshooting) covers the usual causes.

## Step 5 — force a scaling event

The quickest honest test is to move the threshold rather than manufacture load. Temporarily set:

```yaml
scaling_thresholds:
  cpu:
    high: 1     # anything above 1% triggers a scale up
    low: 0
```

Restart the foreground process. You should get:

```
[INFO] Scaled up vCPUs to 3 for VM 101 (hotplug applied).
```

Confirm on the node that it actually happened — the log line reports the command was sent, not that the guest accepted it:

```bash
qm config 101 | grep -E 'cores|vcpus'
```

And inside the guest:

```bash
nproc
lscpu | grep '^CPU(s):'
```

::: tip Generating real load instead
If you would rather test with genuine pressure, `stress-ng --cpu $(nproc) --timeout 300s` inside the guest works. Remember the service samples once per `check_interval` — the load has to still be running when the poll happens.
:::

Now restore your real thresholds.

## Step 6 — watch it come back down

Leave the guest idle for longer than `scale_cooldown` and you should see the reverse:

```
[INFO] Scaled down vCPUs to 2 for VM 101 (hotplug applied).
```

If it scales up but never down, the usual cause is that idle CPU sits above your `low` threshold — a guest doing nothing is rarely at 0%. Widen the dead band.

## Step 7 — hand it to systemd

```bash
sudo nano /usr/local/bin/vm_autoscale/config.yaml   # restore check_interval: 300, level: INFO
sudo systemctl start vm_autoscale.service
sudo systemctl status vm_autoscale.service
journalctl -u vm_autoscale.service -f
```

## Step 8 — add the rest

Add hosts and VMs one at a time, checking the log after each. Things to keep in mind as the list grows:

- Hosts are processed **sequentially**, and each VM opens and closes its own SSH connection. With many VMs a cycle can take longer than `check_interval`, at which point cycles simply run back-to-back.
- Set `scaling_enabled: false` rather than deleting an entry when you want to park a VM — it documents the intent.
- Turn on [notifications](/guide/notifications) once you stop watching the log.
