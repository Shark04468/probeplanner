import tempfile
import unittest
from pathlib import Path
import xml.etree.ElementTree as ET

from frontend_junit import EXPECTED_DUPLICATES, frontend_summary


class FrontendJUnitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'report.xml'
        self.root = ET.Element('testsuites')
        suites = [ET.SubElement(self.root, 'testsuite', name=str(i), tests='0', errors='0', failures='0', skipped='0') for i in range(22)]
        for (file, name), count in EXPECTED_DUPLICATES.items():
            for _ in range(count):
                ET.SubElement(suites[0], 'testcase', classname=file, name=name)
        for i in range(148):
            ET.SubElement(suites[i % 22], 'testcase', classname='fixture-only', name=str(i))
        for s in suites:
            s.set('tests', str(len(s)))

    def check(self):
        ET.ElementTree(self.root).write(self.path)
        return frontend_summary(self.path)

    def test_explicit_parameter_groups_are_not_duplicate_runs(self):
        self.assertEqual(self.check()['executed'], 152)

    def test_unexpected_duplicate_fails(self):
        cases = list(self.root.iter('testcase'))
        cases[-1].attrib.update(cases[-2].attrib)
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            self.check()

    def test_case_failure_fails(self):
        ET.SubElement(next(self.root.iter('testcase')), 'failure')
        with self.assertRaisesRegex(ValueError, 'failure'):
            self.check()

    def test_suite_failure_fails(self):
        self.root[0].set('errors', '1')
        with self.assertRaisesRegex(ValueError, 'error'):
            self.check()

    def test_count_mismatch_fails(self):
        self.root[0].set('tests', '999')
        with self.assertRaisesRegex(ValueError, 'declared'):
            self.check()

    def test_missing_parameter_row_fails(self):
        self.root[0].remove(self.root[0][0])
        with self.assertRaisesRegex(ValueError, 'count'):
            self.check()


if __name__ == '__main__':
    unittest.main()
