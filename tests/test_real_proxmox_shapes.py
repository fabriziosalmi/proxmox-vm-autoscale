"""
Tests built from the output of a real Proxmox VE node.

Every prior test was written against invented `qm` and `pvesh` output, and the
fixtures happened to encode the same assumptions the code did — so 290 of them
passed while two defects sat in the scaling path. Twenty minutes against a
real PVE 9.1.7 node found both.

The payloads below are copied from that node rather than imagined:

- `qm config` for a VM that has never had `vcpus` set — which is the Proxmox
  default, so it describes most VMs in the world.
- `pvesh get /nodes/<n>/status` succeeding with exit 0 while writing a benign
  warning to stderr.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from host_resource_checker import HostResourceChecker, HostResourceUnavailable
from vm_manager import VMResourceManager

# Verbatim from `qm config 103` on PVE 9.1.7. Note what is *not* there:
# no vcpus, no hotplug, no numa, no balloon.
REAL_QM_CONFIG = """boot: order=scsi0
cores: 4
memory: 6144
name: fabos-imgbuild
onboot: 1
scsi0: local-lvm:vm-103-disk-0,size=24G
scsihw: virtio-scsi-single
"""

# Verbatim shape from `pvesh get /nodes/opti2/status --output-format json`.
REAL_NODE_STATUS = (
    '{"cpu":0.0037,"memory":{"available":20000000000,"free":18000000000,'
    '"total":33508769792,"used":12741144576}}'
)

# Proxmox writes this to stderr on a *successful* call when a stale API token
# entry is present in user.cfg.
REAL_STDERR_WARNING = "user config - ignore invalid acl token 'root@pam!fabos-predone'"


def manager_for(config_text, hotplug=True, running=True):
    ssh = MagicMock()

    def respond(cmd, *args, **kwargs):
        if "qm status" in cmd:
            return ("status: running" if running else "status: stopped", "", 0)
        if "qm set" in cmd:
            return ("", "", 0)
        return (config_text, "", 0)

    ssh.execute_command.side_effect = respond
    return VMResourceManager(ssh, "103", {
        "auto_configure_hotplug": False,
        "scale_cooldown": 0,
        "scaling_limits": {"min_cores": 1, "max_cores": 8,
                           "min_ram_mb": 1024, "max_ram_mb": 16384},
    })


class TestAbsentVcpusMeansEveryCoreIsOnline(unittest.TestCase):
    """Proxmox omits `vcpus` when all cores are online. It does not mean one."""

    def test_absent_vcpus_reports_the_core_count(self):
        mgr = manager_for(REAL_QM_CONFIG)
        self.assertEqual(mgr._get_current_cores(), 4)
        self.assertEqual(mgr._get_current_vcpus(), 4)

    def test_an_explicit_vcpus_still_wins(self):
        mgr = manager_for(REAL_QM_CONFIG.replace("cores: 4", "cores: 4\nvcpus: 2"))
        self.assertEqual(mgr._get_current_vcpus(), 2)

    def test_both_absent_falls_back_to_the_proxmox_default_of_one(self):
        mgr = manager_for("memory: 1024\nname: minimal\n")
        self.assertEqual(mgr._get_current_cores(), 1)
        self.assertEqual(mgr._get_current_vcpus(), 1)

    def test_a_scale_up_does_not_halve_the_guest(self):
        """Reading vcpus as 1 on a 4-core guest turned a scale-up into `-vcpus 2`."""
        config = REAL_QM_CONFIG + "hotplug: cpu,memory\nnuma: 1\n"
        mgr = manager_for(config)
        mgr.scale_cpu("up")

        issued = [c.args[0] for c in mgr.ssh_client.execute_command.call_args_list]
        vcpu_commands = [c for c in issued if "-vcpus" in c]
        self.assertFalse(
            any("-vcpus 2" in c for c in vcpu_commands),
            f"a scale-up reduced a 4-vCPU guest: {vcpu_commands}",
        )

    def test_a_scale_up_at_full_width_raises_the_core_count(self):
        """4 cores all online: growing means more cores, not fewer vCPUs."""
        config = REAL_QM_CONFIG + "hotplug: cpu,memory\nnuma: 1\n"
        mgr = manager_for(config)
        self.assertTrue(mgr.scale_cpu("up"))

        issued = [c.args[0] for c in mgr.ssh_client.execute_command.call_args_list]
        self.assertTrue(any("-cores 5" in c for c in issued), issued)

    def test_a_scale_down_moves_one_step_not_straight_to_the_floor(self):
        """It used to compute max(1 - 1, 1) and drop a 4-vCPU guest to 1."""
        config = REAL_QM_CONFIG + "hotplug: cpu,memory\nnuma: 1\n"
        mgr = manager_for(config)
        mgr.scale_cpu("down")

        issued = [c.args[0] for c in mgr.ssh_client.execute_command.call_args_list]
        self.assertTrue(any("-vcpus 3" in c for c in issued), issued)
        self.assertFalse(any("-vcpus 1" in c for c in issued), issued)


class TestStderrOnASuccessfulCommand(unittest.TestCase):
    """A benign warning on stderr is not a failure."""

    def _checker(self, output, error="", exit_status=0):
        ssh = MagicMock()
        ssh.execute_command.return_value = (output, error, exit_status)
        return HostResourceChecker(ssh)

    def test_a_warning_on_stderr_with_exit_zero_is_ignored(self):
        """This killed the host gate on a real node, every VM, every cycle."""
        checker = self._checker(REAL_NODE_STATUS, REAL_STDERR_WARNING, 0)
        self.assertTrue(checker.check_host_resources(90, 90))

    def test_the_readings_are_still_published(self):
        checker = self._checker(REAL_NODE_STATUS, REAL_STDERR_WARNING, 0)
        checker.check_host_resources(90, 90)
        self.assertAlmostEqual(checker.last_cpu_percent, 0.37, places=1)
        self.assertAlmostEqual(checker.last_ram_percent, 38.02, places=1)

    def test_a_non_zero_exit_status_is_still_fatal(self):
        checker = self._checker("", "permission denied", 1)
        with self.assertRaises(HostResourceUnavailable):
            checker.check_host_resources(90, 90)

    def test_the_error_names_the_exit_status(self):
        checker = self._checker("", "permission denied", 13)
        with self.assertRaises(HostResourceUnavailable) as ctx:
            checker.check_host_resources(90, 90)
        self.assertIn("13", str(ctx.exception))

    def test_the_gate_still_blocks_a_saturated_node(self):
        checker = self._checker(REAL_NODE_STATUS, REAL_STDERR_WARNING, 0)
        self.assertFalse(checker.check_host_resources(90, 10))


class TestRealClusterResourcesShape(unittest.TestCase):
    """The JSON contract, as PVE 9.1.7 actually returns it."""

    REAL_ROW = (
        '[{"id":"qemu/103","type":"qemu","vmid":103,"node":"opti2",'
        '"status":"running","cpu":0.00370364808755346,"maxcpu":4,'
        '"mem":5412270080,"maxmem":6442450944,"name":"fabos-imgbuild"},'
        '{"id":"lxc/100","type":"lxc","vmid":100,"node":"opti2",'
        '"status":"running","cpu":0.01,"mem":1000,"maxmem":2000}]'
    )

    def _manager(self):
        ssh = MagicMock()

        def respond(cmd, *args, **kwargs):
            if "qm status" in cmd:
                return ("status: running", "", 0)
            if "cluster/resources" in cmd:
                return (self.REAL_ROW, "", 0)
            return (REAL_QM_CONFIG, "", 0)

        ssh.execute_command.side_effect = respond
        return VMResourceManager(ssh, "103", {
            "auto_configure_hotplug": False, "scale_cooldown": 0,
        })

    def test_usage_is_read_from_the_real_shape(self):
        cpu, ram = self._manager().get_resource_usage()
        self.assertAlmostEqual(cpu, 0.37, places=1)
        self.assertAlmostEqual(ram, 84.0, places=1)

    def test_an_lxc_sharing_the_node_is_not_confused_for_the_guest(self):
        cpu, _ = self._manager().get_resource_usage()
        self.assertNotAlmostEqual(cpu, 1.0, places=1)


if __name__ == "__main__":
    unittest.main()
