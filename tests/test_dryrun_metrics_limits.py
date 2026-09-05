"""
Regression tests for four defects fixed together.

1. `qm set` failures were reported as successes: `execute_command` returns the
   exit status instead of raising, and every caller discarded it.
2. There was no dry-run mode, so there was no way to see what the service would
   do without letting it do it.
3. `scaling_limits` was global, so one instance could not manage a 2-core web
   server and a 16-core database.
4. There was no way to monitor the service except by parsing its log.
"""

import json
import logging
import os
import sys
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics import MetricsRegistry, MetricsServer, build_registry
from vm_manager import CommandFailed, VMResourceManager


def make_logger():
    logger = logging.getLogger("test")
    logger.addHandler(logging.NullHandler())
    return logger


def ssh_returning(config_text="cores: 4\nvcpus: 4\nmemory: 4096\nhotplug: cpu,memory\nnuma: 1",
                  status=0, running=True, set_status=0):
    """SSH double with independently controllable read and write statuses."""
    ssh = MagicMock()

    def respond(cmd, *args, **kwargs):
        if "qm status" in cmd:
            return ("status: running" if running else "status: stopped", "", 0)
        if "qm set" in cmd:
            return ("", "unable to modify" if set_status else "", set_status)
        return (config_text, "permission denied" if status else "", status)

    ssh.execute_command.side_effect = respond
    return ssh


def manager(ssh, config=None, vm_config=None):
    cfg = {"auto_configure_hotplug": False, "scale_cooldown": 0,
           "scaling_limits": {"min_cores": 1, "max_cores": 8,
                              "min_ram_mb": 512, "max_ram_mb": 16384}}
    cfg.update(config or {})
    return VMResourceManager(ssh, "101", cfg, vm_config)


# ---------------------------------------------------------------------------
# 1. Command failures
# ---------------------------------------------------------------------------

class TestCommandFailuresAreNotSuccesses(unittest.TestCase):

    def test_a_failing_qm_set_raises(self):
        mgr = manager(ssh_returning(set_status=2))
        with self.assertRaises(CommandFailed):
            mgr._set_ram(2048)

    def test_the_error_names_the_command_and_the_status(self):
        mgr = manager(ssh_returning(set_status=2))
        with self.assertRaises(CommandFailed) as ctx:
            mgr._set_cores(4)
        message = str(ctx.exception)
        self.assertIn("qm set 101 -cores 4", message)
        self.assertIn("2", message)
        self.assertIn("unable to modify", message)

    def test_a_failed_scale_does_not_start_the_cooldown(self):
        """A command that did not work must not rate-limit the next attempt."""
        mgr = manager(ssh_returning(set_status=1), {"scale_cooldown": 300})
        with self.assertRaises(CommandFailed):
            mgr.scale_cpu("up")
        self.assertTrue(mgr.can_scale("cpu"))

    def test_a_failing_read_raises_instead_of_inventing_a_value(self):
        """Returning a default of 1 core made scale_cpu act on a fiction."""
        mgr = manager(ssh_returning(status=1))
        with self.assertRaises(CommandFailed):
            mgr._get_current_cores()

    def test_an_absent_key_still_means_the_proxmox_default(self):
        mgr = manager(ssh_returning(config_text="name: web1"))
        self.assertEqual(mgr._get_current_cores(), 1)
        self.assertEqual(mgr._get_current_ram(), 512)

    def test_a_missing_vm_is_not_retried(self):
        ssh = MagicMock()
        ssh.execute_command.return_value = ("", "no such VM", 2)
        mgr = manager(ssh)
        with patch("time.sleep") as slept:
            self.assertFalse(mgr.is_vm_running())
        slept.assert_not_called()

    def test_bare_string_results_are_still_accepted(self):
        """Older doubles and call paths hand back a plain string."""
        self.assertEqual(VMResourceManager._unpack("cores: 2"), ("cores: 2", "", 0))
        self.assertEqual(VMResourceManager._unpack(("out", "err", 3)), ("out", "err", 3))


# ---------------------------------------------------------------------------
# 2. Dry run
# ---------------------------------------------------------------------------

class TestDryRun(unittest.TestCase):

    def _issued(self, ssh):
        return [c.args[0] for c in ssh.execute_command.call_args_list]

    def test_no_qm_set_is_issued(self):
        ssh = ssh_returning()
        mgr = manager(ssh, {"dry_run": True})
        mgr.scale_cpu("up")
        self.assertFalse([c for c in self._issued(ssh) if "qm set" in c])

    def test_reads_still_happen(self):
        """Dry run must still evaluate, or it would show nothing useful."""
        ssh = ssh_returning()
        mgr = manager(ssh, {"dry_run": True})
        mgr.scale_cpu("up")
        self.assertTrue([c for c in self._issued(ssh) if "qm config" in c])

    def test_the_decision_is_still_reported_as_taken(self):
        ssh = ssh_returning()
        mgr = manager(ssh, {"dry_run": True})
        self.assertTrue(mgr.scale_cpu("up"))

    def test_hotplug_autoconfiguration_changes_nothing(self):
        ssh = ssh_returning(config_text="cores: 4\nmemory: 8192")
        manager(ssh, {"dry_run": True, "auto_configure_hotplug": True})
        self.assertFalse([c for c in self._issued(ssh) if "qm set" in c])

    def test_ram_scaling_issues_nothing_either(self):
        ssh = ssh_returning()
        mgr = manager(ssh, {"dry_run": True})
        mgr.scale_ram("up")
        self.assertFalse([c for c in self._issued(ssh) if "qm set" in c])

    def test_it_is_off_by_default(self):
        self.assertFalse(manager(ssh_returning()).dry_run)

    def test_a_real_run_does_issue_the_command(self):
        ssh = ssh_returning()
        mgr = manager(ssh)
        mgr.scale_cpu("up")
        self.assertTrue([c for c in self._issued(ssh) if "qm set" in c])


# ---------------------------------------------------------------------------
# 3. Per-VM scaling limits
# ---------------------------------------------------------------------------

class TestPerVMScalingLimits(unittest.TestCase):

    def test_a_vm_can_raise_its_own_ceiling(self):
        mgr = manager(ssh_returning(), vm_config={"scaling_limits": {"max_cores": 16}})
        self.assertEqual(mgr._get_max_cores(), 16)

    def test_unset_bounds_fall_back_to_the_global_section(self):
        mgr = manager(ssh_returning(), vm_config={"scaling_limits": {"max_cores": 16}})
        self.assertEqual(mgr._get_min_cores(), 1)
        self.assertEqual(mgr._get_max_ram(), 16384)

    def test_no_per_vm_block_means_the_global_values(self):
        mgr = manager(ssh_returning(), vm_config={})
        self.assertEqual(mgr._get_max_cores(), 8)

    def test_a_lower_per_vm_ceiling_blocks_scaling_the_global_one_would_allow(self):
        mgr = manager(ssh_returning(config_text="cores: 4\nvcpus: 4"),
                      vm_config={"scaling_limits": {"max_cores": 4}})
        self.assertFalse(mgr.scale_cpu("up"))

    def test_a_higher_per_vm_ceiling_allows_scaling_the_global_one_would_block(self):
        mgr = manager(ssh_returning(config_text="cores: 8\nvcpus: 8\nhotplug: cpu\nnuma: 1"),
                      vm_config={"scaling_limits": {"max_cores": 16}})
        self.assertTrue(mgr.scale_cpu("up"))

    def test_a_non_mapping_block_is_ignored(self):
        mgr = manager(ssh_returning(), vm_config={"scaling_limits": "nonsense"})
        self.assertEqual(mgr._get_max_cores(), 8)


# ---------------------------------------------------------------------------
# 4. Metrics
# ---------------------------------------------------------------------------

class TestMetricsRegistry(unittest.TestCase):

    def test_counters_accumulate_per_label_set(self):
        r = MetricsRegistry()
        r.describe("actions_total", "counter", "Actions.")
        r.inc("actions_total", {"vm_id": "101"})
        r.inc("actions_total", {"vm_id": "101"})
        r.inc("actions_total", {"vm_id": "102"})
        out = r.render()
        self.assertIn('actions_total{vm_id="101"} 2', out)
        self.assertIn('actions_total{vm_id="102"} 1', out)

    def test_gauges_are_overwritten(self):
        r = MetricsRegistry()
        r.describe("cpu", "gauge", "CPU.")
        r.set("cpu", 10, {"vm_id": "101"})
        r.set("cpu", 42.5, {"vm_id": "101"})
        self.assertIn('cpu{vm_id="101"} 42.5', r.render())

    def test_unset_removes_the_series_entirely(self):
        """An unreadable metric must be absent, not zero."""
        r = MetricsRegistry()
        r.describe("cpu", "gauge", "CPU.")
        r.set("cpu", 42, {"vm_id": "101"})
        r.unset("cpu", {"vm_id": "101"})
        self.assertNotIn("cpu{", r.render())

    def test_help_and_type_lines_are_emitted(self):
        r = MetricsRegistry()
        r.describe("cycles_total", "counter", "Cycles completed.")
        r.inc("cycles_total")
        out = r.render()
        self.assertIn("# HELP cycles_total Cycles completed.", out)
        self.assertIn("# TYPE cycles_total counter", out)

    def test_label_values_are_escaped(self):
        r = MetricsRegistry()
        r.describe("g", "gauge", "G.")
        r.set("g", 1, {"host": 'we"ird\\path'})
        self.assertIn(r'host="we\"ird\\path"', r.render())

    def test_concurrent_increments_are_not_lost(self):
        r = MetricsRegistry()
        r.describe("c", "counter", "C.")

        def bump():
            for _ in range(500):
                r.inc("c")

        threads = [threading.Thread(target=bump) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertIn("c 2000", r.render())

    def test_the_shipped_registry_describes_what_it_exports(self):
        r = build_registry()
        out = r.render()
        self.assertIn("vm_autoscale_up 1", out)
        self.assertIn("# TYPE vm_autoscale_up gauge", out)


class TestMetricsServer(unittest.TestCase):

    def setUp(self):
        self.registry = build_registry()
        self.registry.inc("vm_autoscale_cycles_total")
        self.server = MetricsServer(self.registry, make_logger(),
                                    bind="127.0.0.1", port=0, path="/metrics")
        self.assertTrue(self.server.start())
        self.addCleanup(self.server.stop)
        self.port = self.server._server.server_address[1]

    def url(self, path):
        return f"http://127.0.0.1:{self.port}{path}"

    def test_serves_the_exposition_format(self):
        with urllib.request.urlopen(self.url("/metrics"), timeout=5) as resp:
            body = resp.read().decode()
            self.assertEqual(resp.status, 200)
            self.assertIn("version=0.0.4", resp.headers["Content-Type"])
        self.assertIn("vm_autoscale_cycles_total 1", body)

    def test_other_paths_are_404(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(self.url("/"), timeout=5)
        self.assertEqual(ctx.exception.code, 404)

    def test_a_query_string_still_matches(self):
        with urllib.request.urlopen(self.url("/metrics?x=1"), timeout=5) as resp:
            self.assertEqual(resp.status, 200)

    def test_a_bind_failure_is_survivable(self):
        """A metrics endpoint is not worth taking the autoscaler down for."""
        clash = MetricsServer(build_registry(), make_logger(),
                              bind="127.0.0.1", port=self.port)
        self.assertFalse(clash.start())


if __name__ == "__main__":
    unittest.main()
