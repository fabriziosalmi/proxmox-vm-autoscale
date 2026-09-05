"""
Regression tests for three defects fixed together.

1. `virtual_machines[].thresholds` appeared in the example config but nothing
   read it; every VM used the global `scaling_thresholds`.
2. SSH used `AutoAddPolicy` with no known_hosts file loaded or saved, so every
   connection accepted whatever key it was offered and remembered nothing.
3. Billing never recorded VM state changes, never generated a report, billed
   downtime as uptime, and billed a VM that had not changed spec during the
   period as zero.
"""

import json
import logging
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import paramiko

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from metrics import build_registry
from autoscale import VMAutoscaler
from billing_tracker import BillingTracker, SpecChangeRecord, StateChangeRecord
from ssh_utils import SSHClient


def make_logger():
    logger = logging.getLogger("test")
    logger.addHandler(logging.NullHandler())
    return logger


def bare_autoscaler(config):
    with patch.object(VMAutoscaler, "__init__", lambda s, *a, **kw: None):
        a = VMAutoscaler.__new__(VMAutoscaler)
    a.config = config
    a.logger = make_logger()
    a.notification_manager = MagicMock()
    a.billing_tracker = None
    a._vm_managers = {}
    a._vm_states = {}
    a.dry_run = False
    a.metrics = build_registry()
    return a


GLOBAL_THRESHOLDS = {
    "cpu": {"high": 80, "low": 20},
    "ram": {"high": 85, "low": 25},
}


# ---------------------------------------------------------------------------
# 1. Per-VM thresholds
# ---------------------------------------------------------------------------

class TestPerVMThresholds(unittest.TestCase):

    def _autoscaler(self):
        return bare_autoscaler({"scaling_thresholds": GLOBAL_THRESHOLDS})

    def test_falls_back_to_the_global_thresholds(self):
        a = self._autoscaler()
        self.assertEqual(a._thresholds_for({"vm_id": 101}, "cpu"), {"high": 80, "low": 20})
        self.assertEqual(a._thresholds_for({"vm_id": 101}, "ram"), {"high": 85, "low": 25})

    def test_flat_override_shape_from_the_example_config(self):
        a = self._autoscaler()
        vm = {"vm_id": 101, "thresholds": {"cpu_high": 95, "cpu_low": 40,
                                           "ram_high": 70, "ram_low": 10}}
        self.assertEqual(a._thresholds_for(vm, "cpu"), {"high": 95, "low": 40})
        self.assertEqual(a._thresholds_for(vm, "ram"), {"high": 70, "low": 10})

    def test_nested_override_shape(self):
        a = self._autoscaler()
        vm = {"vm_id": 101, "thresholds": {"cpu": {"high": 95, "low": 40}}}
        self.assertEqual(a._thresholds_for(vm, "cpu"), {"high": 95, "low": 40})

    def test_a_partial_override_keeps_the_global_value_for_the_other_bound(self):
        a = self._autoscaler()
        vm = {"vm_id": 101, "thresholds": {"cpu_high": 95}}
        self.assertEqual(a._thresholds_for(vm, "cpu"), {"high": 95, "low": 20})

    def test_overrides_do_not_leak_between_vms(self):
        a = self._autoscaler()
        a._thresholds_for({"vm_id": 101, "thresholds": {"cpu_high": 95}}, "cpu")
        self.assertEqual(a._thresholds_for({"vm_id": 102}, "cpu"), {"high": 80, "low": 20})

    def test_inverted_bounds_fall_back_to_global(self):
        a = self._autoscaler()
        vm = {"vm_id": 101, "thresholds": {"cpu_high": 10, "cpu_low": 90}}
        self.assertEqual(a._thresholds_for(vm, "cpu"), {"high": 80, "low": 20})

    def test_non_mapping_thresholds_are_ignored(self):
        a = self._autoscaler()
        self.assertEqual(
            a._thresholds_for({"vm_id": 101, "thresholds": "nonsense"}, "cpu"),
            {"high": 80, "low": 20},
        )

    def test_handler_uses_the_per_vm_threshold(self):
        """55% is idle for a VM whose low mark is 60, and normal for one at 20."""
        a = self._autoscaler()

        strict = MagicMock()
        a._handle_cpu_scaling(strict, 101, 55.0, {"high": 90, "low": 60})
        strict.scale_cpu.assert_called_once_with("down")

        relaxed = MagicMock()
        a._handle_cpu_scaling(relaxed, 102, 55.0, {"high": 80, "low": 20})
        relaxed.scale_cpu.assert_not_called()


# ---------------------------------------------------------------------------
# 2. Host key verification
# ---------------------------------------------------------------------------

class TestHostKeyPolicy(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.known_hosts = os.path.join(self.tmp.name, "known_hosts")

    def client(self, policy="accept-new"):
        return SSHClient(host="10.0.0.11", user="root", password="x",
                         host_key_policy=policy, known_hosts=self.known_hosts)

    def test_rejects_an_unknown_policy_name(self):
        with self.assertRaises(ValueError):
            SSHClient(host="h", user="root", password="x", host_key_policy="whatever")

    def test_accept_new_loads_and_creates_the_known_hosts_file(self):
        fake = MagicMock()
        self.client("accept-new")._apply_host_key_policy(fake)
        self.assertTrue(os.path.exists(self.known_hosts))
        fake.load_host_keys.assert_called_once_with(self.known_hosts)
        policy = fake.set_missing_host_key_policy.call_args.args[0]
        self.assertIsInstance(policy, paramiko.AutoAddPolicy)

    def test_known_hosts_file_is_not_world_readable(self):
        self.client("accept-new")._apply_host_key_policy(MagicMock())
        self.assertEqual(os.stat(self.known_hosts).st_mode & 0o077, 0)

    def test_strict_uses_reject_policy(self):
        fake = MagicMock()
        self.client("strict")._apply_host_key_policy(fake)
        policy = fake.set_missing_host_key_policy.call_args.args[0]
        self.assertIsInstance(policy, paramiko.RejectPolicy)
        fake.load_host_keys.assert_called_once_with(self.known_hosts)

    def test_auto_keeps_the_old_behaviour_and_loads_nothing(self):
        fake = MagicMock()
        self.client("auto")._apply_host_key_policy(fake)
        policy = fake.set_missing_host_key_policy.call_args.args[0]
        self.assertIsInstance(policy, paramiko.AutoAddPolicy)
        fake.load_host_keys.assert_not_called()

    def test_default_policy_is_accept_new_not_blind_acceptance(self):
        client = SSHClient(host="10.0.0.11", user="root", password="x")
        self.assertEqual(client.host_key_policy, "accept-new")

    def test_unwritable_known_hosts_degrades_instead_of_crashing(self):
        client = SSHClient(host="10.0.0.11", user="root", password="x",
                           known_hosts="/proc/definitely/not/writable/known_hosts")
        fake = MagicMock()
        client._apply_host_key_policy(fake)   # must not raise
        fake.set_missing_host_key_policy.assert_called_once()

    def test_a_changed_host_key_is_fatal_and_not_retried(self):
        client = self.client("accept-new")
        client.max_retries = 5
        bad = paramiko.ssh_exception.BadHostKeyException(
            "10.0.0.11", MagicMock(), MagicMock()
        )
        with patch("paramiko.SSHClient") as fake_cls:
            fake_cls.return_value.connect.side_effect = bad
            with self.assertRaises(paramiko.ssh_exception.BadHostKeyException):
                client.connect()
            # One attempt only: a swapped host key is not a transient fault.
            self.assertEqual(fake_cls.return_value.connect.call_count, 1)


# ---------------------------------------------------------------------------
# 3. Billing
# ---------------------------------------------------------------------------

class BillingTestCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def tracker(self, **billing):
        config = {"billing": {"enabled": True, "csv_output_dir": self.tmp.name,
                              "cost_per_cpu_core_per_hour": 1.0,
                              "cost_per_gb_ram_per_hour": 0.0, **billing}}
        return BillingTracker(config, make_logger())


class TestDowntimeIsNotBilled(BillingTestCase):

    def test_a_vm_stopped_for_half_the_period_is_billed_half(self):
        t = self.tracker()
        start = datetime(2026, 1, 1)
        end = start + timedelta(hours=10)

        t.record_spec_change("101", cpu_cores=2, ram_mb=0, timestamp=start)
        t.record_vm_state_change("101", "started", timestamp=start)
        t.record_vm_state_change("101", "stopped", timestamp=start + timedelta(hours=5))

        report = t.calculate_billing_period("101", start, end)
        # 2 cores x 1.0/hour x 5 up hours
        self.assertAlmostEqual(report.total_cost, 10.0)
        self.assertAlmostEqual(report.total_uptime_hours, 5.0)
        self.assertAlmostEqual(report.total_downtime_hours, 5.0)

    def test_a_vm_up_the_whole_period_is_billed_in_full(self):
        t = self.tracker()
        start = datetime(2026, 1, 1)
        end = start + timedelta(hours=10)

        t.record_spec_change("101", cpu_cores=2, ram_mb=0, timestamp=start)
        t.record_vm_state_change("101", "started", timestamp=start)

        report = t.calculate_billing_period("101", start, end)
        self.assertAlmostEqual(report.total_cost, 20.0)
        self.assertAlmostEqual(report.uptime_percentage, 100.0)

    def test_a_vm_never_started_is_billed_nothing(self):
        t = self.tracker()
        start = datetime(2026, 1, 1)
        end = start + timedelta(hours=10)

        t.record_spec_change("101", cpu_cores=8, ram_mb=0, timestamp=start)
        t.record_vm_state_change("101", "stopped", timestamp=start)

        report = t.calculate_billing_period("101", start, end)
        self.assertAlmostEqual(report.total_cost, 0.0)
        self.assertAlmostEqual(report.total_uptime_hours, 0.0)


class TestPeriodCarriesStateIn(BillingTestCase):

    def test_a_vm_that_never_changed_spec_is_still_billed(self):
        """Spec changes are events; a stable VM produces none inside a period."""
        t = self.tracker()
        t.record_spec_change("101", cpu_cores=4, ram_mb=0,
                             timestamp=datetime(2025, 12, 1))

        start = datetime(2026, 1, 1)
        end = start + timedelta(hours=10)
        report = t.calculate_billing_period("101", start, end)

        self.assertAlmostEqual(report.total_cost, 40.0)
        self.assertEqual(report.min_cpu_cores, 4)
        self.assertEqual(report.max_cpu_cores, 4)

    def test_a_running_state_from_before_the_period_carries_in(self):
        t = self.tracker()
        t.record_spec_change("101", cpu_cores=1, ram_mb=0, timestamp=datetime(2025, 12, 1))
        t.record_vm_state_change("101", "started", timestamp=datetime(2025, 12, 1))

        start = datetime(2026, 1, 1)
        end = start + timedelta(hours=10)
        t.record_vm_state_change("101", "stopped", timestamp=start + timedelta(hours=8))

        report = t.calculate_billing_period("101", start, end)
        # Up for the first 8 hours, not down for them.
        self.assertAlmostEqual(report.total_uptime_hours, 8.0)
        self.assertAlmostEqual(report.total_cost, 8.0)

    def test_the_report_lists_only_events_inside_the_period(self):
        t = self.tracker()
        t.record_spec_change("101", cpu_cores=1, ram_mb=0, timestamp=datetime(2025, 12, 1))
        start = datetime(2026, 1, 1)
        t.record_spec_change("101", cpu_cores=2, ram_mb=0,
                             timestamp=start + timedelta(hours=1))

        report = t.calculate_billing_period("101", start, start + timedelta(hours=10))
        self.assertEqual(len(report.spec_changes), 1)
        self.assertEqual(report.spec_changes[0]["cpu_cores"], 2)


class TestReportSchedule(BillingTestCase):

    def test_the_first_check_starts_the_clock_without_reporting(self):
        t = self.tracker(billing_period_days=30)
        self.assertIsNone(t.get_last_report_time())
        self.assertFalse(t.is_period_due())
        self.assertIsNotNone(t.get_last_report_time())

    def test_not_due_before_the_period_elapses(self):
        t = self.tracker(billing_period_days=30)
        t.set_last_report_time(datetime.now() - timedelta(days=29))
        self.assertFalse(t.is_period_due())

    def test_due_once_the_period_has_elapsed(self):
        t = self.tracker(billing_period_days=30)
        t.set_last_report_time(datetime.now() - timedelta(days=31))
        self.assertTrue(t.is_period_due())

    def test_the_clock_survives_a_restart(self):
        t = self.tracker(billing_period_days=30)
        stamp = datetime.now() - timedelta(days=10)
        t.set_last_report_time(stamp)

        reloaded = self.tracker(billing_period_days=30)
        self.assertEqual(reloaded.get_last_report_time(), stamp)

    def test_the_timestamp_is_persisted_to_the_data_file(self):
        t = self.tracker()
        t.set_last_report_time(datetime(2026, 1, 1, 12, 0))
        with open(os.path.join(self.tmp.name, "billing_data.json")) as fh:
            self.assertEqual(json.load(fh)["last_report_time"], "2026-01-01T12:00:00")


class TestAutoscalerDrivesBilling(BillingTestCase):

    def _autoscaler(self, tracker):
        a = bare_autoscaler({
            "scaling_thresholds": GLOBAL_THRESHOLDS,
            "virtual_machines": [{"vm_id": "101"}, {"vm_id": "102"}],
        })
        a.billing_tracker = tracker
        return a

    def test_state_transitions_are_recorded_once_each(self):
        t = MagicMock()
        a = self._autoscaler(t)

        a._record_vm_state("101", True)
        a._record_vm_state("101", True)     # unchanged: no second record
        a._record_vm_state("101", False)

        self.assertEqual(
            [c.args for c in t.record_vm_state_change.call_args_list],
            [("101", "started"), ("101", "stopped")],
        )

    def test_nothing_is_recorded_when_billing_is_disabled(self):
        a = bare_autoscaler({"scaling_thresholds": GLOBAL_THRESHOLDS})
        a._record_vm_state("101", True)     # must not raise

    def test_reports_are_generated_when_the_period_is_due(self):
        t = MagicMock()
        t.is_period_due.return_value = True
        t.generate_period_report.return_value = MagicMock(
            vm_id="101", total_cost=1.0, total_uptime_hours=1.0
        )
        a = self._autoscaler(t)

        a._maybe_generate_billing_reports()

        self.assertEqual(t.generate_period_report.call_count, 2)
        t.set_last_report_time.assert_called_once()

    def test_no_reports_before_the_period_is_due(self):
        t = MagicMock()
        t.is_period_due.return_value = False
        a = self._autoscaler(t)

        a._maybe_generate_billing_reports()

        t.generate_period_report.assert_not_called()
        t.set_last_report_time.assert_not_called()

    def test_one_failing_vm_does_not_stop_the_others(self):
        t = MagicMock()
        t.is_period_due.return_value = True
        t.generate_period_report.side_effect = [
            RuntimeError("disk full"),
            MagicMock(vm_id="102", total_cost=1.0, total_uptime_hours=1.0),
        ]
        a = self._autoscaler(t)

        a._maybe_generate_billing_reports()

        self.assertEqual(t.generate_period_report.call_count, 2)
        t.set_last_report_time.assert_called_once()


if __name__ == "__main__":
    unittest.main()
