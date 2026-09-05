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

import json
import os
import sys
import tempfile
import unittest
import yaml
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics import build_registry
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
        a.dry_run = False
        a.metrics = build_registry()
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
        a.dry_run = False
        a.metrics = build_registry()
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


GIB = 1024 ** 3


def cluster_entry(vmid, cpu=0.0317, mem=3.82 * GIB, maxmem=5 * GIB, **extra):
    """One `pvesh get /cluster/resources --output-format json` row."""
    entry = {
        "id": f"qemu/{vmid}",
        "type": "qemu",
        "vmid": int(vmid),
        "node": "pve1",
        "status": "running",
        "cpu": cpu,
        "maxcpu": 4,
        "mem": mem,
        "maxmem": maxmem,
    }
    entry.update(extra)
    return entry


def mgr_with_resources(payload, running=True, vm_id="101"):
    """Manager whose SSH double answers qm status and the cluster query."""
    status = ("status: running" if running else "status: stopped", "", 0)
    body = payload if isinstance(payload, str) else json.dumps(payload)
    ssh = MagicMock()

    def side(cmd, *args, **kwargs):
        if "qm status" in cmd:
            return status
        if "cluster/resources" in cmd:
            return (body, "", 0)
        return ("", "", 0)

    ssh.execute_command.side_effect = side
    return VMResourceManager(ssh, vm_id, {
        "auto_configure_hotplug": False, "scale_cooldown": 0,
        "min_cores": 1, "max_cores": 8, "min_ram": 512, "max_ram": 16384,
    })


class TestGetResourceUsage(unittest.TestCase):

    def test_returns_zero_when_vm_not_running(self):
        mgr = make_vm_manager(responses={"qm status": ("status: stopped", "", 0)})
        cpu, ram = mgr.get_resource_usage()
        self.assertEqual(cpu, 0.0)
        self.assertEqual(ram, 0.0)

    def test_reads_cpu_and_ram_from_json(self):
        mgr = mgr_with_resources([cluster_entry(101)])
        cpu, ram = mgr.get_resource_usage()
        self.assertAlmostEqual(cpu, 3.17)
        self.assertAlmostEqual(ram, 3.82 / 5 * 100)

    def test_queries_pvesh_with_json_output(self):
        mgr = mgr_with_resources([cluster_entry(101)])
        mgr.get_resource_usage()
        issued = [c.args[0] for c in mgr.ssh_client.execute_command.call_args_list]
        query = next(c for c in issued if "cluster/resources" in c)
        self.assertIn("--output-format json", query)
        # The old pipeline scraped a box-drawing table; nothing should shell out.
        self.assertNotIn("awk", query)
        self.assertNotIn("grep", query)

    def test_picks_the_exact_vmid_not_a_prefix_match(self):
        """`grep 'qemu/101'` used to match VMID 1010 and read the wrong row."""
        mgr = mgr_with_resources([
            cluster_entry(1010, cpu=0.90, mem=1 * GIB, maxmem=2 * GIB),
            cluster_entry(101, cpu=0.05, mem=1 * GIB, maxmem=4 * GIB),
        ])
        cpu, ram = mgr.get_resource_usage()
        self.assertAlmostEqual(cpu, 5.0)
        self.assertAlmostEqual(ram, 25.0)

    def test_ignores_lxc_containers_with_the_same_vmid(self):
        mgr = mgr_with_resources([
            dict(cluster_entry(101, cpu=0.90), type="lxc", id="lxc/101"),
            cluster_entry(101, cpu=0.05, mem=1 * GIB, maxmem=4 * GIB),
        ])
        cpu, _ = mgr.get_resource_usage()
        self.assertAlmostEqual(cpu, 5.0)


class TestUnreadableMetricsAreNotZero(unittest.TestCase):
    """A metric that cannot be read must never look like an idle guest.

    Returning 0.0 put the VM below every sensible `low` threshold, so a parse
    failure scaled it down one step per cycle until it hit its minimum.
    """

    def test_malformed_json_returns_none(self):
        mgr = mgr_with_resources("not json at all")
        self.assertEqual(mgr.get_resource_usage(), (None, None))

    def test_empty_response_returns_none(self):
        mgr = mgr_with_resources("")
        self.assertEqual(mgr.get_resource_usage(), (None, None))

    def test_payload_that_is_not_a_list_returns_none(self):
        mgr = mgr_with_resources({"error": "permission denied"})
        self.assertEqual(mgr.get_resource_usage(), (None, None))

    def test_vm_absent_from_cluster_resources_returns_none(self):
        mgr = mgr_with_resources([cluster_entry(999)])
        self.assertEqual(mgr.get_resource_usage(), (None, None))

    def test_ssh_failure_on_the_metrics_query_returns_none(self):
        """The status check succeeds, the cluster query does not."""
        mgr = mgr_with_resources([cluster_entry(101)])

        def side(cmd, *args, **kwargs):
            if "qm status" in cmd:
                return ("status: running", "", 0)
            raise OSError("connection reset by peer")

        mgr.ssh_client.execute_command.side_effect = side
        self.assertEqual(mgr.get_resource_usage(), (None, None))

    def test_missing_cpu_field_returns_none_for_cpu_only(self):
        entry = cluster_entry(101)
        del entry["cpu"]
        mgr = mgr_with_resources([entry])
        cpu, ram = mgr.get_resource_usage()
        self.assertIsNone(cpu)
        self.assertIsNotNone(ram)

    def test_missing_memory_fields_return_none_for_ram_only(self):
        entry = cluster_entry(101)
        del entry["maxmem"]
        mgr = mgr_with_resources([entry])
        cpu, ram = mgr.get_resource_usage()
        self.assertIsNotNone(cpu)
        self.assertIsNone(ram)

    def test_zero_maxmem_returns_none_rather_than_zero(self):
        mgr = mgr_with_resources([cluster_entry(101, mem=0, maxmem=0)])
        _, ram = mgr.get_resource_usage()
        self.assertIsNone(ram)

    def test_non_numeric_values_return_none(self):
        mgr = mgr_with_resources([cluster_entry(101, cpu="n/a", mem="?", maxmem="?")])
        self.assertEqual(mgr.get_resource_usage(), (None, None))

    def test_a_genuinely_idle_vm_still_reports_zero(self):
        """Zero must remain reachable, or scale-down would never trigger."""
        mgr = mgr_with_resources([cluster_entry(101, cpu=0.0, mem=0, maxmem=4 * GIB)])
        cpu, ram = mgr.get_resource_usage()
        self.assertEqual(cpu, 0.0)
        self.assertEqual(ram, 0.0)


class TestUnreadableMetricsNeverScale(unittest.TestCase):
    """The autoscaler side of the same guarantee: None is never acted on."""

    def _autoscaler(self):
        with patch.object(VMAutoscaler, "__init__", lambda s, *a, **kw: None):
            a = VMAutoscaler.__new__(VMAutoscaler)
        a.config = {
            "scaling_thresholds": {"cpu": {"high": 80, "low": 20},
                                   "ram": {"high": 85, "low": 25}},
            "host_limits": {"max_host_cpu_percent": 90, "max_host_ram_percent": 90},
        }
        a.logger = make_logger()
        a.notification_manager = MagicMock()
        a.billing_tracker = None
        a.dry_run = False
        a.metrics = build_registry()
        a._vm_managers = {}
        return a

    def test_format_usage_distinguishes_unknown_from_zero(self):
        a = self._autoscaler()
        self.assertEqual(a._format_usage(None), "unavailable")
        self.assertEqual(a._format_usage(0.0), "0.00%")
        self.assertEqual(a._format_usage(3.17), "3.17%")

    def test_cpu_handler_ignores_none(self):
        a = self._autoscaler()
        vm_manager = MagicMock()
        a._handle_cpu_scaling(vm_manager, vm_id=101, cpu_usage=None)
        vm_manager.scale_cpu.assert_not_called()
        a.notification_manager.send_notification.assert_not_called()

    def test_ram_handler_ignores_none(self):
        a = self._autoscaler()
        vm_manager = MagicMock()
        a._handle_ram_scaling(vm_manager, vm_id=101, ram_usage=None)
        vm_manager.scale_ram.assert_not_called()
        a.notification_manager.send_notification.assert_not_called()

    def _run_process_vm(self, usage):
        a = self._autoscaler()
        vm_manager = MagicMock()
        vm_manager.is_vm_running.return_value = True
        vm_manager.get_resource_usage.return_value = usage
        a._vm_managers["101"] = vm_manager

        host = {"name": "pve1", "host": "10.0.0.11", "ssh_user": "root",
                "ssh_port": 22, "ssh_password": "x"}
        vm = {"vm_id": "101", "proxmox_host": "pve1", "scaling_enabled": True,
              "cpu_scaling": True, "ram_scaling": True}

        with patch("autoscale.SSHClient"), patch("autoscale.HostResourceChecker") as hrc:
            hrc.return_value.check_host_resources.return_value = True
            a.process_vm(host, vm)
        return vm_manager

    def test_process_vm_skips_scaling_when_both_metrics_are_unavailable(self):
        """A failed read used to read as 0% and scale the VM down."""
        vm_manager = self._run_process_vm((None, None))
        vm_manager.scale_cpu.assert_not_called()
        vm_manager.scale_ram.assert_not_called()

    def test_process_vm_still_scales_the_metric_it_could_read(self):
        vm_manager = self._run_process_vm((95.0, None))
        vm_manager.scale_cpu.assert_called_once_with("up")
        vm_manager.scale_ram.assert_not_called()

    def test_process_vm_acts_on_a_genuine_zero(self):
        """Zero still means idle and must still scale down."""
        vm_manager = self._run_process_vm((0.0, 0.0))
        vm_manager.scale_cpu.assert_called_once_with("down")
        vm_manager.scale_ram.assert_called_once_with("down")


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
        mgr._mark_scaled("cpu")             # simulate a real CPU scaling action
        self.assertFalse(mgr.can_scale("cpu"))

    def test_can_scale_after_cooldown_expires(self):
        mgr = make_vm_manager(config={
            "auto_configure_hotplug": False,
            "scale_cooldown": 1,
            "min_cores": 1, "max_cores": 8,
            "min_ram": 512, "max_ram": 16384,
        })
        mgr._resource_scale_times["cpu"] = time.time() - 5   # well past cooldown
        self.assertTrue(mgr.can_scale("cpu"))


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
