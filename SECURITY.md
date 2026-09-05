# Security Policy

## Reporting a vulnerability

**Do not open a public GitHub issue.** This project holds credentials for `root`
on hypervisors; a public report puts every deployment at risk before a fix
exists.

Email **fabrizio.salmi@gmail.com** with:

- What the issue is and which component it affects
- Steps to reproduce, a proof of concept, or the code path
- What an attacker gains, and from what starting position
- The release tag or commit hash
- A suggested fix, if you have one
- How you would like to be credited, or that you prefer to remain anonymous

### What to expect

| Stage | Timeline |
|---|---|
| Acknowledgement | Within 48 hours |
| Assessment and a plan | Within 7 days |
| Fix | Depends on severity and complexity |
| Advisory and release | Published together with the fix |

You will be kept informed, credited in the advisory unless you prefer otherwise,
and told when it is safe to publish.

## Supported versions

| Version | Supported |
|---|---|
| `main` | ✅ |
| Latest release | ✅ |
| Older tags | ⚠️ Upgrade — this project is pre-1.0 in maturity |

> Release tags are not in chronological order: `v1.2.0` was published before
> `v0.1.1`. Go by the [releases page](https://github.com/fabriziosalmi/proxmox-vm-autoscale/releases),
> not by the highest number.

## Scope

**In scope** — everything in this repository: the Python modules, `install.sh`,
the systemd unit, the shipped configuration, and the documentation where it
recommends something unsafe.

**Out of scope**

- Vulnerabilities in Proxmox VE itself → [Proxmox security](https://pve.proxmox.com/wiki/Security)
- Vulnerabilities in `paramiko`, `PyYAML` or `requests` → report upstream, though
  do tell us if this project's usage makes one exploitable
- Anything requiring root on the host running the service — root there is total
  compromise by design, since the credentials are readable and the code is
  modifiable
- The already-known weaknesses below. A **new exploitation path** for one of them
  is very much in scope

## Known weaknesses

These are documented, not hidden. Please check them before reporting.

| Weakness | Status |
|---|---|
| SSH host keys are auto-accepted, with no pinning | Design-level; mitigate by network segmentation |
| Credentials stored in plain text in `config.yaml` | No secrets backend; mitigate with mode `600` and disk encryption |
| The service requires and runs as `root` | Every action is a `qm` command; no least-privilege mode exists |
| `install.sh` is fetched over HTTPS and run unverified | Read it first, or install manually |
| VMIDs are interpolated into shell command strings | Config is administrator-controlled, so not remotely exploitable |
| Only RSA SSH keys load | Ed25519 and ECDSA keys fail to load |

Full analysis, including attacker positions and what is *not* a risk:
**[Threat model](https://fabriziosalmi.github.io/proxmox-vm-autoscale/security/)**.

## Hardening

The essentials:

1. `config.yaml` must be root-owned and mode `600` — verify after every install
2. Use a dedicated SSH key, not a reused admin key, and remove `ssh_password`
3. Restrict the key in `authorized_keys` with `from="<autoscaler address>"`
4. Keep management SSH on a segmented network
5. Apply a systemd hardening drop-in
6. Rotate credentials if the config was ever readable by a non-root user

Step by step, with the drop-in and the firewall rules:
**[Hardening guide](https://fabriziosalmi.github.io/proxmox-vm-autoscale/security/hardening.html)**.

## Dependencies

Three direct dependencies: `paramiko`, `PyYAML`, `requests`. Dependabot watches
pip, npm and GitHub Actions weekly.

```bash
pip3 install pip-audit && pip-audit -r requirements.txt
```

## Security update process

1. The fix is developed and tested privately
2. A GitHub security advisory is published
3. A release is tagged with the fix
4. Release notes describe the issue and the action required

A machine-readable [`security.txt`](https://fabriziosalmi.github.io/proxmox-vm-autoscale/.well-known/security.txt)
is published alongside the documentation, per RFC 9116.
