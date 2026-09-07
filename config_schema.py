"""
Configuration validation for VM Autoscale.

Every configuration key used to be read with an inline `.get()` and its own
inline default, scattered across three modules. Nothing checked types, ranges,
unknown keys or referential integrity, so a key that was written, documented
and never read produced no error anywhere - not at load, not at use, not in the
log. That single absence is what produced most of the defects fixed between
1.3.0 and 1.6.0: `scaling_limits` read under a name that did not exist, per-VM
`thresholds` never consulted, `ssh_port` indexed without a default on a key the
shipped example omitted.

This module is the contract those defects were missing. It runs once at
startup, reports *every* problem it finds rather than the first, and refuses to
start on error - because a silently inert setting is worse than a service that
will not boot.

An unknown key is a warning, not an error: rejecting them outright would break
every installation carrying a comment-shaped typo or a key from a newer
version. But it is reported, by path, so it stops being invisible.
"""

import re
from typing import Any

VMID_PATTERN = re.compile(r"^[0-9]{1,10}$")

HOST_KEY_POLICIES = ("accept-new", "strict", "auto")

#: Top-level keys the service understands. Anything else is reported.
KNOWN_TOP_LEVEL = {
    "scaling_thresholds", "scaling_limits", "proxmox_hosts", "virtual_machines",
    "host_limits", "check_interval", "scale_cooldown", "auto_configure_hotplug",
    "dry_run", "metrics", "ssh_host_key_policy", "ssh_known_hosts",
    "scale_down_after_cycles", "notification_dedup_seconds",
    "logging", "gotify", "alerts", "billing",
    # Legacy flat limit keys, still honoured for older configs.
    "min_cores", "max_cores", "min_ram", "max_ram",
}

KNOWN_HOST_KEYS = {"name", "host", "ssh_user", "ssh_password", "ssh_key", "ssh_port"}
KNOWN_VM_KEYS = {
    "vm_id", "proxmox_host", "scaling_enabled", "cpu_scaling", "ram_scaling",
    "thresholds", "scaling_limits",
}
KNOWN_LIMIT_KEYS = {"min_cores", "max_cores", "min_ram_mb", "max_ram_mb"}
KNOWN_THRESHOLD_KEYS = {
    "cpu", "ram", "cpu_high", "cpu_low", "ram_high", "ram_low",
}


class ConfigurationInvalid(Exception):
    """The configuration is structurally unusable.

    Carries every problem found, not just the first, so one restart is enough
    to see all of them.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        joined = "\n".join(f"  - {e}" for e in errors)
        super().__init__(
            f"{len(errors)} configuration problem(s) found:\n{joined}"
        )


class _Validator:
    """Collects problems instead of raising on the first one."""

    def __init__(self, config: dict[str, Any]):
        self.config = config
        self.errors: list[str] = []
        self.warnings: list[str] = []

    # -- primitives ---------------------------------------------------------

    def error(self, path: str, message: str) -> None:
        self.errors.append(f"{path}: {message}")

    def warn(self, path: str, message: str) -> None:
        self.warnings.append(f"{path}: {message}")

    def _number(self, value: Any, path: str, low: float, high: float,
                integer: bool = False) -> None:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            self.error(path, f"expected a number, got {type(value).__name__}")
            return
        if integer and not float(value).is_integer():
            self.error(path, f"expected a whole number, got {value}")
            return
        if not (low <= value <= high):
            self.error(path, f"expected a value between {low} and {high}, got {value}")

    def _bool(self, value: Any, path: str) -> None:
        if not isinstance(value, bool):
            self.error(path, f"expected true or false, got {value!r}")

    def _unknown_keys(self, mapping: dict[str, Any], known: set, path: str) -> None:
        for key in mapping:
            if key not in known:
                self.warn(
                    f"{path}.{key}",
                    "unknown key; it is not read by anything and will have no effect",
                )

    # -- sections -----------------------------------------------------------

    def check_thresholds(self, thresholds: Any, path: str,
                         required: bool = True) -> None:
        if not isinstance(thresholds, dict):
            self.error(path, f"expected a mapping, got {type(thresholds).__name__}")
            return

        for resource in ("cpu", "ram"):
            block = thresholds.get(resource)
            if block is None:
                if required:
                    self.error(f"{path}.{resource}", "missing")
                continue
            if not isinstance(block, dict):
                self.error(f"{path}.{resource}",
                           f"expected a mapping, got {type(block).__name__}")
                continue

            for bound in ("high", "low"):
                value = block.get(bound)
                if value is None:
                    if required:
                        self.error(f"{path}.{resource}.{bound}", "missing")
                    continue
                self._number(value, f"{path}.{resource}.{bound}", 0, 100)

            high, low = block.get("high"), block.get("low")
            if isinstance(high, (int, float)) and isinstance(low, (int, float)):
                if low >= high:
                    self.error(
                        f"{path}.{resource}",
                        f"low ({low}) must be below high ({high}); as written "
                        "the VM would scale up and down on the same reading",
                    )
                elif high - low < 10:
                    self.warn(
                        f"{path}.{resource}",
                        f"the dead band is only {high - low} points wide "
                        "({low}-{high}); expect the VM to flap".format(
                            low=low, high=high),
                    )

    def check_limits(self, limits: Any, path: str, required: bool = True) -> None:
        if not isinstance(limits, dict):
            self.error(path, f"expected a mapping, got {type(limits).__name__}")
            return

        self._unknown_keys(limits, KNOWN_LIMIT_KEYS, path)

        for key, low, high in (("min_cores", 1, 512), ("max_cores", 1, 512),
                               ("min_ram_mb", 16, 4194304), ("max_ram_mb", 16, 4194304)):
            value = limits.get(key)
            if value is None:
                if required and key in ("min_cores", "max_cores",
                                        "min_ram_mb", "max_ram_mb"):
                    self.error(f"{path}.{key}", "missing")
                continue
            self._number(value, f"{path}.{key}", low, high, integer=True)

        for lo_key, hi_key in (("min_cores", "max_cores"),
                               ("min_ram_mb", "max_ram_mb")):
            lo, hi = limits.get(lo_key), limits.get(hi_key)
            if isinstance(lo, (int, float)) and isinstance(hi, (int, float)) and lo > hi:
                self.error(path, f"{lo_key} ({lo}) is above {hi_key} ({hi})")

        min_ram = limits.get("min_ram_mb")
        if isinstance(min_ram, (int, float)) and min_ram < 1024:
            self.warn(
                f"{path}.min_ram_mb",
                f"{min_ram} MB is below 1024; NUMA-enabled guests misbehave "
                "below 1 GB and memory hotplug may fail",
            )

    def check_hosts(self) -> dict[str, int]:
        """Validate `proxmox_hosts` and return the host names it defines."""
        hosts = self.config.get("proxmox_hosts")
        names: dict[str, int] = {}

        if not isinstance(hosts, list) or not hosts:
            self.error("proxmox_hosts", "expected a non-empty list of hosts")
            return names

        for index, host in enumerate(hosts):
            path = f"proxmox_hosts[{index}]"
            if not isinstance(host, dict):
                self.error(path, f"expected a mapping, got {type(host).__name__}")
                continue

            self._unknown_keys(host, KNOWN_HOST_KEYS, path)

            name = host.get("name")
            if not isinstance(name, str) or not name.strip():
                self.error(f"{path}.name", "missing or not a non-empty string")
            elif name in names:
                self.error(f"{path}.name",
                           f"duplicate host name {name!r}, already used at "
                           f"proxmox_hosts[{names[name]}]")
            else:
                names[name] = index

            if not isinstance(host.get("host"), str) or not host.get("host", "").strip():
                self.error(f"{path}.host", "missing or not a non-empty string")

            if not isinstance(host.get("ssh_user"), str) or not host.get("ssh_user", "").strip():
                self.error(f"{path}.ssh_user", "missing or not a non-empty string")

            password = host.get("ssh_password")
            key_path = host.get("ssh_key")
            if not password and not key_path:
                self.error(path, "needs either ssh_password or ssh_key")
            elif password and key_path:
                self.warn(
                    path,
                    "both ssh_password and ssh_key are set; the password wins "
                    "and the key is ignored",
                )

            if "ssh_port" in host:
                self._number(host["ssh_port"], f"{path}.ssh_port", 1, 65535, integer=True)

        return names

    def check_vms(self, host_names: dict[str, int]) -> None:
        vms = self.config.get("virtual_machines")
        if not isinstance(vms, list) or not vms:
            self.error("virtual_machines", "expected a non-empty list of VMs")
            return

        seen: dict[str, int] = {}

        for index, vm in enumerate(vms):
            path = f"virtual_machines[{index}]"
            if not isinstance(vm, dict):
                self.error(path, f"expected a mapping, got {type(vm).__name__}")
                continue

            self._unknown_keys(vm, KNOWN_VM_KEYS, path)

            vm_id = vm.get("vm_id")
            if vm_id is None:
                self.error(f"{path}.vm_id", "missing")
            elif isinstance(vm_id, bool) or not VMID_PATTERN.match(str(vm_id)):
                # This value is interpolated into shell commands run as root.
                self.error(
                    f"{path}.vm_id",
                    f"{vm_id!r} is not a plain numeric VMID; it is interpolated "
                    "into commands run as root on the hypervisor",
                )
            else:
                key = str(vm_id)
                if key in seen:
                    self.error(f"{path}.vm_id",
                               f"duplicate VMID {key}, already declared at "
                               f"virtual_machines[{seen[key]}]")
                else:
                    seen[key] = index

            proxmox_host = vm.get("proxmox_host")
            if not isinstance(proxmox_host, str) or not proxmox_host.strip():
                self.error(f"{path}.proxmox_host", "missing or not a non-empty string")
            elif host_names and proxmox_host not in host_names:
                # Previously this silently skipped the VM forever.
                self.error(
                    f"{path}.proxmox_host",
                    f"{proxmox_host!r} does not match any proxmox_hosts name "
                    f"({', '.join(sorted(host_names)) or 'none defined'})",
                )

            for flag in ("scaling_enabled", "cpu_scaling", "ram_scaling"):
                if flag in vm:
                    self._bool(vm[flag], f"{path}.{flag}")

            if "thresholds" in vm:
                self.check_vm_thresholds(vm["thresholds"], f"{path}.thresholds")

            if "scaling_limits" in vm:
                self.check_limits(vm["scaling_limits"], f"{path}.scaling_limits",
                                  required=False)

    def check_vm_thresholds(self, thresholds: Any, path: str) -> None:
        """Per-VM overrides, in either the flat or the nested shape."""
        if not isinstance(thresholds, dict):
            self.error(path, f"expected a mapping, got {type(thresholds).__name__}")
            return

        self._unknown_keys(thresholds, KNOWN_THRESHOLD_KEYS, path)

        for resource in ("cpu", "ram"):
            nested = thresholds.get(resource)
            if nested is not None and not isinstance(nested, dict):
                self.error(f"{path}.{resource}",
                           f"expected a mapping, got {type(nested).__name__}")
            elif isinstance(nested, dict):
                for bound in ("high", "low"):
                    if nested.get(bound) is not None:
                        self._number(nested[bound], f"{path}.{resource}.{bound}", 0, 100)

            for bound in ("high", "low"):
                flat = f"{resource}_{bound}"
                if thresholds.get(flat) is not None:
                    self._number(thresholds[flat], f"{path}.{flat}", 0, 100)

            high = thresholds.get(f"{resource}_high")
            low = thresholds.get(f"{resource}_low")
            if isinstance(high, (int, float)) and isinstance(low, (int, float)) and low >= high:
                self.error(f"{path}",
                           f"{resource}_low ({low}) must be below "
                           f"{resource}_high ({high})")

    def check_host_limits(self) -> None:
        limits = self.config.get("host_limits")
        if limits is None:
            self.error("host_limits", "missing; it is read for every VM and has no default")
            return
        if not isinstance(limits, dict):
            self.error("host_limits", f"expected a mapping, got {type(limits).__name__}")
            return
        for key in ("max_host_cpu_percent", "max_host_ram_percent"):
            if key not in limits:
                self.error(f"host_limits.{key}", "missing; it has no default")
            else:
                self._number(limits[key], f"host_limits.{key}", 0, 100)

    def check_timing(self) -> None:
        for key, low, high in (("check_interval", 10, 86400),
                               ("scale_cooldown", 0, 86400),
                               ("scale_down_after_cycles", 1, 100),
                               ("notification_dedup_seconds", 0, 86400)):
            if key in self.config:
                self._number(self.config[key], key, low, high, integer=True)

        interval = self.config.get("check_interval", 300)
        cooldown = self.config.get("scale_cooldown", 300)
        if (isinstance(interval, (int, float)) and isinstance(cooldown, (int, float))
                and cooldown < interval):
            self.warn(
                "scale_cooldown",
                f"{cooldown}s is below check_interval ({interval}s); decisions "
                "only happen at poll time, so the effective rate limit is "
                f"{interval}s regardless",
            )

    def check_ssh_policy(self) -> None:
        policy = self.config.get("ssh_host_key_policy", "accept-new")
        if policy not in HOST_KEY_POLICIES:
            self.error("ssh_host_key_policy",
                       f"{policy!r} is not one of {', '.join(HOST_KEY_POLICIES)}")
        elif policy == "auto":
            self.warn("ssh_host_key_policy",
                      "'auto' accepts any host key on every connection and "
                      "records nothing; this offers no protection")

        if "ssh_known_hosts" in self.config and not isinstance(
                self.config["ssh_known_hosts"], str):
            self.error("ssh_known_hosts", "expected a path")

    def check_metrics(self) -> None:
        metrics = self.config.get("metrics")
        if metrics is None:
            return
        if not isinstance(metrics, dict):
            self.error("metrics", f"expected a mapping, got {type(metrics).__name__}")
            return

        self._unknown_keys(metrics, {"enabled", "bind", "port", "path"}, "metrics")

        if "enabled" in metrics:
            self._bool(metrics["enabled"], "metrics.enabled")
        if "port" in metrics:
            self._number(metrics["port"], "metrics.port", 1, 65535, integer=True)
        if "bind" in metrics and not isinstance(metrics["bind"], str):
            self.error("metrics.bind", "expected an address")

        bind = metrics.get("bind", "127.0.0.1")
        if metrics.get("enabled") and bind not in ("127.0.0.1", "::1", "localhost"):
            self.warn(
                "metrics.bind",
                f"the endpoint has no authentication and would be reachable on "
                f"{bind}; the series name your nodes and VMIDs",
            )

    def check_notifications(self) -> None:
        gotify = self.config.get("gotify") or {}
        if isinstance(gotify, dict) and gotify.get("enabled"):
            for key in ("server_url", "app_token"):
                if not gotify.get(key):
                    self.error(f"gotify.{key}", "required when gotify is enabled")
            if "priority" in gotify:
                self._number(gotify["priority"], "gotify.priority", 1, 10, integer=True)

        alerts = self.config.get("alerts") or {}
        if isinstance(alerts, dict) and alerts.get("email_enabled"):
            for key in ("smtp_server", "smtp_user", "email_recipient"):
                if not alerts.get(key):
                    self.error(f"alerts.{key}", "required when email alerts are enabled")
            if "smtp_port" in alerts:
                self._number(alerts["smtp_port"], "alerts.smtp_port", 1, 65535, integer=True)

    def check_billing(self) -> None:
        billing = self.config.get("billing")
        if not isinstance(billing, dict) or not billing.get("enabled"):
            return
        if "billing_period_days" in billing:
            self._number(billing["billing_period_days"], "billing.billing_period_days",
                         1, 3650, integer=True)
        for key in ("cost_per_cpu_core_per_hour", "cost_per_gb_ram_per_hour"):
            if key in billing:
                self._number(billing[key], f"billing.{key}", 0, 1e6)
        if billing.get("csv_output_dir") is not None and not isinstance(
                billing["csv_output_dir"], str):
            self.error("billing.csv_output_dir", "expected a path")

    def check_dry_run(self) -> None:
        if "dry_run" in self.config:
            self._bool(self.config["dry_run"], "dry_run")
        if "auto_configure_hotplug" in self.config:
            self._bool(self.config["auto_configure_hotplug"], "auto_configure_hotplug")

    def run(self) -> tuple[list[str], list[str]]:
        if not isinstance(self.config, dict):
            self.error("<root>",
                       f"expected a mapping at the top level, got "
                       f"{type(self.config).__name__}")
            return self.errors, self.warnings

        self._unknown_keys(self.config, KNOWN_TOP_LEVEL, "<root>")

        for section in ("scaling_thresholds", "scaling_limits",
                        "proxmox_hosts", "virtual_machines"):
            if section not in self.config:
                self.error(section, "missing; it is required")

        if "scaling_thresholds" in self.config:
            self.check_thresholds(self.config["scaling_thresholds"], "scaling_thresholds")
        if "scaling_limits" in self.config:
            self.check_limits(self.config["scaling_limits"], "scaling_limits")

        host_names = self.check_hosts()
        self.check_vms(host_names)
        self.check_host_limits()
        self.check_timing()
        self.check_ssh_policy()
        self.check_metrics()
        self.check_notifications()
        self.check_billing()
        self.check_dry_run()

        return self.errors, self.warnings


def validate(config: dict[str, Any]) -> list[str]:
    """Validate a loaded configuration.

    Returns the list of warnings. Raises `ConfigurationInvalid` carrying every
    error found if the configuration cannot be used.
    """
    errors, warnings = _Validator(config).run()
    if errors:
        raise ConfigurationInvalid(errors)
    return warnings
