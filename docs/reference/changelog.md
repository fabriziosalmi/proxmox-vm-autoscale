---
title: Changelog
description: Release history for Proxmox VM Autoscale, including behaviour changes that affect existing installations.
---

# Changelog

Maintained in [`CHANGELOG.md`](https://github.com/fabriziosalmi/proxmox-vm-autoscale/blob/main/CHANGELOG.md), following [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

::: warning Tags are not in chronological order
`v1.2.0` was published in December 2025 and `v0.1.1` in April 2026 — and **`v0.1.1` is the current release**. The version numbers do not reflect ordering. Go by [the releases page](https://github.com/fabriziosalmi/proxmox-vm-autoscale/releases) rather than by the highest number.
:::

## Unreleased

### Fixed

- **`scaling_limits` from `config.yaml` is now actually enforced.** The validator required the section, but the code looked its values up as flat top-level keys under names that do not exist (`max_ram`/`min_ram` instead of `max_ram_mb`/`min_ram_mb`). Every installation silently ran on the hardcoded defaults 1–8 cores and 512–16384 MB, so the documented `min_ram_mb: 1024` — needed because NUMA misbehaves below 1 GB — had no effect. Flat keys remain accepted as a fallback for older configs.

  ::: danger Upgrade note
  Your configured limits are about to take effect for the first time. Re-read `scaling_limits` before upgrading, particularly if you set a `max_cores` above 8 or a `min_ram_mb` above 512 and have been running against the defaults without realising.
  :::

- **CPU and RAM now have independent cooldowns** ([#30](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/30)). A single shared timestamp meant any CPU threshold breach suppressed RAM scaling for the same cycle. The cooldown is also consumed only when a scaling command is actually issued — previously the *check* consumed it, so a VM already at its limit was rate-limited for doing nothing.

- **`scale_cooldown` now applies between polling cycles.** `VMResourceManager` was rebuilt on every iteration of the main loop, resetting the cooldown to zero; the effective interval was `check_interval`, not `scale_cooldown`. Managers are now cached per VM and rebound to each cycle's SSH connection. Side effect: hotplug auto-configuration runs once per VM instead of two extra `qm config` calls per VM per cycle.

### Security

- **`install.sh` no longer leaves `config.yaml` world-readable.** A recursive `chmod 755` over the install directory applied to the config file too — the file holding the Proxmox root SSH password and SMTP credentials in plain text. The config and its backup are now root-owned mode `600`, and the backup directory mode `700`, matching what `SECURITY.md` already prescribed.

### Added

- `tests/test_scaling_limits_and_cooldown.py`: 20 regression tests covering limit resolution (including one asserting the limits shipped in `config.yaml` are the ones enforced), independent per-resource cooldowns, and manager reuse across cycles.
- This documentation site.

## [0.1.1] — 2026-04-27

### Fixed

- **Host RAM usage now matches the Proxmox web UI** ([#38](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/38)). RAM was computed from `free + cached`, ignoring reclaimable buff/cache, so a host with a warm page cache reported ~90% usage when the real figure was ~66% — and all scaling on it was suppressed. It now reads the `used` field from `pvesh` directly.

### Added

- `tests/test_host_resource_checker.py` — full coverage of `HostResourceChecker`: the RAM fix, threshold boundaries, byte-valued output, error paths.
- `tests/test_autoscale.py` — `NotificationManager` validation, formatting and routing; `VMAutoscaler` config loading; CPU/RAM decision logic; `VMResourceManager` scaling helpers.

### Changed

- Removed unused `cached_mem` and `free_mem` variables from `HostResourceChecker`.

## [1.2.0] — 2025-12-09

Published out of order; predates 0.1.1. Added the hotplug fix and the billing tracker. Not covered by `CHANGELOG.md`.

## [0.1.0-docs] — unreleased documentation pass

Comprehensive documentation improvements: `requirements.txt`, `ARCHITECTURE.md`, a troubleshooting section, a table of contents, expanded `SECURITY.md` and `CONTRIBUTING.md`, and the changelog itself.

## [0.1.0] — initial release

CPU and RAM autoscaling, multi-host SSH support, Gotify and SMTP notifications, systemd integration, YAML configuration, host resource safety checks, scaling cooldowns.
