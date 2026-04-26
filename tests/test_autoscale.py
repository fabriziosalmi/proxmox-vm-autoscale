"""
Unit tests for autoscale.py — NotificationManager and VMAutoscaler.

These tests verify:
- NotificationManager config validation
- _format_message with various input types
- send_notification routing (Gotify / SMTP / neither)
- VMAutoscaler._load_config: missing file, missing sections
- _handle_cpu_scaling: scale up on high usage, scale down on low usage, no-op in middle
- _handle_ram_scaling: scale up on high usage, scale down on low usage, no-op in middle
- _record_billing_spec integration
"""

import os
import sys
import tempfile
import unittest
import yaml
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoscale import NotificationManager, ConfigurationError, VMAutoscaler


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_logger():
    return MagicMock()


def minimal_config(**overrides):
    """Return a minimal valid config dict, optionally overriding keys."""
    cfg = {
        "gotify": {"enabled": False},
        "alerts": {"email_enabled": False},
    }
    cfg.update(overrides)
    return cfg


def full_autoscaler_config():
    return {
        "scaling_thresholds": {
            "cpu": {"high": 80, "low": 20},
            "ram": {"high": 80, "low": 20},
        },
        "scaling_limits": {},
        "proxmox_hosts": [],
        "virtual_machines": [],
        "host_limits": {"max_host_cpu_percent": 90, "max_host_ram_percent": 90},
    }


# ---------------------------------------------------------------------------
# NotificationManager — config validation
# ---------------------------------------------------------------------------

class TestNotificationManagerValidation(unittest.TestCase):

    def test_no_notification_channel_logs_warning(self):
        logger = make_logger()
        NotificationManager(minimal_config(), logger)
        logger.warning.assert_called()

    def test_gotify_enabled_but_incomplete_raises(self):
        cfg = minimal_config(gotify={"enabled": True, "server_url": "", "app_token": ""})
        with self.assertRaises(ConfigurationError):
            NotificationManager(cfg, make_logger())

    def test_gotify_enabled_with_valid_config_does_not_raise(self):
        cfg = minimal_config(gotify={"enabled": True, "server_url": "http://g", "app_token": "tok"})
        # Should not raise
        NotificationManager(cfg, make_logger())

    def test_email_enabled_but_missing_fields_raises(self):
        cfg = minimal_config(alerts={
            "email_enabled": True,
            "smtp_server": "",
            "smtp_user": "",
            "email_recipient": "",
        })
        with self.assertRaises(ConfigurationError):
            NotificationManager(cfg, make_logger())

    def test_email_enabled_with_valid_config_does_not_raise(self):
        cfg = minimal_config(alerts={
            "email_enabled": True,
            "smtp_server": "smtp.example.com",
            "smtp_user": "user@example.com",
            "email_recipient": "dest@example.com",
        })
        NotificationManager(cfg, make_logger())


# ---------------------------------------------------------------------------
# NotificationManager — _format_message
# ---------------------------------------------------------------------------

class TestFormatMessage(unittest.TestCase):

    def setUp(self):
        self.nm = NotificationManager(minimal_config(), make_logger())

    def test_string_passthrough(self):
        self.assertEqual(self.nm._format_message("hello"), "hello")

    def test_tuple_joined(self):
        self.assertEqual(self.nm._format_message(("hello", "world")), "hello world")

    def test_tuple_skips_empty_parts(self):
        self.assertEqual(self.nm._format_message(("hello", "", "world")), "hello world")

    def test_non_string_converted(self):
        result = self.nm._format_message(42)
        self.assertEqual(result, "42")


# ---------------------------------------------------------------------------
# NotificationManager — send_notification routing
# ---------------------------------------------------------------------------

class TestSendNotificationRouting(unittest.TestCase):

    def test_routes_to_gotify_when_enabled(self):
        cfg = minimal_config(gotify={"enabled": True, "server_url": "http://g", "app_token": "tok"})
        nm = NotificationManager(cfg, make_logger())
        nm.send_gotify_notification = MagicMock()
        nm.send_notification("test")
        nm.send_gotify_notification.assert_called_once()

    def test_routes_to_email_when_enabled(self):
        cfg = minimal_config(alerts={
            "email_enabled": True,
            "smtp_server": "smtp.example.com",
            "smtp_user": "u@e.com",
            "email_recipient": "r@e.com",
        })
        nm = NotificationManager(cfg, make_logger())
        nm.send_smtp_notification = MagicMock()
        nm.send_notification("test")
        nm.send_smtp_notification.assert_called_once()

    def test_logs_warning_when_no_channel_sends(self):
        nm = NotificationManager(minimal_config(), make_logger())
        nm.logger = make_logger()
        nm.send_notification("test")
        nm.logger.warning.assert_called()

    def test_continues_to_email_if_gotify_fails(self):
        cfg = {
            "gotify": {"enabled": True, "server_url": "http://g", "app_token": "tok"},
            "alerts": {
                "email_enabled": True,
                "smtp_server": "smtp.example.com",
                "smtp_user": "u@e.com",
                "email_recipient": "r@e.com",
            },
        }
        nm = NotificationManager(cfg, make_logger())
        nm.send_gotify_notification = MagicMock(side_effect=Exception("gotify down"))
        nm.send_smtp_notification = MagicMock()
        nm.send_notification("test")
        nm.send_smtp_notification.assert_called_once()


# ---------------------------------------------------------------------------
# VMAutoscaler — _load_config
# ---------------------------------------------------------------------------

class TestLoadConfig(unittest.TestCase):

    def _write_yaml(self, data):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False)
        yaml.dump(data, f)
        f.close()
        return f.name

    def test_raises_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            VMAutoscaler._load_config("/nonexistent/path/config.yaml")

    def test_raises_on_missing_required_sections(self):
        path = self._write_yaml({"scaling_thresholds": {}})
        with self.assertRaises(ConfigurationError):
            VMAutoscaler._load_config(path)
        os.unlink(path)

    def test_loads_valid_config(self):
        path = self._write_yaml(full_autoscaler_config())
        cfg = VMAutoscaler._load_config(path)
        self.assertIn("proxmox_hosts", cfg)
        os.unlink(path)


# ---------------------------------------------------------------------------
# VMAutoscaler — _handle_cpu_scaling
# ---------------------------------------------------------------------------

class TestHandleCPUScaling(unittest.TestCase):

    def _make_autoscaler(self):
        """Build a VMAutoscaler with mocked init to avoid file I/O."""
        with patch.object(VMAutoscaler, "__init__", lambda s, *a, **kw: None):
            a = VMAutoscaler.__new__(VMAutoscaler)
        a.config = {
            "scaling_thresholds": {"cpu": {"high": 80, "low": 20}},
        }
        a.logger = make_logger()
        a.notification_manager = MagicMock()
        a.billing_tracker = None
        return a

    def test_scales_up_on_high_cpu(self):
        a = self._make_autoscaler()
        vm_manager = MagicMock()
        vm_manager.scale_cpu.return_value = True
        a._handle_cpu_scaling(vm_manager, vm_id=101, cpu_usage=90.0)
        vm_manager.scale_cpu.assert_called_once_with("up")
        a.notification_manager.send_notification.assert_called_once()

    def test_scales_down_on_low_cpu(self):
        a = self._make_autoscaler()
        vm_manager = MagicMock()
        vm_manager.scale_cpu.return_value = True
        a._handle_cpu_scaling(vm_manager, vm_id=101, cpu_usage=5.0)
        vm_manager.scale_cpu.assert_called_once_with("down")
        a.notification_manager.send_notification.assert_called_once()

    def test_no_scaling_in_middle_band(self):
        a = self._make_autoscaler()
        vm_manager = MagicMock()
        a._handle_cpu_scaling(vm_manager, vm_id=101, cpu_usage=50.0)
        vm_manager.scale_cpu.assert_not_called()
        a.notification_manager.send_notification.assert_not_called()

    def test_no_notification_when_scale_cpu_returns_false(self):
        a = self._make_autoscaler()
        vm_manager = MagicMock()
        vm_manager.scale_cpu.return_value = False   # cooldown / already at limit
        a._handle_cpu_scaling(vm_manager, vm_id=101, cpu_usage=90.0)
        a.notification_manager.send_notification.assert_not_called()

    def test_records_billing_when_tracker_present(self):
        a = self._make_autoscaler()
        a.billing_tracker = MagicMock()
        a._record_billing_spec = MagicMock()
        vm_manager = MagicMock()
        vm_manager.scale_cpu.return_value = True
        a._handle_cpu_scaling(vm_manager, vm_id=101, cpu_usage=90.0)
        a._record_billing_spec.assert_called_once()


# ---------------------------------------------------------------------------
# VMAutoscaler — _handle_ram_scaling
# ---------------------------------------------------------------------------

class TestHandleRAMScaling(unittest.TestCase):

    def _make_autoscaler(self):
        with patch.object(VMAutoscaler, "__init__", lambda s, *a, **kw: None):
            a = VMAutoscaler.__new__(VMAutoscaler)
        a.config = {
            "scaling_thresholds": {"ram": {"high": 80, "low": 20}},
        }
        a.logger = make_logger()
        a.notification_manager = MagicMock()
        a.billing_tracker = None
        return a

    def test_scales_up_on_high_ram(self):
        a = self._make_autoscaler()
        vm_manager = MagicMock()
        vm_manager.scale_ram.return_value = True
        a._handle_ram_scaling(vm_manager, vm_id=101, ram_usage=90.0)
        vm_manager.scale_ram.assert_called_once_with("up")
        a.notification_manager.send_notification.assert_called_once()

    def test_scales_down_on_low_ram(self):
        a = self._make_autoscaler()
        vm_manager = MagicMock()
        vm_manager.scale_ram.return_value = True
        a._handle_ram_scaling(vm_manager, vm_id=101, ram_usage=5.0)
        vm_manager.scale_ram.assert_called_once_with("down")
        a.notification_manager.send_notification.assert_called_once()

    def test_no_scaling_in_middle_band(self):
        a = self._make_autoscaler()
        vm_manager = MagicMock()
        a._handle_ram_scaling(vm_manager, vm_id=101, ram_usage=50.0)
        vm_manager.scale_ram.assert_not_called()
        a.notification_manager.send_notification.assert_not_called()

    def test_no_notification_when_scale_ram_returns_false(self):
        a = self._make_autoscaler()
        vm_manager = MagicMock()
        vm_manager.scale_ram.return_value = False
        a._handle_ram_scaling(vm_manager, vm_id=101, ram_usage=90.0)
        a.notification_manager.send_notification.assert_not_called()

    def test_records_billing_when_tracker_present(self):
        a = self._make_autoscaler()
        a.billing_tracker = MagicMock()
        a._record_billing_spec = MagicMock()
        vm_manager = MagicMock()
        vm_manager.scale_ram.return_value = True
        a._handle_ram_scaling(vm_manager, vm_id=101, ram_usage=90.0)
        a._record_billing_spec.assert_called_once()


# ---------------------------------------------------------------------------
# VMResourceManager — get_resource_usage / scale_cpu / scale_ram / can_scale
# ---------------------------------------------------------------------------

import time
from vm_manager import VMResourceManager


def make_vm_manager(responses=None, config=None):
    ssh = MagicMock()

    def execute_side_effect(cmd):
        if responses:
            for key, val in responses.items():
                if key in cmd:
                    return val
        return ("", "", 0)

    ssh.execute_command.side_effect = execute_side_effect
    cfg = config or {
        "auto_configure_hotplug": False,
        "scale_cooldown": 0,
        "min_cores": 1, "max_cores": 8,
        "min_ram": 512, "max_ram": 16384,
    }
    return VMResourceManager(ssh, "101", cfg)


class TestGetResourceUsage(unittest.TestCase):

    def test_returns_zero_when_vm_not_running(self):
        mgr = make_vm_manager(responses={"qm status": ("status: stopped", "", 0)})
        cpu, ram = mgr.get_resource_usage()
        self.assertEqual(cpu, 0.0)
        self.assertEqual(ram, 0.0)

    def test_parses_cpu_and_ram_from_output(self):
        running = ("status: running", "", 0)
        usage_line = ("  3.17%     5.00 GiB     3.82 GiB ", "", 0)
        ssh = MagicMock()

        def side(cmd):
            if "qm status" in cmd:
                return running
            return usage_line

        ssh.execute_command.side_effect = side
        mgr = VMResourceManager(ssh, "101", {
            "auto_configure_hotplug": False, "scale_cooldown": 0,
            "min_cores": 1, "max_cores": 8, "min_ram": 512, "max_ram": 16384,
        })
        cpu, ram = mgr.get_resource_usage()
        self.assertAlmostEqual(cpu, 3.17)
        self.assertGreater(ram, 0)


class TestParseCPUUsage(unittest.TestCase):

    def _mgr(self):
        return make_vm_manager()

    def test_parses_percentage(self):
        mgr = self._mgr()
        self.assertAlmostEqual(mgr._parse_cpu_usage("  5.25%  2.00 GiB  1.00 GiB"), 5.25)

    def test_returns_zero_for_no_match(self):
        mgr = self._mgr()
        self.assertEqual(mgr._parse_cpu_usage("no numbers here"), 0.0)

    def test_returns_zero_for_empty_string(self):
        mgr = self._mgr()
        self.assertEqual(mgr._parse_cpu_usage(""), 0.0)


class TestParseRAMUsage(unittest.TestCase):

    def _mgr(self):
        return make_vm_manager()

    def test_parses_gib_values(self):
        mgr = self._mgr()
        # 2 GiB used out of 4 GiB → 50%
        pct = mgr._parse_ram_usage("  10%   4.00 GiB   2.00 GiB ")
        self.assertAlmostEqual(pct, 50.0)

    def test_parses_mib_values(self):
        mgr = self._mgr()
        # 512 MiB = 0.5 GiB used, 1024 MiB = 1 GiB total → 50%
        pct = mgr._parse_ram_usage("  5%   1024.00 MiB   512.00 MiB ")
        self.assertAlmostEqual(pct, 50.0)

    def test_returns_zero_for_no_match(self):
        mgr = self._mgr()
        self.assertEqual(mgr._parse_ram_usage("no memory info"), 0.0)

    def test_returns_zero_for_zero_max_mem(self):
        mgr = self._mgr()
        self.assertEqual(mgr._parse_ram_usage("  5%   0.00 GiB   0.00 GiB "), 0.0)


class TestCanScale(unittest.TestCase):

    def test_can_scale_initially(self):
        mgr = make_vm_manager()
        self.assertTrue(mgr.can_scale())

    def test_cannot_scale_during_cooldown(self):
        mgr = make_vm_manager(config={
            "auto_configure_hotplug": False,
            "scale_cooldown": 300,
            "min_cores": 1, "max_cores": 8,
            "min_ram": 512, "max_ram": 16384,
        })
        mgr.last_scale_time = time.time()   # simulate recent scale
        self.assertFalse(mgr.can_scale())

    def test_can_scale_after_cooldown_expires(self):
        mgr = make_vm_manager(config={
            "auto_configure_hotplug": False,
            "scale_cooldown": 1,
            "min_cores": 1, "max_cores": 8,
            "min_ram": 512, "max_ram": 16384,
        })
        mgr.last_scale_time = time.time() - 5   # well past cooldown
        self.assertTrue(mgr.can_scale())


class TestScaleCPU(unittest.TestCase):

    def _mgr_with_cores(self, cores, vcpus, running=True):
        status = "status: running" if running else "status: stopped"
        config_str = f"cores: {cores}\nvcpus: {vcpus}\nhotplug: cpu,memory\nnuma: 1"
        ssh = MagicMock()

        def side(cmd):
            if "qm status" in cmd:
                return (status, "", 0)
            return (config_str, "", 0)

        ssh.execute_command.side_effect = side
        return VMResourceManager(ssh, "101", {
            "auto_configure_hotplug": False, "scale_cooldown": 0,
            "min_cores": 1, "max_cores": 8,
        })

    def test_scale_up_returns_true_when_below_max(self):
        mgr = self._mgr_with_cores(2, 2)
        result = mgr.scale_cpu("up")
        self.assertTrue(result)

    def test_scale_down_returns_true_when_above_min(self):
        mgr = self._mgr_with_cores(4, 4)
        result = mgr.scale_cpu("down")
        self.assertTrue(result)

    def test_scale_up_returns_false_at_max_cores(self):
        mgr = self._mgr_with_cores(8, 8)
        result = mgr.scale_cpu("up")
        self.assertFalse(result)

    def test_scale_down_returns_false_at_min_cores(self):
        mgr = self._mgr_with_cores(1, 1)
        result = mgr.scale_cpu("down")
        self.assertFalse(result)


class TestScaleRAM(unittest.TestCase):

    def _mgr_with_ram(self, ram_mb, running=True):
        status = "status: running" if running else "status: stopped"
        config_str = f"memory: {ram_mb}\nhotplug: cpu,memory\nnuma: 1"
        ssh = MagicMock()

        def side(cmd):
            if "qm status" in cmd:
                return (status, "", 0)
            return (config_str, "", 0)

        ssh.execute_command.side_effect = side
        return VMResourceManager(ssh, "101", {
            "auto_configure_hotplug": False, "scale_cooldown": 0,
            "min_ram": 512, "max_ram": 16384,
        })

    def test_scale_up_returns_true_when_below_max(self):
        mgr = self._mgr_with_ram(4096)
        self.assertTrue(mgr.scale_ram("up"))

    def test_scale_down_returns_true_when_above_min(self):
        mgr = self._mgr_with_ram(4096)
        self.assertTrue(mgr.scale_ram("down"))

    def test_scale_up_returns_false_at_max(self):
        mgr = self._mgr_with_ram(16384)
        self.assertFalse(mgr.scale_ram("up"))

    def test_scale_down_returns_false_at_min(self):
        mgr = self._mgr_with_ram(512)
        self.assertFalse(mgr.scale_ram("down"))


if __name__ == "__main__":
    unittest.main()
