"""Synthetic deployment checks do not count as actual viewer or anatomy acceptance."""
import json
from pathlib import Path
import shutil
import tempfile
import unittest

from install_viewer_candidate import install_candidate, validate_payload, inventory, FILES, PLAYER_FILES
from qualify_macos import sha

PAYLOAD = Path(__file__).resolve().parent / 'viewer_candidate'
WRAPPER = Path(__file__).resolve().parents[2] / 'apps/ui/public/unity/index.html'


class CandidateTests(unittest.TestCase):
    def make_static(self, root):
        static = root / 'static'
        (static / 'unity/Build').mkdir(parents=True)
        for name in FILES: (static / 'unity' / name).write_bytes(b'old frozen fixture')
        shutil.copyfile(WRAPPER, static / 'unity/index.html')
        (static / 'index.html').write_text('unchanged react fixture')
        return static

    def test_sealed_payload_passes(self):
        c = validate_payload(PAYLOAD)
        self.assertEqual(set(c['files']), FILES)

    def test_only_player_and_truthful_provenance_change(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d); static = self.make_static(root)
            before = inventory(static)
            r = install_candidate(static, PAYLOAD, root/'report.json')
            self.assertEqual(before['index.html'], r['after']['index.html'])
            self.assertEqual(before['unity/index.html'], r['after']['unity/index.html'])
            self.assertFalse(r['uiAccepted'])
            self.assertEqual(set(r['changed']), {'unity/'+n for n in FILES})

    def test_bad_wrapper_stops_before_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); static=self.make_static(root)
            (static/'unity/index.html').write_text('wrong wrapper')
            before=inventory(static)
            with self.assertRaisesRegex(ValueError, 'wrapper differs'):
                install_candidate(static, PAYLOAD, root/'report.json')
            self.assertEqual(before, inventory(static))

    def test_corrupt_player_and_extra_member_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'payload'; shutil.copytree(PAYLOAD,p)
            member=p/sorted(PLAYER_FILES)[0]; original=member.read_bytes(); member.write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError,'bytes differ'): validate_payload(p)
            member.write_bytes(original); (p/'unexpected').write_text('extra')
            with self.assertRaisesRegex(ValueError,'Unexpected payload member'): validate_payload(p)

    def test_changed_contract_and_unsafe_path_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'payload'; shutil.copytree(PAYLOAD,p)
            c=json.loads((p/'CONTRACT.json').read_text())
            c['files']['../escape']={'sha256':'0'*64,'bytes':0}
            (p/'CONTRACT.json').write_text(json.dumps(c))
            with self.assertRaisesRegex(ValueError,'hash differs'): validate_payload(p)
            with self.assertRaisesRegex(ValueError,'path set'): validate_payload(p,sha(p/'CONTRACT.json'))

    def test_existing_report_and_missing_frozen_asset_stop_before_write(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); static=self.make_static(root); report=root/'report.json'
            report.write_text('preserved')
            with self.assertRaisesRegex(ValueError,'Invalid output destination'): install_candidate(static,PAYLOAD,report)
            (static/'unity/Build/WebGL-Build.wasm.unityweb').unlink()
            before=inventory(static)
            with self.assertRaisesRegex(ValueError,'assets missing'): install_candidate(static,PAYLOAD,root/'new.json')
            self.assertEqual(before,inventory(static))


if __name__ == '__main__': unittest.main()
