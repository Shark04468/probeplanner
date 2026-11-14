"""Synthetic receiver tests, never evidence of an actual Mac or anatomy."""
import copy
import hashlib
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest

from prepare_warm_assets import (MAX_BYTES, OPTIMIZER_SHA, PROFILE_SHA, SOURCE_COMMIT,
                                 NoRedirect, receive, validate_contract, validate_headers)


BODY = b'v 1 2 3\n'
ROW = {'url': '/api/allen_mesh_lod/382?lod=1', 'kind': 'mesh', 'bytes': len(BODY),
       'sha256': hashlib.sha256(BODY).hexdigest()}
HEADERS = {'Content-Length': str(len(BODY)), 'Content-Type': 'text/plain; charset=utf-8',
           'X-Lod': '0', 'X-Lod-Requested': '1', 'X-Structure-Id': '382'}


class Response(io.BytesIO):
    def __init__(self, body=BODY, headers=None, status=200):
        super().__init__(body)
        self.headers, self.status = headers or HEADERS, status


class Opener:
    def __init__(self, **kwargs):
        self.kwargs, self.calls = kwargs, []
    def open(self, request, timeout):
        self.calls.append((request.full_url, request.get_method(), timeout))
        return Response(**self.kwargs)


class WarmAssetsTests(unittest.TestCase):
    def contract(self):
        return dict(sourceCommit=SOURCE_COMMIT, optimizerSha256=OPTIMIZER_SHA,
                    profileContractSha256=PROFILE_SHA, assets=[copy.deepcopy(ROW)])
    def test_valid_contract(self):
        validate_contract(self.contract())
    def test_rejects_dynamic_or_escaped_urls(self):
        for value in ('/api/optimize', '/api/mri/section', '//external/1',
                      '/api/allen_mesh/../secret', '/api/allen_mesh/1?other=2',
                      '/api/allen_mesh_lod/1?lod=3'):
            with self.subTest(value=value):
                c=self.contract(); c['assets'][0]['url']=value
                with self.assertRaises(ValueError):
                    validate_contract(c)
    def test_duplicate_url_rejected(self):
        c=self.contract(); c['assets'].append(copy.deepcopy(ROW))
        with self.assertRaises(ValueError):
            validate_contract(c)
    def test_wrong_identity_rejected(self):
        for key in ('sourceCommit', 'optimizerSha256', 'profileContractSha256'):
            c=self.contract(); c[key]='wrong'
            with self.assertRaises(ValueError):
                validate_contract(c)
    def test_invalid_sizes_and_sha_rejected(self):
        for key, value in (('bytes',0),('bytes',MAX_BYTES+1),('sha256','../../file')):
            c=self.contract(); c['assets'][0][key]=value
            with self.assertRaises(ValueError):
                validate_contract(c)
    def test_wrong_header_rejected(self):
        for key, value in (('X-Structure-Id','1'),('X-Lod-Requested','2'),
                           ('Content-Length','1'),('Content-Type','application/json'),
                           ('Content-Encoding','gzip')):
            with self.subTest(key=key):
                headers=dict(HEADERS); headers[key]=value
                with self.assertRaises(ValueError):
                    validate_headers(ROW, headers, 200)
    def test_redirect_rejected(self):
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None,None,302,'',{},'https://other/')
    def test_actual_get_verified_and_promoted(self):
        with tempfile.TemporaryDirectory() as d:
            opener=Opener(); root=Path(d)
            result=receive(opener,'http://127.0.0.1:8000',ROW,root,time.monotonic()+3,threading.Lock())
            self.assertEqual(result['status'],'PASS_ACTUAL_RESPONSE_IDENTITY')
            self.assertEqual((root/(ROW['sha256']+'.obj')).read_bytes(),BODY)
            self.assertFalse(list(root.glob('*.partial')))
            self.assertEqual(opener.calls[0][1],'GET')
    def test_alias_still_reads_live_backend(self):
        with tempfile.TemporaryDirectory() as d:
            opener=Opener(); root=Path(d)
            for _ in range(2):
                result=receive(opener,'http://127.0.0.1:8000',ROW,root,time.monotonic()+3,threading.Lock())
                self.assertEqual(result['status'],'PASS_ACTUAL_RESPONSE_IDENTITY')
            self.assertEqual(len(opener.calls),2)
            self.assertEqual(len(list(root.glob('*.obj'))),1)
    def test_corrupt_truncated_and_oversized_bodies_not_promoted(self):
        for body in (b'v 3 2 1\n', BODY[:-1], BODY+b'x'):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as d:
                result=receive(Opener(body=body),'http://127.0.0.1:8000',ROW,Path(d),
                               time.monotonic()+3,threading.Lock())
                self.assertEqual(result['status'],'FAIL')
                self.assertFalse(list(Path(d).glob('*.obj')))
    def test_existing_file_tamper_stops_before_network(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d); (root/(ROW['sha256']+'.obj')).write_bytes(b'v 3 2 1\n')
            opener=Opener()
            result=receive(opener,'http://127.0.0.1:8000',ROW,root,time.monotonic()+3,threading.Lock())
            self.assertEqual(result['status'],'FAIL')
            self.assertEqual(opener.calls,[])
    def test_expired_deadline_stops_before_network(self):
        with tempfile.TemporaryDirectory() as d:
            opener=Opener()
            result=receive(opener,'http://127.0.0.1:8000',ROW,Path(d),time.monotonic()-1,threading.Lock())
            self.assertEqual(result['status'],'FAIL')
            self.assertEqual(opener.calls,[])


if __name__=='__main__':
    unittest.main(verbosity=2)
