"""Fail-closed contract tests for the external portability report, not the planner."""
from pathlib import Path
import tempfile
import json
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from portable_checks import packaging_counts, main


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

    def test_contract_serialization_uses_path_objects(self):
        def strict_sha(path):
            self.assertIsInstance(path, Path)
            return "bound"

        def execute(*args):
            raise RuntimeError("first_stage_reached")

        def require(condition, message):
            if not condition:
                raise ValueError(message)

        original = SimpleNamespace(__file__=__file__, sha=strict_sha,
            inventory=lambda *a: {}, verify_package=lambda *a: {}, verify_git=lambda *a: {},
            verify_environment=lambda *a: {}, inspect_profile=lambda *a: {},
            verify_replay=lambda *a: {}, PUBLIC_PROFILE_SHA256="bound",
            OLD_ARCHIVE_SHA256="bound", OLD_MANIFEST_SHA256="bound", require=require, execute=execute)
        writer = SimpleNamespace(serialize=lambda value: json.dumps(value).encode(),
                                 write_bytes=lambda path, value: path.write_bytes(value))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source"
            source.mkdir()
            output = root / "output"
            argv = ["driver", "--source", str(source), "--evidence-dir", str(root / "evidence"),
                    "--profile", str(root / "profile"), "--replay-dir", str(root / "replay"), "--output", str(output)]
            old_path = list(sys.path)
            try:
                with patch.object(sys, "argv", argv), patch.dict(sys.modules,
                      {"run_release_checks_v030": original, "prepare_public_profile": writer}):
                    self.assertEqual(main(), 1)
            finally:
                sys.path[:] = old_path
            result = json.loads((output / "SUMMARY.json").read_text())
            self.assertEqual(result["error"], "RuntimeError: first_stage_reached")
            self.assertTrue((output / "RUN_CONTRACT.json").is_file())


if __name__ == "__main__":
    unittest.main()
