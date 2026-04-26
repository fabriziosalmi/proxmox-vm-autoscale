# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
