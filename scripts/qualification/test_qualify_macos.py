import importlib.util
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
