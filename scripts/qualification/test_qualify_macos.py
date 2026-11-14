import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location("qualification", Path(__file__).with_name("qualify_macos.py"))
qualification = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qualification)


class TestJUnit(unittest.TestCase):
    def make_report(self, path, count=112, child=None, declared=None, duplicate=False):
        root = ET.Element("testsuites")
        suite = ET.SubElement(root, "testsuite", tests=str(count if declared is None else declared),
                              failures="0", errors="0", skipped="0")
        for i in range(count):
            case = ET.SubElement(suite, "testcase", classname="frozen", name=str(0 if duplicate else i))
            if i == 0 and child:
                ET.SubElement(case, child)
        ET.ElementTree(root).write(path)

    def check(self, **kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.xml"
            self.make_report(path, **kwargs)
            return qualification.junit_summary(path)

    def test_exact_pass(self):
        self.assertEqual(self.check()["executed"], 112)

    def test_count_not_from_text_or_header(self):
        with self.assertRaises(ValueError):
            self.check(count=111, declared=112)

    def test_extra_test_rejected(self):
        with self.assertRaises(ValueError):
            self.check(count=113)

    def test_failure_error_and_skip_rejected(self):
        for tag in ("failure", "error", "skipped"):
            with self.subTest(tag=tag), self.assertRaises(ValueError):
                self.check(child=tag)

    def test_duplicate_rejected(self):
        with self.assertRaises(ValueError):
            self.check(duplicate=True)

    def test_bad_declared_count_rejected(self):
        with self.assertRaises(ValueError):
            self.check(declared=0)

    def test_command_record_does_not_case_collide_with_source_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            (out / "SOURCE_IDENTITY.json").write_text('{"sourceConsistencyPassed":true}')
            qualification.run_step(out, out, "source_identity", [sys.executable, "-c", "print('ok')"], 10)
            names = [p.name.casefold() for p in out.iterdir()]
            self.assertEqual(len(names), len(set(names)))
            self.assertTrue(json.loads((out / "SOURCE_IDENTITY.json").read_text())["sourceConsistencyPassed"])
            self.assertEqual(json.loads((out / "source_identity.step.json").read_text())["returncode"], 0)

    def test_nonzero_command_is_retained_and_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with self.assertRaises(ValueError):
                qualification.run_step(out, out, "failed", [sys.executable, "-c", "raise SystemExit(2)"], 10)
            self.assertEqual(json.loads((out / "failed.step.json").read_text())["returncode"], 2)


if __name__ == "__main__":
    unittest.main()
