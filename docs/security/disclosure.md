---
title: Reporting a vulnerability
description: How to responsibly disclose a security vulnerability in Proxmox VM Autoscale, what to include, and what response to expect.
---

# Reporting a vulnerability

## Do not open a public issue

Report privately by email:

**fabrizio.salmi@gmail.com**

Public disclosure before a fix exists puts every deployment at risk — and these deployments hold root credentials for hypervisors.

## What to include

- **What it is** — the class of issue and the component affected
- **How to reproduce** — steps, a proof of concept, or the code path
- **Impact** — what an attacker gains, and from what starting position
- **Version** — release tag or commit hash
- **Suggested fix**, if you have one
- **How you would like to be credited**, or that you prefer to stay anonymous

## What to expect

| Stage | Timeline |
|---|---|
| Acknowledgement | Within 48 hours |
| Initial assessment and a plan | Within 7 days |
| Fix | Depends on severity and complexity |
| Advisory and release | Together with the fix |

You will be kept informed as the fix progresses, credited in the advisory unless you prefer otherwise, and told when it is safe to publish.

## Scope

**In scope** — anything in this repository: the Python modules, `install.sh`, the systemd unit, the shipped configuration and the documentation where it recommends something unsafe.

**Out of scope**

- Vulnerabilities in Proxmox VE itself → [Proxmox security](https://pve.proxmox.com/wiki/Security)
- Vulnerabilities in `paramiko`, `PyYAML` or `requests` → report upstream, though do tell us if this project's usage makes one exploitable
- Issues requiring root on the autoscaler host — root there is [already total compromise](/security/#root-on-the-autoscaler-host) by design
- Weaknesses already documented on the [known limitations](/reference/limitations) page — they are known; a *new* exploitation path for one of them is very much in scope

## Already-known weaknesses

Please check these before reporting, so your effort goes somewhere useful:

- SSH host keys are auto-accepted with no pinning
- Credentials are stored in plain text in `config.yaml`
- The service requires and runs as root
- `install.sh` is fetched over HTTPS and executed unverified
- VMIDs are interpolated into shell command strings without validation

All are documented in the [threat model](/security/).

## Supported versions

| Version | Supported |
|---|---|
| `main` | ✅ |
| Latest release (`v1.4.0`) | ✅ |
| Older tags | ⚠️ Alpha — upgrade |

Note that release tags before `v1.3.0` are not in chronological order: `v1.2.0` was published before `v0.1.1`. Numbering is monotonic from `v1.3.0` onwards.

## machine-readable

A [`security.txt`](/proxmox-vm-autoscale/.well-known/security.txt) is published under this site, per [RFC 9116](https://www.rfc-editor.org/rfc/rfc9116).

::: info About its location
RFC 9116 places `security.txt` at the root of a domain. This site lives under a path on `github.io`, so the file is served from the project path rather than the canonical one. It is provided for completeness; the email address above is the authoritative contact.
:::

## Security updates

When an issue is fixed:

1. It is developed and tested privately.
2. A GitHub security advisory is published.
3. A release is tagged with the fix.
4. Release notes describe the issue and the required action.

[Watch the repository](https://github.com/fabriziosalmi/proxmox-vm-autoscale) → Custom → Releases to be notified.
