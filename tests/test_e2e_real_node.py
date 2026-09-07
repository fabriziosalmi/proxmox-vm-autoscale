"""
End-to-end tests against a real Proxmox node.

Skipped unless `VMA_E2E_HOST` is set — see `tests/e2e_support.py` for the
environment and the safety guards. They are not part of CI and never will be:
CI has no hypervisor to talk to.

Every test here asserts something a mock could not have told us. That is the
entry criterion, and it is not decoration: a single session against real
hardware found three defects while 303 mocked tests passed, because every
fixture encoded the same assumptions the code did. Two of those defects were in
the scaling path — a scale-up that halved a guest, and a benign warning on
stderr that made the whole service inert.

Where a test could be written against a fake, it belongs in the unit suite
instead. Duplicating unit coverage here buys nothing and costs a real VM.
"""

import json
import os
import sys
import unittest

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from e2e_support import BASELINE, E2ETestCase, wait_for
from host_resource_checker import HostResourceChecker
from vm_manager import CommandFailed, VMResourceManager

pytestmark = pytest.mark.e2e


def manager_config(**overrides):
    config = {
        "auto_configure_hotplug": False,
        "scale_cooldown": 0,
        "scaling_limits": {
            "min_cores": 1, "max_cores": 8,
            "min_ram_mb": 1024, "max_ram_mb": 8192,
        },
    }
    config.update(overrides)
    return config


# ---------------------------------------------------------------------------
# The protocol contracts the code depends on
# ---------------------------------------------------------------------------

class TestProxmoxContracts(E2ETestCase):
    """What the node actually returns, against what the parsers assume."""

    def test_cluster_resources_carries_the_fields_the_parser_reads(self):
        payload = json.loads(
            self.run_command("pvesh get /cluster/resources --output-format json"))
        rows = [r for r in payload
                if r.get("type") == "qemu" and str(r.get("vmid")) == self.vmid]

        self.assertEqual(len(rows), 1, f"VM {self.vmid} not found exactly once")
        row = rows[0]
        for field in ("type", "vmid", "cpu", "mem", "maxmem"):
            self.assertIn(field, row, f"{field} absent from cluster resources")

        self.assertIsInstance(row["vmid"], int)
        self.assertGreater(row["maxmem"], 0)
        self.assertLessEqual(float(row["cpu"]), 1.0,
                             "cpu is documented as a fraction, not a percentage")

    def test_node_status_carries_the_fields_the_host_gate_reads(self):
        payload = json.loads(
            self.run_command(
                "pvesh get /nodes/$(hostname)/status --output-format json"))
        self.assertIn("cpu", payload)
        self.assertIn("memory", payload)
        for field in ("used", "total"):
            self.assertIn(field, payload["memory"])
        self.assertGreater(payload["memory"]["total"], 0)

    def test_a_successful_pvesh_call_may_still_write_to_stderr(self):
        """The v1.7.1 defect: stderr was treated as failure, ignoring the status.

        Not every node reproduces this — it needs a stale ACL entry — so the
        test asserts the invariant that matters rather than the symptom: exit
        status is what decides, whatever stderr contains.
        """
        result = self.client.execute_command(
            "pvesh get /nodes/$(hostname)/status --output-format json")
        _output, error, status = result
        self.assertEqual(status, 0)
        if error.strip():
            self.assertNotIn("Traceback", error,
                             "stderr on a successful call should be a warning")

    def test_qm_config_is_parseable_by_the_production_reader(self):
        manager = VMResourceManager(self.client, self.vmid, manager_config())
        manager.invalidate_cache()
        self.assertEqual(manager._get_current_cores(), BASELINE["cores"])
        self.assertEqual(manager._get_current_vcpus(), BASELINE["vcpus"])
        self.assertEqual(manager._get_current_ram(), BASELINE["memory"])

    def test_an_absent_vcpus_line_means_every_core_is_online(self):
        """The other v1.7.1 defect, verified against a real guest.

        Reading the absence as 1 turned a scale-up into `qm set -vcpus 2` on a
        four-core guest.
        """
        self.run_command(f"qm set {self.vmid} --delete vcpus")
        manager = VMResourceManager(self.client, self.vmid, manager_config())
        manager.invalidate_cache()

        self.assertNotIn("vcpus", self.config_of())
        self.assertEqual(manager._get_current_vcpus(), BASELINE["cores"])


# ---------------------------------------------------------------------------
# Reading a running guest
# ---------------------------------------------------------------------------

class TestReadingARunningGuest(E2ETestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ensure_guest_running()

    def _manager(self, **overrides):
        manager = VMResourceManager(self.client, self.vmid,
                                    manager_config(**overrides))
        manager.invalidate_cache()
        return manager

    def test_the_guest_is_seen_as_running(self):
        self.assertTrue(self._manager().is_vm_running())

    def test_usage_is_read_and_is_plausible(self):
        cpu, ram = self._manager().get_resource_usage()
        self.assertIsNotNone(cpu, "CPU unreadable on a running guest")
        self.assertIsNotNone(ram, "RAM unreadable on a running guest")
        self.assertGreaterEqual(cpu, 0.0)
        self.assertLessEqual(cpu, 100.0)
        self.assertGreater(ram, 0.0, "a running guest uses some memory")
        self.assertLessEqual(ram, 100.0)

    def _pvesh_ram_percent(self):
        payload = json.loads(
            self.run_command("pvesh get /cluster/resources --output-format json"))
        row = next(r for r in payload
                   if r.get("type") == "qemu" and str(r.get("vmid")) == self.vmid)
        return row["mem"] / row["maxmem"] * 100

    def test_the_reading_agrees_with_pvesh(self):
        """The parser and the source of truth must not disagree.

        The value moves: `pvestatd` resamples every few seconds, and a guest
        that has just booted climbs steeply. Comparing one sample against
        another taken a moment later once differed by 12 percentage points
        with nothing wrong. So the reading is bracketed by a sample either
        side of it and must fall inside — which is what "agrees" can mean
        about a quantity that does not hold still.
        """
        before = self._pvesh_ram_percent()
        _cpu, ram = self._manager().get_resource_usage()
        after = self._pvesh_ram_percent()

        self.assertIsNotNone(ram)
        low, high = min(before, after), max(before, after)
        self.assertGreaterEqual(ram, low - 1.0,
                                f"read {ram}, node reported {before}..{after}")
        self.assertLessEqual(ram, high + 1.0,
                             f"read {ram}, node reported {before}..{after}")

    def test_a_vmid_that_does_not_exist_is_not_reported_as_running(self):
        manager = VMResourceManager(self.client, "999999", manager_config())
        self.assertFalse(manager.is_vm_running())

    def test_the_host_gate_passes_with_generous_limits(self):
        checker = HostResourceChecker(self.client)
        self.assertTrue(checker.check_host_resources(99.9, 99.9))
        self.assertIsNotNone(checker.last_cpu_percent)
        self.assertIsNotNone(checker.last_ram_percent)

    def test_the_host_gate_blocks_with_an_impossible_ceiling(self):
        checker = HostResourceChecker(self.client)
        self.assertFalse(checker.check_host_resources(99.9, 0.001))


# ---------------------------------------------------------------------------
# The mutation path — what dry-run can never prove
# ---------------------------------------------------------------------------

class TestScalingReachesTheGuest(E2ETestCase):
    """Not "the command was accepted" — "the running guest changed"."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ensure_guest_running()

    def _manager(self, **overrides):
        manager = VMResourceManager(self.client, self.vmid,
                                    manager_config(**overrides))
        manager.invalidate_cache()
        return manager

    def _online_cpus(self):
        return int(self.status_of().get("cpus", 0))

    def test_a_scale_up_adds_one_online_vcpu_to_the_running_guest(self):
        before = self._online_cpus()
        self.assertEqual(before, BASELINE["vcpus"])

        self.assertTrue(self._manager().scale_cpu("up"))

        self.assertEqual(int(self.config_of()["vcpus"]), before + 1)
        after = wait_for(lambda: self._online_cpus() == before + 1)
        self.assertTrue(after, f"QEMU still reports {self._online_cpus()} vCPUs")

    def test_a_scale_up_never_reduces_the_guest(self):
        """The v1.7.1 defect stated as an invariant, on real hardware."""
        before = self._online_cpus()
        self._manager().scale_cpu("up")
        self.assertGreaterEqual(self._online_cpus(), before)

    def test_a_scale_down_removes_one_online_vcpu(self):
        before = self._online_cpus()
        self.assertTrue(self._manager().scale_cpu("down"))
        self.assertEqual(int(self.config_of()["vcpus"]), before - 1)

    def test_scaling_moves_one_step_at_a_time(self):
        before = int(self.config_of()["vcpus"])
        self._manager().scale_cpu("up")
        self.assertEqual(int(self.config_of()["vcpus"]), before + 1)

    def test_growing_ram_past_the_ceiling_raises_both(self):
        """The hypervisor refuses a balloon above `memory`; growing must not."""
        config = self.config_of()
        self.assertEqual(int(config["balloon"]), int(config["memory"]),
                         "baseline should sit at the ceiling")
        before = int(config["balloon"])

        self.assertTrue(self._manager().scale_ram("up"))

        after = self.config_of()
        self.assertEqual(int(after["memory"]), before + 512)
        self.assertEqual(int(after["balloon"]), before + 512)

    def test_growing_ram_within_the_ceiling_only_moves_the_balloon(self):
        self.run_command(f"qm set {self.vmid} --balloon 1024")
        ceiling = int(self.config_of()["memory"])

        self.assertTrue(self._manager().scale_ram("up"))

        after = self.config_of()
        self.assertEqual(int(after["balloon"]), 1536)
        self.assertEqual(int(after["memory"]), ceiling,
                         "the ceiling was raised when it did not need to be")

    def test_shrinking_ram_moves_the_balloon_and_leaves_the_ceiling(self):
        ceiling = int(self.config_of()["memory"])
        before = int(self.config_of()["balloon"])

        self.assertTrue(self._manager().scale_ram("down"))

        after = self.config_of()
        self.assertEqual(int(after["balloon"]), before - 512)
        self.assertEqual(int(after["memory"]), ceiling)

    def test_a_balloon_is_never_set_above_the_ceiling(self):
        """Asserted against the hypervisor, which is the thing that refuses it."""
        manager = self._manager()
        for _ in range(3):
            manager.scale_ram("up")
            manager.invalidate_cache()
            config = self.config_of()
            self.assertLessEqual(int(config["balloon"]), int(config["memory"]))

    def test_the_ceiling_is_respected_against_a_real_guest(self):
        manager = self._manager(
            scaling_limits={"min_cores": 1, "max_cores": BASELINE["cores"],
                            "min_ram_mb": 1024, "max_ram_mb": 8192})
        for _ in range(BASELINE["cores"] - BASELINE["vcpus"]):
            manager.scale_cpu("up")
            manager.invalidate_cache()
        self.assertFalse(manager.scale_cpu("up"),
                         "scaled past max_cores on a real guest")


class TestDryRunChangesNothing(E2ETestCase):
    """The safety property, asserted against the hypervisor rather than a log."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ensure_guest_running()

    def _manager(self):
        manager = VMResourceManager(self.client, self.vmid,
                                    manager_config(dry_run=True))
        manager.invalidate_cache()
        return manager

    def test_a_scale_up_leaves_the_configuration_untouched(self):
        before = dict(self.config_of())
        self._manager().scale_cpu("up")
        self.assertEqual(self.config_of()["vcpus"], before["vcpus"])
        self.assertEqual(self.config_of()["cores"], before["cores"])

    def test_a_ram_change_leaves_the_balloon_untouched(self):
        before = self.config_of()["balloon"]
        self._manager().scale_ram("up")
        self.assertEqual(self.config_of()["balloon"], before)

    def test_hotplug_autoconfiguration_changes_nothing(self):
        self.run_command(f"qm set {self.vmid} --delete hotplug")
        VMResourceManager(self.client, self.vmid,
                          manager_config(dry_run=True, auto_configure_hotplug=True))
        self.assertNotIn("hotplug", self.config_of())


# ---------------------------------------------------------------------------
# Failure detection
# ---------------------------------------------------------------------------

class TestFailuresAreDetected(E2ETestCase):
    """A command that genuinely fails on a real node must raise."""

    def _manager(self):
        manager = VMResourceManager(self.client, self.vmid, manager_config())
        manager.invalidate_cache()
        return manager

    def test_a_rejected_qm_set_raises_rather_than_reporting_success(self):
        manager = self._manager()
        with self.assertRaises(CommandFailed) as ctx:
            manager._run(f"qm set {self.vmid} --cores 0", mutating=True)
        self.assertIn("exit status", str(ctx.exception))

    def test_reading_a_vm_that_does_not_exist_raises(self):
        manager = VMResourceManager(self.client, "999999", manager_config())
        with self.assertRaises(CommandFailed):
            manager._get_current_cores()

    def test_a_failed_scale_does_not_consume_the_cooldown(self):
        manager = VMResourceManager(
            self.client, "999999", manager_config(scale_cooldown=300))
        with self.assertRaises(CommandFailed):
            manager.scale_cpu("up")
        self.assertTrue(manager.can_scale("cpu"))


# ---------------------------------------------------------------------------
# Efficiency, measured rather than argued
# ---------------------------------------------------------------------------

class TestWorkPerDecision(E2ETestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ensure_guest_running()

    def test_a_scaling_decision_does_not_refetch_qm_config(self):
        """It ran `qm config` three times per decision before the cache."""
        issued = []
        original = self.client.execute_command

        def recording(command, *args, **kwargs):
            issued.append(command)
            return original(command, *args, **kwargs)

        self.client.execute_command = recording
        self.addCleanup(setattr, self.client, "execute_command", original)

        manager = VMResourceManager(self.client, self.vmid, manager_config())
        manager.invalidate_cache()
        manager.scale_cpu("up")

        config_reads = [c for c in issued if c.startswith("qm config")]
        self.assertLessEqual(
            len(config_reads), 1,
            f"`qm config` was read {len(config_reads)} times in one decision",
        )


if __name__ == "__main__":
    unittest.main()
