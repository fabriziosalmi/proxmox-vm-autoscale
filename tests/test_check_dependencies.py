"""
Tests for the dependency verifier.

It exists because installing on a real Proxmox VE 9 node produced an
installation whose dependencies violated `requirements.txt` — paramiko 3.5.1
against a declared floor of 5.0.0 — and nothing anywhere said so. The service
started and worked, which is precisely the problem: a contract nothing enforces
is decoration.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import check_dependencies as cd


class TestVersionParsing(unittest.TestCase):

    def test_plain_versions_compare_numerically(self):
        self.assertLess(cd.parse_version("3.5.1"), cd.parse_version("5.0.0"))
        self.assertLess(cd.parse_version("6.0.2"), cd.parse_version("6.0.3"))
        self.assertLess(cd.parse_version("2.32.3"), cd.parse_version("2.34.2"))

    def test_ten_sorts_above_nine_not_below(self):
        """String comparison would put 2.9.0 above 2.10.0."""
        self.assertLess(cd.parse_version("2.9.0"), cd.parse_version("2.10.0"))

    def test_suffixes_are_ignored_rather_than_crashing(self):
        self.assertEqual(cd.parse_version("3.5.1rc1"), (3, 5, 1))
        self.assertEqual(cd.parse_version("2.0.0.dev3"), (2, 0, 0, 0))

    def test_equal_versions_satisfy_a_floor(self):
        self.assertFalse(cd.parse_version("6.0.3") < cd.parse_version("6.0.3"))


class TestRequirementParsing(unittest.TestCase):

    def _requirements(self, text):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write(text)
            path = fh.name
        self.addCleanup(os.unlink, path)
        return cd.read_requirements(path)

    def test_it_reads_the_shipped_requirements(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        names = [n for n, _ in cd.read_requirements(os.path.join(root, "requirements.txt"))]
        self.assertIn("paramiko", names)
        self.assertIn("PyYAML", names)
        self.assertIn("requests", names)

    def test_comments_and_blank_lines_are_skipped(self):
        parsed = self._requirements("# a comment\n\nparamiko>=5.0.0\n")
        self.assertEqual(parsed, [("paramiko", "5.0.0")])

    def test_whitespace_around_the_operator_is_tolerated(self):
        self.assertEqual(self._requirements("requests >= 2.34.2\n"),
                         [("requests", "2.34.2")])

    def test_a_missing_file_yields_nothing_rather_than_raising(self):
        self.assertEqual(cd.read_requirements("/nonexistent/requirements.txt"), [])


class TestReporting(unittest.TestCase):

    def _run(self, requirements_text, installed):
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as fh:
            fh.write(requirements_text)
            path = fh.name
        self.addCleanup(os.unlink, path)

        with patch.object(cd, "installed_version", side_effect=installed.get):
            return cd.main(["check_dependencies.py", path])

    def test_satisfied_requirements_exit_zero(self):
        code = self._run("paramiko>=3.0.0\n", {"paramiko": "3.5.1"})
        self.assertEqual(code, 0)

    def test_the_exact_mismatch_from_the_real_node_is_caught(self):
        """apt on Debian 13 against the floors requirements.txt declares."""
        code = self._run(
            "paramiko>=5.0.0\nPyYAML>=6.0.3\nrequests>=2.34.2\n",
            {"paramiko": "3.5.1", "PyYAML": "6.0.2", "requests": "2.32.3"},
        )
        self.assertEqual(code, 1)

    def test_a_missing_package_is_reported(self):
        code = self._run("paramiko>=3.0.0\n", {"paramiko": None})
        self.assertEqual(code, 1)

    def test_no_requirements_is_not_a_failure(self):
        code = self._run("# nothing here\n", {})
        self.assertEqual(code, 0)

    def test_one_bad_package_among_good_ones_still_fails(self):
        code = self._run(
            "paramiko>=3.0.0\nrequests>=2.34.2\n",
            {"paramiko": "3.5.1", "requests": "2.32.3"},
        )
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
