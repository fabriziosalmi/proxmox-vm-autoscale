"""
Tests for the configuration contract.

Before this existed, a key could be written, documented and never read, and
nothing anywhere would say so - not at load, not at use, not in the log. That
absence produced most of the defects fixed between 1.3.0 and 1.6.0. These tests
pin the contract that replaced it.
"""

import copy
import os
import sys
import unittest

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_schema import ConfigurationInvalid, validate

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def valid_config(**overrides):
    config = {
        "scaling_thresholds": {
            "cpu": {"high": 80, "low": 20},
            "ram": {"high": 85, "low": 25},
        },
        "scaling_limits": {
            "min_cores": 1, "max_cores": 8,
            "min_ram_mb": 1024, "max_ram_mb": 16384,
        },
        "check_interval": 300,
        "scale_cooldown": 300,
        "proxmox_hosts": [
            {"name": "pve1", "host": "10.0.0.11", "ssh_user": "root",
             "ssh_key": "/root/.ssh/id_ed25519", "ssh_port": 22},
        ],
        "virtual_machines": [
            {"vm_id": 101, "proxmox_host": "pve1", "scaling_enabled": True,
             "cpu_scaling": True, "ram_scaling": True},
        ],
        "host_limits": {"max_host_cpu_percent": 90, "max_host_ram_percent": 90},
    }
    config.update(overrides)
    return config


def errors_for(config):
    try:
        validate(config)
    except ConfigurationInvalid as e:
        return e.errors
    return []


class TestAcceptsWhatItShould(unittest.TestCase):

    def test_a_valid_config_passes_without_errors(self):
        self.assertEqual(validate(valid_config()), [])

    def test_the_shipped_example_config_is_valid(self):
        """The file users are told to edit must itself pass."""
        with open(os.path.join(REPO_ROOT, "config.yaml")) as fh:
            shipped = yaml.safe_load(fh)
        validate(shipped)   # must not raise

    def test_optional_sections_may_be_absent(self):
        config = valid_config()
        for optional in ("check_interval", "scale_cooldown"):
            config.pop(optional, None)
        self.assertEqual(errors_for(config), [])


class TestReferentialIntegrity(unittest.TestCase):
    """The failure that used to be completely silent."""

    def test_a_vm_pointing_at_an_unknown_host_is_an_error(self):
        config = valid_config()
        config["virtual_machines"][0]["proxmox_host"] = "typo"
        errors = errors_for(config)
        self.assertTrue(any("typo" in e and "proxmox_host" in e for e in errors))

    def test_the_error_lists_the_hosts_that_do_exist(self):
        config = valid_config()
        config["virtual_machines"][0]["proxmox_host"] = "typo"
        self.assertTrue(any("pve1" in e for e in errors_for(config)))

    def test_duplicate_host_names_are_rejected(self):
        config = valid_config()
        config["proxmox_hosts"].append(dict(config["proxmox_hosts"][0]))
        self.assertTrue(any("duplicate host name" in e for e in errors_for(config)))

    def test_duplicate_vmids_are_rejected(self):
        config = valid_config()
        config["virtual_machines"].append(dict(config["virtual_machines"][0]))
        self.assertTrue(any("duplicate VMID" in e for e in errors_for(config)))


class TestVMIDIsTreatedAsDangerous(unittest.TestCase):
    """vm_id is interpolated into shell commands run as root."""

    def test_a_shell_metacharacter_is_rejected(self):
        config = valid_config()
        config["virtual_machines"][0]["vm_id"] = "101; rm -rf /"
        self.assertTrue(any("vm_id" in e for e in errors_for(config)))

    def test_a_non_numeric_vmid_is_rejected(self):
        config = valid_config()
        config["virtual_machines"][0]["vm_id"] = "web-server"
        self.assertTrue(any("vm_id" in e for e in errors_for(config)))

    def test_a_numeric_string_is_accepted(self):
        """The shipped example quotes one VMID and leaves the other bare."""
        config = valid_config()
        config["virtual_machines"][0]["vm_id"] = "101"
        self.assertEqual(errors_for(config), [])


class TestRangesAndTypes(unittest.TestCase):

    def test_a_threshold_above_100_is_rejected(self):
        config = valid_config()
        config["scaling_thresholds"]["cpu"]["high"] = 150
        self.assertTrue(any("scaling_thresholds.cpu.high" in e for e in errors_for(config)))

    def test_inverted_thresholds_are_rejected(self):
        config = valid_config()
        config["scaling_thresholds"]["cpu"] = {"high": 20, "low": 80}
        self.assertTrue(any("scale up and down" in e for e in errors_for(config)))

    def test_min_above_max_is_rejected(self):
        config = valid_config()
        config["scaling_limits"]["min_cores"] = 16
        self.assertTrue(any("min_cores" in e and "max_cores" in e
                            for e in errors_for(config)))

    def test_a_string_where_a_number_belongs_is_rejected(self):
        config = valid_config()
        config["check_interval"] = "five minutes"
        self.assertTrue(any("check_interval" in e for e in errors_for(config)))

    def test_a_bad_ssh_port_is_rejected(self):
        config = valid_config()
        config["proxmox_hosts"][0]["ssh_port"] = 70000
        self.assertTrue(any("ssh_port" in e for e in errors_for(config)))

    def test_an_unknown_host_key_policy_is_rejected(self):
        self.assertTrue(any("ssh_host_key_policy" in e
                            for e in errors_for(valid_config(ssh_host_key_policy="trustme"))))

    def test_a_host_without_any_credential_is_rejected(self):
        config = valid_config()
        config["proxmox_hosts"][0].pop("ssh_key")
        self.assertTrue(any("ssh_password or ssh_key" in e for e in errors_for(config)))

    def test_host_limits_are_required_because_they_have_no_default(self):
        config = valid_config()
        config.pop("host_limits")
        self.assertTrue(any("host_limits" in e for e in errors_for(config)))


class TestUnknownKeysAreReported(unittest.TestCase):
    """The exact failure mode behind four separate historical defects."""

    def test_a_misspelt_top_level_key_produces_a_warning(self):
        warnings = validate(valid_config(check_intervall=300))
        self.assertTrue(any("check_intervall" in w for w in warnings))

    def test_a_misspelt_vm_key_produces_a_warning(self):
        config = valid_config()
        config["virtual_machines"][0]["treshholds"] = {"cpu_high": 90}
        warnings = validate(config)
        self.assertTrue(any("treshholds" in w for w in warnings))

    def test_a_misspelt_limit_key_produces_a_warning(self):
        config = valid_config()
        config["scaling_limits"]["max_ram"] = 32768      # the 1.3.0 defect, exactly
        warnings = validate(config)
        self.assertTrue(any("max_ram" in w for w in warnings))

    def test_an_unknown_key_is_a_warning_not_an_error(self):
        """Rejecting outright would break configs carrying newer keys."""
        self.assertEqual(errors_for(valid_config(some_future_key=1)), [])


class TestWarningsThatSaveOperators(unittest.TestCase):

    def test_both_password_and_key_warns_that_the_key_is_ignored(self):
        config = valid_config()
        config["proxmox_hosts"][0]["ssh_password"] = "hunter2"
        self.assertTrue(any("password wins" in w for w in validate(config)))

    def test_a_narrow_dead_band_warns_about_flapping(self):
        config = valid_config()
        config["scaling_thresholds"]["cpu"] = {"high": 70, "low": 65}
        self.assertTrue(any("flap" in w for w in validate(config)))

    def test_min_ram_below_a_gigabyte_warns_about_numa(self):
        config = valid_config()
        config["scaling_limits"]["min_ram_mb"] = 512
        self.assertTrue(any("NUMA" in w for w in validate(config)))

    def test_cooldown_below_the_poll_interval_warns_it_has_no_effect(self):
        config = valid_config(check_interval=300, scale_cooldown=60)
        self.assertTrue(any("scale_cooldown" in w for w in validate(config)))

    def test_the_auto_host_key_policy_warns_that_it_protects_nothing(self):
        warnings = validate(valid_config(ssh_host_key_policy="auto"))
        self.assertTrue(any("no protection" in w for w in warnings))

    def test_a_publicly_bound_metrics_endpoint_warns(self):
        config = valid_config(metrics={"enabled": True, "bind": "0.0.0.0", "port": 9808})
        self.assertTrue(any("no authentication" in w for w in validate(config)))

    def test_a_localhost_metrics_endpoint_does_not_warn(self):
        config = valid_config(metrics={"enabled": True, "bind": "127.0.0.1"})
        self.assertFalse(any("metrics.bind" in w for w in validate(config)))


class TestReportsEverythingAtOnce(unittest.TestCase):

    def test_all_problems_are_collected_not_just_the_first(self):
        config = valid_config()
        config["scaling_thresholds"]["cpu"]["high"] = 150
        config["scaling_limits"]["min_cores"] = 99
        config["virtual_machines"][0]["proxmox_host"] = "typo"
        config["proxmox_hosts"][0].pop("ssh_user")
        self.assertGreaterEqual(len(errors_for(config)), 4)

    def test_the_message_renders_one_problem_per_line(self):
        config = valid_config()
        config["scaling_thresholds"]["cpu"]["high"] = 150
        config["virtual_machines"][0]["proxmox_host"] = "typo"
        try:
            validate(config)
        except ConfigurationInvalid as e:
            self.assertIn("\n  - ", str(e))

    def test_a_non_mapping_config_is_rejected_without_crashing(self):
        self.assertTrue(errors_for(["not", "a", "mapping"]))


class TestPerVMOverrides(unittest.TestCase):

    def test_the_flat_threshold_shape_validates(self):
        config = valid_config()
        config["virtual_machines"][0]["thresholds"] = {"cpu_high": 90, "cpu_low": 30}
        self.assertEqual(errors_for(config), [])

    def test_the_nested_threshold_shape_validates(self):
        config = valid_config()
        config["virtual_machines"][0]["thresholds"] = {"cpu": {"high": 90, "low": 30}}
        self.assertEqual(errors_for(config), [])

    def test_inverted_per_vm_thresholds_are_rejected(self):
        config = valid_config()
        config["virtual_machines"][0]["thresholds"] = {"cpu_high": 30, "cpu_low": 90}
        self.assertTrue(errors_for(config))

    def test_a_partial_per_vm_limit_block_is_allowed(self):
        config = valid_config()
        config["virtual_machines"][0]["scaling_limits"] = {"max_cores": 16}
        self.assertEqual(errors_for(config), [])


if __name__ == "__main__":
    unittest.main()
