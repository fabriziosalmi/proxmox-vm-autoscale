"""
Tests that stop the documentation from drifting away from the code.

REVIEW.md §8.3 recorded the problem and nothing acted on it: the documentation
restates behaviour that lives in code — configuration keys, metric names,
module names, the version — across eighteen pages plus a README plus
ARCHITECTURE.md plus SECURITY.md, and **nothing checked any of it**. During a
single session, thirteen files had to be corrected in one change, and stale
claims were found repeatedly *after* the code had already moved.

These are not style tests. Each one encodes a specific way the documentation has
actually gone wrong here, so that going wrong that way again fails the build
instead of reaching a reader.

They deliberately check *presence and agreement*, not prose. A key that exists
must be mentioned somewhere sensible; a version that is claimed must be the real
one. Whether the surrounding sentence is any good is a judgement no test makes.
"""

import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config_schema import (
    KNOWN_HOST_KEYS,
    KNOWN_LIMIT_KEYS,
    KNOWN_TOP_LEVEL,
    KNOWN_VM_KEYS,
)
from metrics import build_registry
from version import __version__

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: Legacy flat keys kept only for backwards compatibility. They are deliberately
#: absent from the reference so nobody writes a new config using them.
UNDOCUMENTED_BY_DESIGN = {"min_ram", "max_ram"}


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding="utf-8") as fh:
        return fh.read()


class TestEveryConfigurationKeyIsDocumented(unittest.TestCase):
    """A key the code accepts but nobody documents is a key nobody will use."""

    def setUp(self):
        self.reference = read("docs", "reference", "configuration.md")

    def _assert_documented(self, keys, label):
        missing = sorted(
            key for key in keys
            if key not in UNDOCUMENTED_BY_DESIGN and f"`{key}`" not in self.reference
        )
        self.assertEqual(
            missing, [],
            f"{label} accepted by the schema but absent from the "
            f"configuration reference: {missing}",
        )

    def test_top_level_keys(self):
        self._assert_documented(KNOWN_TOP_LEVEL, "top-level keys")

    def test_host_keys(self):
        self._assert_documented(KNOWN_HOST_KEYS, "proxmox_hosts keys")

    def test_vm_keys(self):
        self._assert_documented(KNOWN_VM_KEYS, "virtual_machines keys")

    def test_limit_keys(self):
        self._assert_documented(KNOWN_LIMIT_KEYS, "scaling_limits keys")

    def test_the_legacy_keys_stay_undocumented(self):
        """Documenting them would invite new configs to use them."""
        for key in UNDOCUMENTED_BY_DESIGN:
            self.assertNotIn(f"| `{key}` |", self.reference)


class TestEveryMetricIsDocumented(unittest.TestCase):
    """An exported series nobody documents cannot be alerted on."""

    def test_the_operations_guide_lists_every_series(self):
        guide = read("docs", "guide", "operations.md")
        exported = sorted(build_registry()._help)
        missing = [name for name in exported if name not in guide]
        self.assertEqual(
            missing, [],
            f"metrics exported but not documented in the operations guide: {missing}",
        )

    def test_the_guide_does_not_promise_series_that_do_not_exist(self):
        guide = read("docs", "guide", "operations.md")
        exported = set(build_registry()._help)
        promised = set(re.findall(r"`(vm_autoscale_[a-z_]+)`", guide))
        invented = sorted(promised - exported)
        self.assertEqual(
            invented, [],
            f"documented metrics that the service never exports: {invented}",
        )


class TestEveryModuleIsDocumented(unittest.TestCase):

    def test_the_module_reference_covers_every_source_file(self):
        sources = sorted(
            name for name in os.listdir(ROOT)
            if name.endswith(".py") and name not in ("conftest.py", "setup.py")
        )
        reference = read("docs", "reference", "modules.md")
        missing = [name for name in sources if name not in reference]
        self.assertEqual(
            missing, [],
            f"source modules absent from the module reference: {missing}",
        )


class TestTheVersionAgreesEverywhere(unittest.TestCase):
    """Four places claim the version. They drifted before; they cannot now."""

    def test_the_changelog_leads_with_the_current_version(self):
        changelog = read("CHANGELOG.md")
        released = re.findall(r"^## \[(\d+\.\d+\.\d+)\]", changelog, re.MULTILINE)
        self.assertTrue(released, "no released version found in CHANGELOG.md")
        self.assertEqual(
            released[0], __version__,
            f"version.py says {__version__} but the newest changelog entry is "
            f"{released[0]}",
        )

    def test_the_site_navigation_shows_the_current_version(self):
        config = read("docs", ".vitepress", "config.mts")
        shown = re.search(r"text: 'v(\d+\.\d+\.\d+)'", config)
        self.assertIsNotNone(shown, "no version in the site navigation")
        self.assertEqual(shown.group(1), __version__)

    def test_the_supported_versions_table_names_the_current_release(self):
        disclosure = read("docs", "security", "disclosure.md")
        self.assertIn(f"`v{__version__}`", disclosure)

    def test_the_pinned_clone_example_uses_the_current_release(self):
        hardening = read("docs", "security", "hardening.md")
        self.assertIn(f"--branch v{__version__}", hardening)

    def test_the_changelog_has_a_link_definition_for_the_current_version(self):
        changelog = read("CHANGELOG.md")
        self.assertIn(f"[{__version__}]: https://", changelog)


class TestClaimsThatHaveBeenWrongBefore(unittest.TestCase):
    """Each of these was stated in the docs and was not true at the time."""

    def test_the_python_floor_matches_what_ci_actually_tests(self):
        workflow = read(".github", "workflows", "ci.yml")
        tested = re.findall(r'"(\d+\.\d+)"', workflow)
        self.assertTrue(tested, "no Python versions found in the CI matrix")
        lowest = min(tuple(int(p) for p in v.split(".")) for v in tested)

        pyproject = read("pyproject.toml")
        declared = re.search(r'requires-python = ">=(\d+\.\d+)"', pyproject)
        self.assertIsNotNone(declared)
        declared_tuple = tuple(int(p) for p in declared.group(1).split("."))

        self.assertEqual(
            declared_tuple, lowest,
            "pyproject declares a Python floor that CI does not test — the "
            "README claimed 3.6+ for a year on that basis",
        )

    def test_the_shipped_config_is_the_one_the_reference_describes(self):
        """The example config must itself satisfy the documented contract."""
        import yaml

        from config_schema import validate
        validate(yaml.safe_load(read("config.yaml")))

    def test_the_readme_points_at_the_documentation_site(self):
        self.assertIn(
            "https://fabriziosalmi.github.io/proxmox-vm-autoscale/",
            read("README.md"),
        )


if __name__ == "__main__":
    unittest.main()
