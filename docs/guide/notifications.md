---
title: Notifications
description: Configure Gotify push and SMTP email notifications for Proxmox VM Autoscale, including when they fire, what priority each event carries, and how failures are handled.
---

# Notifications

Two independent channels: **Gotify** push and **SMTP** email. Enable either, both, or neither. Configuration is validated at startup, so a broken notification setup stops the service immediately rather than failing quietly the first time something interesting happens.

## When notifications fire

| Event | Priority | Sent |
|---|---|---|
| CPU or RAM scaled **up** | 7 | Only when the resource actually changed |
| CPU or RAM scaled **down** | 5 | Only when the resource actually changed |
| Error processing a VM | 9 | Every occurrence |
| Unexpected error in the main loop | 10 | Every occurrence, then 60 s pause |

Nothing is sent for a routine poll, and nothing is sent when a threshold is crossed but no change results — hitting `max_cores`, or being inside a cooldown, is silent. Priority is a Gotify concept; the SMTP channel ignores it.

::: tip Error notifications can be a firehose
A node that has become unreachable produces a priority-9 notification **per VM, per cycle**. Twenty VMs on a 300-second interval is 240 notifications an hour. There is no rate limiting or deduplication. Consider Gotify-side filtering if this matters to you.
:::

## Gotify

```yaml
gotify:
  enabled: true
  server_url: https://gotify.example.com
  app_token: AbCdEf123456789
  priority: 5
```

| Key | Required | Notes |
|---|---|---|
| `enabled` | yes | `false` disables the channel entirely |
| `server_url` | when enabled | Base URL. A trailing slash is stripped for you |
| `app_token` | when enabled | **Application** token, not a client token |
| `priority` | no | Default priority; per-event values above override it |

Messages `POST` to `{server_url}/message` with a 10-second timeout, `Authorization: Bearer <app_token>`, and the title `VM Autoscale Alert`.

If `enabled: true` and either `server_url` or `app_token` is missing, startup fails with:

```
ConfigurationError: Gotify is enabled but configuration is incomplete
```

### Getting a token

In Gotify: **Apps → Create Application**, then copy the token shown. Verify it out of band before wiring it in:

```bash
curl -X POST "https://gotify.example.com/message" \
  -H "Authorization: Bearer AbCdEf123456789" \
  -F "title=test" -F "message=hello" -F "priority=5"
```

## Email (SMTP)

```yaml
alerts:
  email_enabled: true
  email_recipient: ops@example.com
  smtp_server: smtp.example.com
  smtp_port: 587
  smtp_user: autoscale@example.com
  smtp_password: your_smtp_password
```

| Key | Required | Notes |
|---|---|---|
| `email_enabled` | yes | |
| `smtp_server` | when enabled | Validated at startup |
| `smtp_user` | when enabled | Also used as the `From` address |
| `email_recipient` | when enabled | A string, or a list of strings for several recipients |
| `smtp_port` | no | Defaults to `587` |
| `smtp_password` | no | **Leave empty to skip `login()`** — for relays that authenticate by IP |

The service always calls `starttls()`, then authenticates only if `smtp_password` is non-empty.

### Subject lines

The VMID is extracted from the message body with the pattern `VM\s+(\d+)`, producing:

```
Subject: VM Autoscale Alert for VM 101
```

Messages with no VMID in them — most main-loop errors — get `VM Autoscale Alert for VM ` with a trailing space. Cosmetic, but it makes subject-based mail filters unreliable for those.

### Several recipients

```yaml
alerts:
  email_recipient:
    - ops@example.com
    - oncall@example.com
```

### Local relay

If the host already has a working MTA:

```yaml
alerts:
  email_enabled: true
  smtp_server: localhost
  smtp_port: 25
  smtp_user: vm-autoscale@$(hostname -f)
  smtp_password: ""
  email_recipient: root@example.com
```

Note `starttls()` is called unconditionally, so a relay on port 25 must advertise STARTTLS. A plain-text-only local relay will fail.

## Failure handling

Channels are attempted independently and a failure in one does not block the other. Failures are logged, not retried, and never crash the service:

```
[ERROR] Failed to send Gotify notification: HTTPSConnectionPool(...): Read timed out.
```

If every configured channel fails, the message itself is written to the log so it is not lost:

```
[WARNING] Failed to send notification through any channel.
          Message: Scaled up CPU for VM 101 due to high usage (91.2%).
          Errors: Failed to send Gotify notification: ...
```

With no channel enabled at all, you get one warning at startup and then silence:

```
[WARNING] No notification method is enabled in configuration
```

## Verifying without waiting for load

Temporarily set an impossible threshold, run the service in the foreground for one cycle, and put it back:

```yaml
scaling_thresholds:
  cpu:
    high: 1
    low: 0
```

```bash
sudo systemctl stop vm_autoscale.service
sudo python3 /usr/local/bin/vm_autoscale/autoscale.py
```

## Security notes

`smtp_password` and `app_token` sit in plain text in `config.yaml`. That file must be mode `600` and root-owned — the installer does this, and the [hardening guide](/security/hardening) covers verifying it. Notification bodies contain VMIDs, host names and usage figures; treat your Gotify server and mail path as carrying operational detail about your infrastructure.
