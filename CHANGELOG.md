# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
  issuing two extra `qm config` calls per VM per cycle.

### Security
- **`install.sh` no longer leaves `config.yaml` world-readable.** A recursive
  `chmod 755` over the install directory applied to the config file too, which
  stores the Proxmox root SSH password and SMTP credentials in plain text. The
  config and its backup are now `chown root:root` + mode `600`, and the backup
  directory is mode `700` — matching what `SECURITY.md` already prescribed.

### Added
- `tests/test_scaling_limits_and_cooldown.py`: 20 regression tests covering
  limit resolution (including a test that the limits shipped in `config.yaml`
  are the ones enforced), independent per-resource cooldowns, and manager
  reuse across cycles.

## [0.1.1] - 2026-04-27

### Fixed
- **Host RAM usage calculation now matches Proxmox WebUI** ([#38](https://github.com/fabriziosalmi/proxmox-vm-autoscale/issues/38)): RAM usage is computed from the `used` field reported by `pvesh`, which excludes reclaimable buff/cache. Previously, `free + cached` was used as the available memory estimate, leading to incorrectly high RAM usage percentages (e.g. ~90% instead of ~66%) and suppressed scaling on hosts with heavy caching.

### Added
- `tests/test_host_resource_checker.py`: full test coverage for `HostResourceChecker`, including the RAM calculation fix, threshold boundary cases, bytes output handling, and error paths.
- `tests/test_autoscale.py`: tests for `NotificationManager` (config validation, message formatting, routing with Gotify/SMTP fallback), `VMAutoscaler` config loading, `_handle_cpu_scaling` / `_handle_ram_scaling` decision logic, and `VMResourceManager` scaling helpers (`scale_cpu`, `scale_ram`, `can_scale`, `_parse_cpu_usage`, `_parse_ram_usage`).

### Changed
- Removed unused `cached_mem` and `free_mem` variables from `HostResourceChecker.check_host_resources`.

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

[Unreleased]: https://github.com/fabriziosalmi/proxmox-vm-autoscale/compare/v0.1.1...HEAD
[0.1.1]: https://github.com/fabriziosalmi/proxmox-vm-autoscale/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/fabriziosalmi/proxmox-vm-autoscale/releases/tag/v0.1.0
