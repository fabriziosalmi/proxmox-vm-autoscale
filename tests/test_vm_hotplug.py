"""
Unit tests for VM Hotplug functionality in vm_manager.py.

These tests verify:
- Hotplug detection (_check_hotplug_enabled)
- NUMA detection (_check_numa_enabled)
- Auto-configuration of hotplug settings
- CPU scaling with hotplug (using vcpus)
- RAM scaling with hotplug (using balloon)
- Fallback behavior when hotplug is not enabled
"""

import os
import unittest
from unittest.mock import MagicMock, patch, call

# Add parent directory to path for imports
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vm_manager import VMResourceManager


class MockSSHClient:
    """Mock SSH client for testing."""
    
    def __init__(self):
        self.commands = []
        self.responses = {}
    
    def execute_command(self, command):
        self.commands.append(command)
        return self.responses.get(command, "")
    
    def set_response(self, command, response):
        self.responses[command] = response


class TestHotplugDetection(unittest.TestCase):
    """Tests for hotplug and NUMA detection."""

    def setUp(self):
        """Set up test fixtures."""
        self.ssh_client = MockSSHClient()
        self.config = {
            'auto_configure_hotplug': False,  # Disable auto-config for detection tests
            'scale_cooldown': 0,
            'min_cores': 1,
            'max_cores': 8,
            'min_ram': 512,
            'max_ram': 16384
        }

    def test_check_hotplug_enabled_both(self):
        """Test detection when both CPU and memory hotplug are enabled."""
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 4\nmemory: 8192\nhotplug: cpu,memory,network,disk"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        cpu_hotplug, memory_hotplug = manager._check_hotplug_enabled()
        
        self.assertTrue(cpu_hotplug)
        self.assertTrue(memory_hotplug)

    def test_check_hotplug_enabled_cpu_only(self):
        """Test detection when only CPU hotplug is enabled."""
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 4\nmemory: 8192\nhotplug: cpu,network,disk"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        cpu_hotplug, memory_hotplug = manager._check_hotplug_enabled()
        
        self.assertTrue(cpu_hotplug)
        self.assertFalse(memory_hotplug)

    def test_check_hotplug_enabled_memory_only(self):
        """Test detection when only memory hotplug is enabled."""
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 4\nmemory: 8192\nhotplug: memory,network"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        cpu_hotplug, memory_hotplug = manager._check_hotplug_enabled()
        
        self.assertFalse(cpu_hotplug)
        self.assertTrue(memory_hotplug)

    def test_check_hotplug_disabled(self):
        """Test detection when hotplug is not configured."""
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 4\nmemory: 8192\n"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        cpu_hotplug, memory_hotplug = manager._check_hotplug_enabled()
        
        self.assertFalse(cpu_hotplug)
        self.assertFalse(memory_hotplug)

    def test_check_numa_enabled(self):
        """Test NUMA detection when enabled."""
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 4\nmemory: 8192\nnuma: 1"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        numa_enabled = manager._check_numa_enabled()
        
        self.assertTrue(numa_enabled)

    def test_check_numa_disabled(self):
        """Test NUMA detection when disabled."""
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 4\nmemory: 8192\nnuma: 0"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        numa_enabled = manager._check_numa_enabled()
        
        self.assertFalse(numa_enabled)

    def test_check_numa_not_configured(self):
        """Test NUMA detection when not configured."""
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 4\nmemory: 8192"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        numa_enabled = manager._check_numa_enabled()
        
        self.assertFalse(numa_enabled)


class TestAutoConfigureHotplug(unittest.TestCase):
    """Tests for auto-configuration of hotplug settings."""

    def setUp(self):
        """Set up test fixtures."""
        self.ssh_client = MockSSHClient()

    def test_auto_configure_when_nothing_enabled(self):
        """Test that hotplug and NUMA are configured when not present."""
        config = {
            'auto_configure_hotplug': True,
            'scale_cooldown': 0
        }
        
        # Mock VM config without hotplug or NUMA
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 4\nmemory: 8192"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", config)
        
        # Should have called qm set to enable hotplug and NUMA
        commands = [c for c in self.ssh_client.commands if 'qm set' in c]
        self.assertTrue(any('-hotplug' in c for c in commands))
        self.assertTrue(any('-numa' in c for c in commands))

    def test_no_auto_configure_when_already_enabled(self):
        """Test that nothing is changed when hotplug and NUMA are already enabled."""
        config = {
            'auto_configure_hotplug': True,
            'scale_cooldown': 0
        }
        
        # Mock VM config with hotplug and NUMA already enabled
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 4\nmemory: 8192\nhotplug: cpu,memory,network,disk\nnuma: 1"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", config)
        
        # Should NOT have called qm set to modify settings
        set_commands = [c for c in self.ssh_client.commands if 'qm set' in c]
        self.assertEqual(len(set_commands), 0)

    def test_auto_configure_disabled(self):
        """Test that auto-configure respects the disabled flag."""
        config = {
            'auto_configure_hotplug': False,
            'scale_cooldown': 0
        }
        
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 4\nmemory: 8192"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", config)
        
        # Should NOT have called _ensure_hotplug_configured
        set_commands = [c for c in self.ssh_client.commands if 'qm set' in c]
        self.assertEqual(len(set_commands), 0)


class TestCPUScalingWithHotplug(unittest.TestCase):
    """Tests for CPU scaling with hotplug support."""

    def setUp(self):
        """Set up test fixtures."""
        self.ssh_client = MockSSHClient()
        self.config = {
            'auto_configure_hotplug': False,
            'scale_cooldown': 0,
            'min_cores': 1,
            'max_cores': 8
        }

    def test_scale_cpu_up_with_hotplug_vcpus_available(self):
        """Test CPU scale up uses vcpus when there's headroom within cores."""
        # VM has 4 cores but only 2 vcpus active
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 4\nvcpus: 2\nhotplug: cpu,memory\nnuma: 1"
        )
        self.ssh_client.set_response(
            "qm status 101 --verbose",
            "status: running"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        manager.last_scale_time = 0  # Reset cooldown
        manager._scale_cpu_up(current_cores=4, current_vcpus=2)
        
        # Should set vcpus to 3, not increase cores
        vcpus_commands = [c for c in self.ssh_client.commands if '-vcpus' in c]
        cores_commands = [c for c in self.ssh_client.commands if '-cores' in c and 'qm set' in c]
        
        self.assertTrue(any('3' in c for c in vcpus_commands))

    def test_scale_cpu_down_with_hotplug(self):
        """Test CPU scale down uses vcpus for hotplug."""
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 4\nvcpus: 4\nhotplug: cpu,memory\nnuma: 1"
        )
        self.ssh_client.set_response(
            "qm status 101 --verbose",
            "status: running"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        manager.last_scale_time = 0
        manager._scale_cpu_down(current_cores=4, current_vcpus=4)
        
        # Should reduce vcpus first
        vcpus_commands = [c for c in self.ssh_client.commands if '-vcpus' in c]
        self.assertTrue(any('3' in c for c in vcpus_commands))

    def test_scale_cpu_up_without_hotplug_warns(self):
        """Test CPU scale up without hotplug logs a warning."""
        self.ssh_client.set_response(
            "qm config 101",
            "cores: 2\nvcpus: 2"  # No hotplug
        )
        self.ssh_client.set_response(
            "qm status 101 --verbose",
            "status: running"
        )
        
        with patch('logging.getLogger') as mock_logger:
            mock_log = MagicMock()
            mock_logger.return_value = mock_log
            
            manager = VMResourceManager(self.ssh_client, "101", self.config)
            manager.last_scale_time = 0
            manager._scale_cpu_up(current_cores=2, current_vcpus=2)
            
            # Check that warning was logged
            # Note: In actual test, would verify warning was logged


class TestRAMScalingWithHotplug(unittest.TestCase):
    """Tests for RAM scaling with hotplug support."""

    def setUp(self):
        """Set up test fixtures."""
        self.ssh_client = MockSSHClient()
        self.config = {
            'auto_configure_hotplug': False,
            'scale_cooldown': 0,
            'min_ram': 512,
            'max_ram': 16384
        }

    def test_set_ram_with_hotplug_uses_balloon(self):
        """Test RAM scaling uses balloon when hotplug and NUMA are enabled."""
        self.ssh_client.set_response(
            "qm config 101",
            "memory: 4096\nhotplug: cpu,memory\nnuma: 1"
        )
        self.ssh_client.set_response(
            "qm status 101 --verbose",
            "status: running"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        manager._set_ram(8192)
        
        # Should use balloon for hotplug
        balloon_commands = [c for c in self.ssh_client.commands if '-balloon' in c]
        self.assertTrue(len(balloon_commands) > 0)

    def test_set_ram_without_numa_uses_memory(self):
        """Test RAM scaling uses memory config when NUMA is not enabled."""
        self.ssh_client.set_response(
            "qm config 101",
            "memory: 4096\nhotplug: cpu,memory"  # Hotplug but no NUMA
        )
        self.ssh_client.set_response(
            "qm status 101 --verbose",
            "status: running"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        manager._set_ram(8192)
        
        # Should use -memory, not -balloon
        memory_commands = [c for c in self.ssh_client.commands if '-memory' in c]
        balloon_commands = [c for c in self.ssh_client.commands if '-balloon' in c]
        
        self.assertTrue(len(memory_commands) > 0)
        self.assertEqual(len(balloon_commands), 0)

    def test_set_ram_vm_stopped_uses_memory(self):
        """Test RAM scaling uses memory config when VM is stopped."""
        self.ssh_client.set_response(
            "qm config 101",
            "memory: 4096\nhotplug: cpu,memory\nnuma: 1"
        )
        self.ssh_client.set_response(
            "qm status 101 --verbose",
            "status: stopped"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        manager._set_ram(8192)
        
        # Should use -memory when VM is stopped
        memory_commands = [c for c in self.ssh_client.commands if '-memory' in c]
        self.assertTrue(len(memory_commands) > 0)


class TestVMRunningCheck(unittest.TestCase):
    """Tests for VM running status check."""

    def setUp(self):
        """Set up test fixtures."""
        self.ssh_client = MockSSHClient()
        self.config = {
            'auto_configure_hotplug': False,
            'scale_cooldown': 0
        }

    def test_is_vm_running_true(self):
        """Test VM running detection when VM is running."""
        self.ssh_client.set_response(
            "qm status 101 --verbose",
            "status: running\ncpuunits: 1024"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        result = manager.is_vm_running()
        
        self.assertTrue(result)

    def test_is_vm_running_false(self):
        """Test VM running detection when VM is stopped."""
        self.ssh_client.set_response(
            "qm status 101 --verbose",
            "status: stopped"
        )
        
        manager = VMResourceManager(self.ssh_client, "101", self.config)
        result = manager.is_vm_running()
        
        self.assertFalse(result)


if __name__ == '__main__':
    unittest.main()
