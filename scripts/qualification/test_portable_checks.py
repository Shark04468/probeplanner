"""Fail-closed contract tests for the external portability report, not the planner."""
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from portable_checks import packaging_counts


class PackagingPolicyTests(unittest.TestCase):
    def outcome(self, platform="linux", count=203, skip=None, message="Windows reparse-point test", failure=False):
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite", tests=str(count), errors="0", failures="0")
        for index in range(count):
            c = ET.SubElement(suite, "testcase", classname="other", name="test_" + str(index))
            if index == 0 and skip:
                c.set("classname", "test_verify_source_v030")
                c.set("name", skip)
                ET.SubElement(c, "skipped", message=message)
            if index == 1 and failure:
                ET.SubElement(c, "failure")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tests.xml"
            ET.ElementTree(root).write(path)
            return packaging_counts(path, platform)

    def test_all_pass(self):
        self.assertTrue(self.outcome()["countGatePassed"])

    def test_nonwindows_junction_is_not_a_pass(self):
        result = self.outcome(skip="test_windows_junction_not_followed")
        self.assertTrue(result["countGatePassed"])
        self.assertEqual((result["passed"], result["skipped"], result["notApplicableCount"]), (202, 1, 1))

    def test_windows_junction_cannot_skip(self):
        self.assertFalse(self.outcome(platform="win32", skip="test_windows_junction_not_followed")["countGatePassed"])

    def test_wrong_skip(self):
        self.assertFalse(self.outcome(skip="test_symlink_not_followed")["countGatePassed"])

    def test_wrong_reason(self):
        self.assertFalse(self.outcome(skip="test_windows_junction_not_followed", message="missing dependency")["countGatePassed"])

    def test_failure_blocks(self):
        self.assertFalse(self.outcome(skip="test_windows_junction_not_followed", failure=True)["countGatePassed"])

    def test_count_change_blocks(self):
        self.assertFalse(self.outcome(count=202)["countGatePassed"])

    def test_original_windows_privilege_policy(self):
        result = self.outcome(platform="win32", skip="test_symlink_not_followed", message="Host does not permit unprivileged symlinks")
        self.assertTrue(result["countGatePassed"])
        self.assertEqual(result["passed"], 202)
        self.assertEqual(result["notApplicableCount"], 0)


if __name__ == "__main__":
    unittest.main()
