"""
Unit tests for HostResourceChecker in host_resource_checker.py.

These tests verify:
- Normal operation: resources within limits
- CPU threshold exceeded
- RAM threshold exceeded
- RAM usage calculation uses 'used' field (not free+cached) - fix for issue #38
- Invalid/missing JSON fields
- JSON decode errors
- Command execution errors
- Bytes output handling
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from host_resource_checker import HostResourceChecker


def make_ssh(output="", error="", exit_status=0):
    """Helper: build a mock SSH client with a fixed response."""
    client = MagicMock()
    client.execute_command.return_value = (output, error, exit_status)
    return client


def proxmox_payload(cpu=0.1, total=64 * 1024 ** 3, used=10 * 1024 ** 3):
    """Return a JSON string mimicking pvesh node/status output."""
    return json.dumps({
        "cpu": cpu,
        "memory": {
            "total": total,
            "used": used,
        }
    })


class TestHostResourceCheckerWithinLimits(unittest.TestCase):
    """Resources well within thresholds — should return True."""

    def test_returns_true_when_within_limits(self):
        # 10% CPU, ~15.6% RAM (10 GiB used out of 64 GiB)
        ssh = make_ssh(output=proxmox_payload(cpu=0.10, total=64 * 1024 ** 3, used=10 * 1024 ** 3))
        checker = HostResourceChecker(ssh)
        self.assertTrue(checker.check_host_resources(80, 80))

    def test_returns_true_at_exact_cpu_threshold(self):
        """CPU exactly at the threshold should still pass (not strictly greater-than)."""
        ssh = make_ssh(output=proxmox_payload(cpu=0.80, total=1024, used=100))
        checker = HostResourceChecker(ssh)
        self.assertTrue(checker.check_host_resources(80, 80))

    def test_returns_true_at_exact_ram_threshold(self):
        """RAM exactly at the threshold should still pass."""
        ssh = make_ssh(output=proxmox_payload(cpu=0.10, total=1000, used=800))
        checker = HostResourceChecker(ssh)
        # RAM = 800/1000 * 100 = 80%
        self.assertTrue(checker.check_host_resources(80, 80))


class TestHostResourceCheckerThresholdExceeded(unittest.TestCase):
    """One or both resources exceed their thresholds — should return False."""

    def test_returns_false_when_cpu_exceeds_limit(self):
        ssh = make_ssh(output=proxmox_payload(cpu=0.95, total=64 * 1024 ** 3, used=10 * 1024 ** 3))
        checker = HostResourceChecker(ssh)
        self.assertFalse(checker.check_host_resources(80, 80))

    def test_returns_false_when_ram_exceeds_limit(self):
        # 95% RAM used
        total = 64 * 1024 ** 3
        used = int(total * 0.95)
        ssh = make_ssh(output=proxmox_payload(cpu=0.10, total=total, used=used))
        checker = HostResourceChecker(ssh)
        self.assertFalse(checker.check_host_resources(80, 80))

    def test_returns_false_when_both_exceed_limits(self):
        total = 64 * 1024 ** 3
        used = int(total * 0.95)
        ssh = make_ssh(output=proxmox_payload(cpu=0.95, total=total, used=used))
        checker = HostResourceChecker(ssh)
        self.assertFalse(checker.check_host_resources(80, 80))


class TestHostResourceCheckerRAMCalculation(unittest.TestCase):
    """
    Verify that RAM usage is calculated from 'used' only (issue #38).
    Proxmox reports buff/cache separately; the WebUI shows used/total
    which excludes reclaimable cache — our calculation must match that.
    """

    def test_ram_uses_used_field_not_free_cached(self):
        """
        64 GiB total, 42 GiB used (as reported by pvesh, matching WebUI).
        Expected RAM% ≈ 65.6% — well under an 80% threshold.
        A wrong implementation using (total - free) / total with free=6.5 GiB
        would give ~89.8% and incorrectly block scaling.
        """
        total = 64 * 1024 ** 3        # 64 GiB in bytes
        used  = 42 * 1024 ** 3        # 42 GiB in bytes (WebUI value)
        ssh = make_ssh(output=proxmox_payload(cpu=0.0, total=total, used=used))
        checker = HostResourceChecker(ssh)
        result = checker.check_host_resources(80, 80)
        self.assertTrue(result, "Should pass: 42/64 GiB ≈ 65.6%, under 80% threshold")

    def test_ram_percentage_precision(self):
        """Used=500, total=1000 → exactly 50%."""
        ssh = make_ssh(output=proxmox_payload(cpu=0.0, total=1000, used=500))
        checker = HostResourceChecker(ssh)
        self.assertTrue(checker.check_host_resources(80, 60))   # 50% < 60% → pass

    def test_ram_percentage_triggers_threshold(self):
        """Used=900, total=1000 → 90%, exceeds 80% threshold."""
        ssh = make_ssh(output=proxmox_payload(cpu=0.0, total=1000, used=900))
        checker = HostResourceChecker(ssh)
        self.assertFalse(checker.check_host_resources(80, 80))


class TestHostResourceCheckerEdgeCases(unittest.TestCase):
    """Error handling and edge cases."""

    def test_raises_on_command_error(self):
        ssh = make_ssh(output="", error="permission denied", exit_status=1)
        checker = HostResourceChecker(ssh)
        with self.assertRaises(Exception):
            checker.check_host_resources(80, 80)

    def test_raises_on_invalid_json(self):
        ssh = make_ssh(output="this is not json")
        checker = HostResourceChecker(ssh)
        with self.assertRaises(Exception):
            checker.check_host_resources(80, 80)

    def test_raises_on_missing_cpu_key(self):
        payload = json.dumps({"memory": {"total": 1000, "used": 500}})
        ssh = make_ssh(output=payload)
        checker = HostResourceChecker(ssh)
        with self.assertRaises(KeyError):
            checker.check_host_resources(80, 80)

    def test_raises_on_missing_memory_key(self):
        payload = json.dumps({"cpu": 0.5})
        ssh = make_ssh(output=payload)
        checker = HostResourceChecker(ssh)
        with self.assertRaises(KeyError):
            checker.check_host_resources(80, 80)

    def test_handles_bytes_output(self):
        """Output returned as bytes should be decoded transparently."""
        payload_bytes = proxmox_payload(cpu=0.10, total=1000, used=100).encode("utf-8")
        ssh = make_ssh(output=payload_bytes)
        checker = HostResourceChecker(ssh)
        self.assertTrue(checker.check_host_resources(80, 80))

    def test_handles_output_with_surrounding_whitespace(self):
        """pvesh sometimes pads output with newlines."""
        padded = "\n\n" + proxmox_payload(cpu=0.10, total=1000, used=100) + "\n"
        ssh = make_ssh(output=padded)
        checker = HostResourceChecker(ssh)
        self.assertTrue(checker.check_host_resources(80, 80))


if __name__ == "__main__":
    unittest.main()
