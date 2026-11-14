"""Validate the frozen Vitest report without conflating parameter rows with retries."""
from collections import Counter
import xml.etree.ElementTree as ET

EXPECTED_DUPLICATES = {
    ('src/components/PinpointIO.test.jsx',
     'recorded hardware conservation > does not quantize recorded pose during local export'): 2,
    ('src/components/PinpointIO.test.jsx',
     'plan coordinate round trips > exports resolved coordinates and depth without applying correction twice'): 2,
}


def frontend_summary(path):
    root = ET.parse(path).getroot()
    suites = list(root.iter('testsuite'))
    cases = list(root.iter('testcase'))
    if len(cases) != 152 or len(suites) != 22:
        raise ValueError('Frozen frontend test/suite count differs')
    identities = Counter((c.get('classname'), c.get('name')) for c in cases)
    duplicates = {k: n for k, n in identities.items() if n > 1}
    if duplicates != EXPECTED_DUPLICATES or any(not all(k) for k in identities):
        raise ValueError('Unexpected duplicate or unnamed frontend case')
    for suite in suites:
        if int(suite.get('tests', '-1')) != len(list(suite.iter('testcase'))):
            raise ValueError('Frontend suite declared count differs')
        if any(int(suite.get(k, '0')) for k in ['errors', 'failures', 'skipped']):
            raise ValueError('Frontend suite contains error/failure/skip')
    if any(list(root.iter(k)) for k in ['error', 'failure', 'skipped']):
        raise ValueError('Frontend case contains error/failure/skip')
    return dict(executed=152, suites=22, unique_display_names=150, errors=0, failures=0, skipped=0,
                parameterized_names=[dict(file=k[0], name=k[1], rows=n) for k, n in duplicates.items()],
                parameterization_source='Frozen PinpointIO.test.jsx lines 35 and 51: JSON and TXT encoders',
                identity_policy='file plus title plus ordinal within explicit two-row parameter groups; no retries')
