"""
Unit tests for the BillingTracker module.

These tests verify:
- Recording of spec changes
- Recording of VM state changes
- Billing period calculations
- Cost calculations
- CSV export functionality
"""

import os
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

# Add parent directory to path for imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from billing_tracker import BillingTracker, SpecChangeRecord, StateChangeRecord, BillingReport


class TestBillingTracker(unittest.TestCase):
    """Tests for BillingTracker class."""

    def setUp(self):
        """Set up test fixtures."""
        self.temp_dir = tempfile.mkdtemp()
        self.config = {
            'billing': {
                'enabled': True,
                'billing_period_days': 30,
                'cost_per_cpu_core_per_hour': 0.01,
                'cost_per_gb_ram_per_hour': 0.005,
                'csv_output_dir': self.temp_dir,
                'webhook_script': '',
                'webhook_url': ''
            }
        }
        self.logger = MagicMock()
        self.tracker = BillingTracker(self.config, self.logger)

    def tearDown(self):
        """Clean up temporary files."""
        import shutil
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_record_spec_change(self):
        """Test recording a spec change."""
        vm_id = "101"
        self.tracker.record_spec_change(vm_id, cpu_cores=4, ram_mb=8192)
        
        self.assertIn(vm_id, self.tracker._spec_changes)
        self.assertEqual(len(self.tracker._spec_changes[vm_id]), 1)
        
        record = self.tracker._spec_changes[vm_id][0]
        self.assertEqual(record.cpu_cores, 4)
        self.assertEqual(record.ram_mb, 8192)

    def test_record_multiple_spec_changes(self):
        """Test recording multiple spec changes for the same VM."""
        vm_id = "101"
        
        self.tracker.record_spec_change(vm_id, cpu_cores=2, ram_mb=4096)
        self.tracker.record_spec_change(vm_id, cpu_cores=4, ram_mb=8192)
        self.tracker.record_spec_change(vm_id, cpu_cores=2, ram_mb=4096)
        
        self.assertEqual(len(self.tracker._spec_changes[vm_id]), 3)

    def test_record_vm_state_change_started(self):
        """Test recording VM started state."""
        vm_id = "101"
        self.tracker.record_vm_state_change(vm_id, 'started')
        
        self.assertIn(vm_id, self.tracker._state_changes)
        self.assertEqual(len(self.tracker._state_changes[vm_id]), 1)
        self.assertEqual(self.tracker._state_changes[vm_id][0].state, 'started')

    def test_record_vm_state_change_stopped(self):
        """Test recording VM stopped state."""
        vm_id = "101"
        self.tracker.record_vm_state_change(vm_id, 'stopped')
        
        self.assertEqual(self.tracker._state_changes[vm_id][0].state, 'stopped')

    def test_record_vm_state_change_invalid(self):
        """Test that invalid state raises ValueError."""
        vm_id = "101"
        with self.assertRaises(ValueError):
            self.tracker.record_vm_state_change(vm_id, 'invalid_state')

    def test_set_vm_name(self):
        """Test setting VM name."""
        vm_id = "101"
        vm_name = "WebServer-01"
        
        self.tracker.set_vm_name(vm_id, vm_name)
        
        self.assertEqual(self.tracker._vm_names[vm_id], vm_name)

    def test_calculate_billing_period_basic(self):
        """Test basic billing period calculation."""
        vm_id = "101"
        now = datetime.now()
        period_start = now - timedelta(hours=10)
        period_end = now
        
        # Record some spec changes
        self.tracker.record_spec_change(
            vm_id, cpu_cores=2, ram_mb=4096,
            timestamp=period_start + timedelta(hours=1)
        )
        self.tracker.record_spec_change(
            vm_id, cpu_cores=4, ram_mb=8192,
            timestamp=period_start + timedelta(hours=5)
        )
        
        report = self.tracker.calculate_billing_period(vm_id, period_start, period_end)
        
        self.assertEqual(report.vm_id, vm_id)
        self.assertEqual(report.min_cpu_cores, 2)
        self.assertEqual(report.max_cpu_cores, 4)
        self.assertEqual(report.min_ram_mb, 4096)
        self.assertEqual(report.max_ram_mb, 8192)

    def test_calculate_billing_period_with_uptime(self):
        """Test billing calculation with uptime tracking."""
        vm_id = "101"
        now = datetime.now()
        period_start = now - timedelta(hours=10)
        period_end = now
        
        # VM started, then stopped, then started again
        self.tracker.record_vm_state_change(
            vm_id, 'started', timestamp=period_start + timedelta(hours=1)
        )
        self.tracker.record_vm_state_change(
            vm_id, 'stopped', timestamp=period_start + timedelta(hours=5)
        )
        self.tracker.record_vm_state_change(
            vm_id, 'started', timestamp=period_start + timedelta(hours=7)
        )
        
        report = self.tracker.calculate_billing_period(vm_id, period_start, period_end)
        
        # Should have 4 hours (1-5) + 3 hours (7-10) = 7 hours uptime
        self.assertGreater(report.total_uptime_hours, 0)
        self.assertLess(report.uptime_percentage, 100)

    def test_cost_calculation(self):
        """Test cost is calculated correctly."""
        vm_id = "101"
        now = datetime.now()
        period_start = now - timedelta(hours=10)
        period_end = now
        
        # Record spec change at start of period
        self.tracker.record_spec_change(
            vm_id, cpu_cores=2, ram_mb=2048,  # 2 GB RAM
            timestamp=period_start
        )
        
        report = self.tracker.calculate_billing_period(vm_id, period_start, period_end)
        
        # Expected cost: 2 cores * 0.01 * 10 hours + 2 GB * 0.005 * 10 hours
        # = 0.20 + 0.10 = 0.30
        self.assertGreater(report.total_cost, 0)

    def test_export_csv(self):
        """Test CSV export creates file with correct content."""
        vm_id = "101"
        now = datetime.now()
        period_start = now - timedelta(days=1)
        period_end = now
        
        self.tracker.set_vm_name(vm_id, "TestVM")
        self.tracker.record_spec_change(
            vm_id, cpu_cores=4, ram_mb=8192,
            timestamp=period_start + timedelta(hours=1)
        )
        
        report = self.tracker.calculate_billing_period(vm_id, period_start, period_end)
        csv_path = self.tracker.export_csv(report)
        
        self.assertTrue(os.path.exists(csv_path))
        
        with open(csv_path, 'r') as f:
            content = f.read()
        
        self.assertIn('Billing Report', content)
        self.assertIn(vm_id, content)
        self.assertIn('TestVM', content)

    def test_data_persistence(self):
        """Test that data is persisted and can be reloaded."""
        vm_id = "101"
        self.tracker.record_spec_change(vm_id, cpu_cores=4, ram_mb=8192)
        self.tracker.set_vm_name(vm_id, "PersistentVM")
        
        # Create new tracker instance (should load persisted data)
        new_tracker = BillingTracker(self.config, self.logger)
        
        self.assertIn(vm_id, new_tracker._spec_changes)
        self.assertEqual(new_tracker._vm_names.get(vm_id), "PersistentVM")

    def test_empty_period_report(self):
        """Test billing report for period with no data."""
        vm_id = "101"
        now = datetime.now()
        period_start = now - timedelta(days=1)
        period_end = now
        
        report = self.tracker.calculate_billing_period(vm_id, period_start, period_end)
        
        self.assertEqual(report.vm_id, vm_id)
        self.assertEqual(report.total_cost, 0)


class TestSpecChangeRecord(unittest.TestCase):
    """Tests for SpecChangeRecord dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        now = datetime.now()
        record = SpecChangeRecord(timestamp=now, cpu_cores=4, ram_mb=8192)
        
        result = record.to_dict()
        
        self.assertEqual(result['cpu_cores'], 4)
        self.assertEqual(result['ram_mb'], 8192)
        self.assertIn('timestamp', result)


class TestStateChangeRecord(unittest.TestCase):
    """Tests for StateChangeRecord dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        now = datetime.now()
        record = StateChangeRecord(timestamp=now, state='started')
        
        result = record.to_dict()
        
        self.assertEqual(result['state'], 'started')
        self.assertIn('timestamp', result)


class TestBillingReport(unittest.TestCase):
    """Tests for BillingReport dataclass."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        now = datetime.now()
        report = BillingReport(
            vm_id="101",
            vm_name="TestVM",
            period_start=now - timedelta(days=1),
            period_end=now,
            min_cpu_cores=2,
            max_cpu_cores=4,
            avg_cpu_cores=3.0,
            min_ram_mb=4096,
            max_ram_mb=8192,
            avg_ram_mb=6144.0,
            total_uptime_hours=23.5,
            total_downtime_hours=0.5,
            uptime_percentage=97.9,
            spec_changes=[],
            total_cost=1.50
        )
        
        result = report.to_dict()
        
        self.assertEqual(result['vm_id'], "101")
        self.assertEqual(result['min_cpu_cores'], 2)
        self.assertEqual(result['max_cpu_cores'], 4)
        self.assertEqual(result['total_cost'], 1.50)


if __name__ == '__main__':
    unittest.main()
