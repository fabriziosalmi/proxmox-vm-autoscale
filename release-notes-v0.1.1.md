## What's changed

### Bug fix — Host RAM usage now matches Proxmox WebUI (#38)

RAM usage reported by the host resource checker was based on `free + cached` memory, which ignored reclaimable buff/cache and caused the host to appear near its limit even when plenty of memory was actually available. The calculation now reads the `used` field directly from the `pvesh` node status, consistent with what the Proxmox WebUI displays.

**Before:** host with 42 GiB used / 64 GiB total could report ~90% usage → scaling suppressed  
**After:** correctly reports ~66% → scaling proceeds normally

### New tests (94 total, all green)

- `tests/test_host_resource_checker.py` — `HostResourceChecker` coverage: RAM fix regression test, threshold boundary cases, bytes output, JSON errors, missing fields
- `tests/test_autoscale.py` — `NotificationManager` (config validation, message formatting, Gotify/SMTP routing with fallback), `VMAutoscaler` config loading, CPU/RAM scaling decision logic, `VMResourceManager` scaling helpers and cooldown

**Full changelog:** https://github.com/fabriziosalmi/proxmox-vm-autoscale/blob/main/CHANGELOG.md
