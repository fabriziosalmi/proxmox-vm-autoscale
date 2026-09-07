"""
Shared construction helpers for the test suite.

Every test used to build a `VMAutoscaler` by patching `__init__` away and then
setting seven attributes by hand. Two consequences, both of which actually
happened: the constructor - the method that wires the entire object graph - was
never executed by the suite, and adding a single attribute to it broke ten
tests across two files without any behaviour changing.

`make_autoscaler` runs the real constructor against a real temporary config
file instead. Tests get a correctly wired object, the constructor and the
configuration contract are exercised on every call, and a new attribute costs
nothing.
"""

import logging
import os
import tempfile
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import yaml

from autoscale import VMAutoscaler


def valid_config(**overrides: Any) -> Dict[str, Any]:
    """A configuration that passes validation, as a starting point."""
    config: Dict[str, Any] = {
        "scaling_thresholds": {
            "cpu": {"high": 80, "low": 20},
            "ram": {"high": 85, "low": 25},
        },
        "scaling_limits": {
            "min_cores": 1, "max_cores": 8,
            "min_ram_mb": 1024, "max_ram_mb": 16384,
        },
        "check_interval": 300,
        "scale_cooldown": 300,
        # One reading is enough by default in tests; the sustained-shrink
        # behaviour has its own tests that set this explicitly.
        "scale_down_after_cycles": 1,
        # Deduplication would swallow the second of two identical notifications
        # and make assertions depend on message text. Tests that care about it
        # set it themselves.
        "notification_dedup_seconds": 0,
        "proxmox_hosts": [
            {"name": "pve1", "host": "10.0.0.11", "ssh_user": "root",
             "ssh_key": "/root/.ssh/id_ed25519", "ssh_port": 22},
        ],
        "virtual_machines": [
            {"vm_id": 101, "proxmox_host": "pve1", "scaling_enabled": True,
             "cpu_scaling": True, "ram_scaling": True},
        ],
        "host_limits": {"max_host_cpu_percent": 90, "max_host_ram_percent": 90},
    }
    config.update(overrides)
    return config


def write_config(config: Dict[str, Any], directory: str) -> str:
    """Write a config to a YAML file and point its log at the same directory."""
    config = dict(config)
    config.setdefault("logging", {})
    config["logging"] = dict(config["logging"])
    config["logging"].setdefault("log_file", os.path.join(directory, "autoscale.log"))

    path = os.path.join(directory, "config.yaml")
    with open(path, "w") as fh:
        yaml.safe_dump(config, fh)
    return path


class autoscaler_context:
    """Context manager yielding a fully constructed VMAutoscaler.

    Cleans up the temporary directory it created. Use `make_autoscaler` inside
    a `unittest.TestCase` instead; this exists for tests that want explicit
    control over the lifetime.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None,
                 mock_notifications: bool = True):
        self.config = config if config is not None else valid_config()
        self.mock_notifications = mock_notifications
        self._tmp = None

    def __enter__(self) -> VMAutoscaler:
        self._tmp = tempfile.TemporaryDirectory()
        path = write_config(self.config, self._tmp.name)

        # The real constructor: validation, signal handlers, metrics registry,
        # manager cache, billing wiring.
        autoscaler = VMAutoscaler(config_path=path)

        if self.mock_notifications:
            autoscaler.notification_manager = MagicMock()
        return autoscaler

    def __exit__(self, *exc) -> None:
        if self._tmp is not None:
            self._tmp.cleanup()
            self._tmp = None


def make_autoscaler(test_case, config: Optional[Dict[str, Any]] = None,
                    mock_notifications: bool = True) -> VMAutoscaler:
    """Construct a real VMAutoscaler, cleaned up when `test_case` finishes."""
    tmp = tempfile.TemporaryDirectory()
    test_case.addCleanup(tmp.cleanup)

    path = write_config(config if config is not None else valid_config(), tmp.name)
    autoscaler = VMAutoscaler(config_path=path)

    if mock_notifications:
        autoscaler.notification_manager = MagicMock()

    test_case.addCleanup(autoscaler.shutdown)
    return autoscaler


def quiet_logger(name: str = "test") -> logging.Logger:
    logger = logging.getLogger(name)
    logger.addHandler(logging.NullHandler())
    return logger
