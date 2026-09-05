"""
Regression tests for scaling limits resolution and cooldown behaviour.

These cover three defects that made the documented configuration ineffective:

1. `scaling_limits` was declared mandatory by the config validator but never
   read: `VMResourceManager` looked the limits up as flat top-level keys under
   names that did not exist (`max_ram` / `min_ram` vs `max_ram_mb` /
   `min_ram_mb`), so every install silently ran on hardcoded defaults.
2. CPU and RAM shared a single cooldown timestamp, and the timestamp was
   consumed by the *check* rather than by an actual scaling action. Any CPU
   threshold breach therefore suppressed RAM scaling for the same cycle, even
   when the CPU had not moved at all.
3. `VMResourceManager` was rebuilt on every polling cycle, resetting the
   cooldown to zero, so `scale_cooldown` never applied between cycles.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from autoscale import VMAutoscaler
from vm_manager import VMResourceManager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def make_ssh(cores=2, vcpus=2, memory=4096, running=True):
    """SSH double returning a plausible `qm config` / `qm status` for one VM."""
    status = "status: running" if running else "status: stopped"
    vm_config = (
        f"cores: {cores}\n"
        f"vcpus: {vcpus}\n"
        f"memory: {memory}\n"
        "hotplug: cpu,memory\n"
        "numa: 1"
    )

    ssh = MagicMock()

    def respond(cmd, *args, **kwargs):
        if "qm status" in cmd:
            return (status, "", 0)
        return (vm_config, "", 0)

    ssh.execute_command.side_effect = respond
    return ssh


# ---------------------------------------------------------------------------
# 1. scaling_limits resolution
# ---------------------------------------------------------------------------

class TestScalingLimitsResolution(unittest.TestCase):

    def _mgr(self, config):
        config = dict(config)
        config.setdefault("auto_configure_hotplug", False)
        return VMResourceManager(make_ssh(), "101", config)

    def test_reads_limits_from_scaling_limits_section(self):
        mgr = self._mgr({
            "scaling_limits": {
                "min_cores": 2,
                "max_cores": 12,
                "min_ram_mb": 1024,
                "max_ram_mb": 32768,
            }
        })
        self.assertEqual(mgr._get_min_cores(), 2)
        self.assertEqual(mgr._get_max_cores(), 12)
        self.assertEqual(mgr._get_min_ram(), 1024)
        self.assertEqual(mgr._get_max_ram(), 32768)

    def test_falls_back_to_legacy_top_level_keys(self):
        mgr = self._mgr({
            "min_cores": 3,
            "max_cores": 6,
            "min_ram": 2048,
            "max_ram": 8192,
        })
        self.assertEqual(mgr._get_min_cores(), 3)
        self.assertEqual(mgr._get_max_cores(), 6)
        self.assertEqual(mgr._get_min_ram(), 2048)
        self.assertEqual(mgr._get_max_ram(), 8192)

    def test_scaling_limits_wins_over_legacy_keys(self):
        mgr = self._mgr({
            "scaling_limits": {"max_cores": 16, "max_ram_mb": 65536},
            "max_cores": 4,
            "max_ram": 1024,
        })
        self.assertEqual(mgr._get_max_cores(), 16)
        self.assertEqual(mgr._get_max_ram(), 65536)

    def test_defaults_when_nothing_configured(self):
        mgr = self._mgr({})
        self.assertEqual(mgr._get_min_cores(), 1)
        self.assertEqual(mgr._get_max_cores(), 8)
        self.assertEqual(mgr._get_min_ram(), 512)
        self.assertEqual(mgr._get_max_ram(), 16384)

    def test_shipped_config_yaml_limits_take_effect(self):
        """The limits documented in config.yaml must be the ones enforced."""
        with open(os.path.join(REPO_ROOT, "config.yaml")) as fh:
            shipped = yaml.safe_load(fh)
        shipped["auto_configure_hotplug"] = False

        mgr = VMResourceManager(make_ssh(), "101", shipped)
        limits = shipped["scaling_limits"]

        self.assertEqual(mgr._get_min_cores(), limits["min_cores"])
        self.assertEqual(mgr._get_max_cores(), limits["max_cores"])
        self.assertEqual(mgr._get_min_ram(), limits["min_ram_mb"])
        self.assertEqual(mgr._get_max_ram(), limits["max_ram_mb"])
        # min_ram_mb is 1024 precisely because NUMA misbehaves below it: the
        # old hardcoded 512 default was the value actually being enforced.
        self.assertNotEqual(mgr._get_min_ram(), 512)

    def test_min_ram_from_config_blocks_scaling_below_it(self):
        mgr = VMResourceManager(make_ssh(memory=1024), "101", {
            "auto_configure_hotplug": False,
            "scale_cooldown": 0,
            "scaling_limits": {"min_ram_mb": 1024, "max_ram_mb": 16384},
        })
        self.assertFalse(mgr.scale_ram("down"))

    def test_max_cores_from_config_blocks_scaling_above_it(self):
        mgr = VMResourceManager(make_ssh(cores=4, vcpus=4), "101", {
            "auto_configure_hotplug": False,
            "scale_cooldown": 0,
            "scaling_limits": {"min_cores": 1, "max_cores": 4},
        })
        self.assertFalse(mgr.scale_cpu("up"))


# ---------------------------------------------------------------------------
# 2. CPU and RAM cooldowns are independent and consumed only on real changes
# ---------------------------------------------------------------------------

class TestIndependentCooldowns(unittest.TestCase):

    def _mgr(self, cores=2, vcpus=2, memory=4096, cooldown=300):
        return VMResourceManager(make_ssh(cores, vcpus, memory), "101", {
            "auto_configure_hotplug": False,
            "scale_cooldown": cooldown,
            "scaling_limits": {
                "min_cores": 1, "max_cores": 8,
                "min_ram_mb": 512, "max_ram_mb": 16384,
            },
        })

    def test_cpu_scaling_does_not_block_ram_scaling(self):
        """Regression for issue #30: RAM stopped scaling whenever CPU scaled."""
        mgr = self._mgr()
        self.assertTrue(mgr.scale_cpu("up"))
        self.assertTrue(mgr.scale_ram("up"))

    def test_ram_scaling_does_not_block_cpu_scaling(self):
        mgr = self._mgr()
        self.assertTrue(mgr.scale_ram("up"))
        self.assertTrue(mgr.scale_cpu("up"))

    def test_same_resource_is_blocked_during_cooldown(self):
        mgr = self._mgr()
        self.assertTrue(mgr.scale_cpu("up"))
        self.assertFalse(mgr.scale_cpu("up"))

    def test_no_op_scaling_does_not_start_the_cooldown(self):
        """Hitting a limit is not a scaling action, so it must not rate-limit."""
        mgr = self._mgr(cores=8, vcpus=8)          # already at max_cores
        self.assertFalse(mgr.scale_cpu("up"))      # no change performed
        self.assertTrue(mgr.can_scale("cpu"))      # cooldown untouched
        self.assertTrue(mgr.scale_cpu("down"))     # a real change is allowed

    def test_can_scale_does_not_consume_the_cooldown(self):
        mgr = self._mgr()
        self.assertTrue(mgr.can_scale("cpu"))
        self.assertTrue(mgr.can_scale("cpu"))
        self.assertTrue(mgr.can_scale("ram"))

    def test_cooldown_defaults_to_cpu_resource(self):
        mgr = self._mgr()
        self.assertTrue(mgr.scale_cpu("up"))
        self.assertFalse(mgr.can_scale())          # legacy no-arg call → "cpu"
        self.assertTrue(mgr.can_scale("ram"))

    def test_zero_cooldown_allows_consecutive_scaling(self):
        mgr = self._mgr(cooldown=0)
        self.assertTrue(mgr.scale_cpu("up"))
        self.assertTrue(mgr.scale_cpu("up"))


# ---------------------------------------------------------------------------
# 3. Managers survive across polling cycles
# ---------------------------------------------------------------------------

class TestVMManagerReuse(unittest.TestCase):

    def _autoscaler(self, config=None):
        with patch.object(VMAutoscaler, "__init__", lambda s, *a, **kw: None):
            a = VMAutoscaler.__new__(VMAutoscaler)
        a.config = config or {"auto_configure_hotplug": False, "scale_cooldown": 300}
        a.logger = MagicMock()
        a.notification_manager = MagicMock()
        a.billing_tracker = None
        a._vm_managers = {}
        return a

    def test_same_manager_returned_for_the_same_vm(self):
        a = self._autoscaler()
        first = a._get_vm_manager(make_ssh(), "101")
        second = a._get_vm_manager(make_ssh(), "101")
        self.assertIs(first, second)

    def test_manager_is_rebound_to_the_current_ssh_client(self):
        a = self._autoscaler()
        first_ssh = make_ssh()
        second_ssh = make_ssh()
        manager = a._get_vm_manager(first_ssh, "101")
        a._get_vm_manager(second_ssh, "101")
        self.assertIs(manager.ssh_client, second_ssh)

    def test_distinct_vms_get_distinct_managers(self):
        a = self._autoscaler()
        self.assertIsNot(
            a._get_vm_manager(make_ssh(), "101"),
            a._get_vm_manager(make_ssh(), "102"),
        )

    def test_int_and_str_vm_ids_map_to_one_manager(self):
        a = self._autoscaler()
        self.assertIs(
            a._get_vm_manager(make_ssh(), 101),
            a._get_vm_manager(make_ssh(), "101"),
        )

    def test_cooldown_survives_across_cycles(self):
        """The whole point of reuse: scale_cooldown must outlive one cycle."""
        a = self._autoscaler({
            "auto_configure_hotplug": False,
            "scale_cooldown": 300,
            "scaling_limits": {"min_cores": 1, "max_cores": 8},
        })
        manager = a._get_vm_manager(make_ssh(), "101")
        self.assertTrue(manager.scale_cpu("up"))

        # Next polling cycle: new SSH connection, same manager.
        manager_next_cycle = a._get_vm_manager(make_ssh(), "101")
        self.assertFalse(manager_next_cycle.scale_cpu("up"))

    def test_hotplug_autoconfiguration_runs_once_per_vm(self):
        a = self._autoscaler({"auto_configure_hotplug": True, "scale_cooldown": 0})
        ssh = MagicMock()
        ssh.execute_command.return_value = ("cores: 4\nmemory: 8192", "", 0)

        a._get_vm_manager(ssh, "101")
        calls_after_first = len(ssh.execute_command.call_args_list)
        a._get_vm_manager(ssh, "101")

        self.assertEqual(len(ssh.execute_command.call_args_list), calls_after_first)


if __name__ == "__main__":
    unittest.main()
