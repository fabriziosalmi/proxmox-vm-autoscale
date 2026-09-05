# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.3.0] - 2026-09-05

> **Upgrade note.** Limits you set in `scaling_limits` were previously ignored;
> they now take effect. Re-read that section before upgrading, particularly if
> you configured a `max_cores` above 8 or a `min_ram_mb` above 512 and have been
> running against the hardcoded defaults without realising.
>
> This release also **drops Python 3.9**. See *Removed* below.

### Fixed
- **`scaling_limits` from `config.yaml` is now actually enforced.** The config
  validator required the section, but `VMResourceManager` looked the values up
  as flat top-level keys under names that did not exist (`max_ram`/`min_ram`
  instead of `max_ram_mb`/`min_ram_mb`). Every installation silently ran on the
  hardcoded defaults 1–8 cores and 512–16384 MB, so the documented
  `min_ram_mb: 1024` (needed because NUMA misbehaves below 1 GB) had no effect.
  Flat top-level keys are still accepted as a fallback for older configs.
- **CPU and RAM now have independent cooldowns** ([#30](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/30)):
  a single shared timestamp meant any CPU threshold breach suppressed RAM
  scaling for the same cycle. The cooldown is also consumed only when a scaling
  command is actually issued — previously the mere *check* consumed it, so a
  VM already at its limit was rate-limited for doing nothing.
- **`scale_cooldown` now applies between polling cycles.** `VMResourceManager`
  was rebuilt on every iteration of the main loop, resetting the cooldown to
  zero; the effective interval was `check_interval`, not `scale_cooldown`.
  Managers are now cached per VM and rebound to each cycle's SSH connection.
  As a side effect, hotplug auto-configuration runs once per VM instead of
  issuing two extra `qm config` calls per VM per cycle — and is retried on the
  next cycle if it failed, so a transient SSH error during the first cycle no
  longer leaves a VM permanently non-live-scalable.
- `can_scale()` and `_mark_scaled()` reject unknown resource names instead of
  silently treating them as never rate-limited.

### Security
- **`install.sh` no longer leaves `config.yaml` world-readable.** A recursive
  `chmod 755` over the install directory applied to the config file too, which
  stores the Proxmox root SSH password and SMTP credentials in plain text. The
  config and its backup are now `chown root:root` + mode `600`, and the backup
  directory is mode `700` — matching what `SECURITY.md` already prescribed.

### Added
- **Documentation site** at <https://fabriziosalmi.github.io/proxmox-vm-autoscale/> —
  guide, configuration reference, architecture, module API, a full catalogue of
  known limitations, threat model, hardening guide and privacy statement. Built
  with VitePress and deployed by GitHub Actions, with `sitemap.xml`, `llms.txt`,
  `.well-known/security.txt` and JSON-LD structured data.
- `tests/test_scaling_limits_and_cooldown.py`: 24 regression tests covering
  limit resolution (including a test that the limits shipped in `config.yaml`
  are the ones enforced), independent per-resource cooldowns, resource-name
  validation, and manager reuse and hotplug retry across cycles.

### Changed
- `install.sh` now installs the `vm_autoscale.service` tracked in the
  repository instead of generating its own copy inline. The two had drifted:
  the generated unit had no `RestartSec`, so a crash-looping service restarted
  as fast as systemd allowed, and the tracked file was never actually used.
- `install.sh` no longer aborts when pip cannot write to the system
  interpreter. On Proxmox VE 8 and Debian 12+ that is PEP 668's
  `externally-managed-environment`, and treating it as fatal failed an
  installation that was otherwise complete — the apt step already provides
  `paramiko`, `PyYAML` and `requests`.
- Dependency floors raised to `paramiko>=5.0.0`, `PyYAML>=6.0.3` and
  `requests>=2.34.2`.
- GitHub Actions bumped to current majors. Dependabot now watches the
  `github-actions` and `npm` ecosystems as well as `pip`.
- README trimmed from 343 lines to 131, `ARCHITECTURE.md` from 290 to 91 and
  `SECURITY.md` from 122 to 106, with the long-form material moved to the
  documentation site rather than maintained in two places and drifting apart.

### Removed
- **Python 3.9 support.** `requests` 2.34.2 requires Python >= 3.10. Python 3.9
  reached end of life in October 2025, and the only Proxmox release shipping it
  is VE 7, end of life since July 2024. The CI matrix is now 3.10–3.12.
- `release-notes-v0.1.1.md` from the repository root — it duplicated the GitHub
  release verbatim.

## [0.1.1] - 2026-04-27

### Fixed
- **Host RAM usage calculation now matches Proxmox WebUI** ([#38](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/38)): RAM usage is computed from the `used` field reported by `pvesh`, which excludes reclaimable buff/cache. Previously, `free + cached` was used as the available memory estimate, leading to incorrectly high RAM usage percentages (e.g. ~90% instead of ~66%) and suppressed scaling on hosts with heavy caching.

### Added
- `tests/test_host_resource_checker.py`: full test coverage for `HostResourceChecker`, including the RAM calculation fix, threshold boundary cases, bytes output handling, and error paths.
- `tests/test_autoscale.py`: tests for `NotificationManager` (config validation, message formatting, routing with Gotify/SMTP fallback), `VMAutoscaler` config loading, `_handle_cpu_scaling` / `_handle_ram_scaling` decision logic, and `VMResourceManager` scaling helpers (`scale_cpu`, `scale_ram`, `can_scale`, `_parse_cpu_usage`, `_parse_ram_usage`).

### Changed
- Removed unused `cached_mem` and `free_mem` variables from `HostResourceChecker.check_host_resources`.

---

## [1.2.0] - 2025-12-09

> Documented retroactively. This tag was published **before** `0.1.1` despite
> the higher number — the two release lines were never reconciled. Entries in
> this file are chronological, so `1.2.0` appears below `0.1.1`. From `1.3.0`
> onwards the numbering is monotonic again.

### Added
- **Hotplug support** ([#37](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/37)):
  `auto_configure_hotplug` enables hotplug and NUMA on managed VMs; RAM changes
  use `balloon` and CPU changes use `vcpus` so they apply to a running guest,
  with a fallback when hotplug is unavailable. `cores` changes and NUMA itself
  still require a guest reboot.
- **Billing tracking** ([#33](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/33)):
  `billing_tracker.py`, recording CPU/RAM spec changes with timestamps, plus
  period cost calculation, CSV export and webhook support.
- 30 unit tests covering hotplug and billing.

### Known issues in this release
Recorded here because the original release notes overstate what shipped:

- Only spec recording is wired into the service. `generate_period_report`,
  `export_csv`, `run_webhook`, `record_vm_state_change` and `set_vm_name` are
  public API that nothing calls, so no CSV is produced and no uptime is tracked
  unless you invoke them yourself.
- The cost calculation accepts uptime records and ignores them, so downtime is
  billed as uptime.
- `auto_configure_hotplug` ran on every polling cycle, issuing two extra
  `qm config` calls per VM per cycle. Fixed in 1.3.0.

---

## [0.1.0-docs] - Unreleased documentation pass

### Added
- Comprehensive documentation improvements across all markdown files
- `requirements.txt` for Python dependency management
- `ARCHITECTURE.md` with detailed system architecture documentation
- Troubleshooting section in README with common issues and solutions
- Table of contents in README for better navigation
- Enhanced configuration examples with inline comments
- Development setup instructions in CONTRIBUTING.md
- Comprehensive security policy in SECURITY.md
- This CHANGELOG file to track project changes

### Changed
- Enhanced README.md with improved structure and clarity
- Updated CONTRIBUTING.md with detailed contribution guidelines
- Expanded SECURITY.md with security best practices and reporting process
- Improved configuration examples with better annotations
- Updated prerequisites section with specific version requirements

### Fixed
- Typo in config.yaml comment: "doeasnt" → "doesn't"
- Typo in README: "togheter" → "together"
- Improved formatting and consistency across documentation
- Clarified Python version requirement (3.6+)

## [0.1.0] - Initial Release

### Added
- Initial release of Proxmox VM Autoscale
- Automatic CPU and RAM scaling for Proxmox VMs
- Multi-host support via SSH
- Gotify notification support
- Email notification support
- Systemd service integration
- Configuration via YAML file
- Comprehensive logging
- Host resource safety checks
- Scaling cooldown periods

[Unreleased]: https://github.com/fabriziosalmi/proxmox-vm-autoscale/compare/v1.3.0...HEAD
[1.3.0]: https://github.com/fabriziosalmi/proxmox-vm-autoscale/compare/v0.1.1...v1.3.0
[0.1.1]: https://github.com/fabriziosalmi/proxmox-vm-autoscale/compare/v1.2.0...v0.1.1
[1.2.0]: https://github.com/fabriziosalmi/proxmox-vm-autoscale/compare/v0.1.0...v1.2.0
[0.1.0]: https://github.com/fabriziosalmi/proxmox-vm-autoscale/releases/tag/v0.1.0
