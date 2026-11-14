"""Bounded synthetic HTTP-open recovery tests, not real application evidence."""
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

from prepare_warm_assets import open_with_bounded_recovery, receive
from test_warm_assets import BODY, ROW, Response


class SequenceOpener:
    def __init__(self, values):
        self.values, self.calls = list(values), 0
    def open(self, request, timeout):
        self.calls += 1
        result = self.values.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def failure(code=500, body=b'{"requestId":"fixture"}', headers=None):
    return urllib.error.HTTPError('http://127.0.0.1/api/allen_mesh/382', code, 'fixture', headers or {}, io.BytesIO(body))


class RecoveryTests(unittest.TestCase):
    def run_receive(self, opener):
        with tempfile.TemporaryDirectory() as d, patch('prepare_warm_assets.time.sleep'):
            result = receive(opener, 'http://127.0.0.1:8000', ROW, Path(d), time.monotonic()+20, threading.Lock())
            return result, list(Path(d).glob('*.obj'))
    def test_one_failure_recovered_with_first_error_preserved(self):
        opener = SequenceOpener([failure(), Response()])
        result, files = self.run_receive(opener)
        self.assertEqual(result['status'], 'PASS_ACTUAL_RESPONSE_IDENTITY')
        self.assertEqual(opener.calls, 2)
        self.assertEqual(len(files), 1)
        self.assertEqual(result['attempts'][0]['http_status'], 500)
        self.assertIn('fixture', result['attempts'][0]['body_prefix_utf8'])
        self.assertEqual(result['attempts'][1]['http_status'], 200)
    def test_second_failure_stops_without_third_attempt(self):
        opener = SequenceOpener([failure(), failure(), Response()])
        result, files = self.run_receive(opener)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(opener.calls, 2)
        self.assertEqual(files, [])
    def test_forbidden_rate_and_nontransient_status_not_retried(self):
        for code in [403, 404, 429]:
            with self.subTest(code=code):
                opener = SequenceOpener([failure(code), Response()])
                result, _ = self.run_receive(opener)
                self.assertEqual(result['status'], 'FAIL')
                self.assertEqual(opener.calls, 1)
    def test_retry_after_stops_without_ignoring_server_instruction(self):
        opener = SequenceOpener([failure(503, headers={'Retry-After': '120'}), Response()])
        result, _ = self.run_receive(opener)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(opener.calls, 1)
    def test_corrupt_success_body_not_retried_or_promoted(self):
        opener = SequenceOpener([Response(body=BODY[:-1]), Response()])
        result, files = self.run_receive(opener)
        self.assertEqual(result['status'], 'FAIL')
        self.assertEqual(opener.calls, 1)
        self.assertEqual(files, [])
    def test_error_body_is_bounded_and_marked(self):
        opener = SequenceOpener([failure(body=b'x'*9000), Response()])
        result, _ = self.run_receive(opener)
        self.assertTrue(result['attempts'][0]['body_truncated'])
        self.assertEqual(len(result['attempts'][0]['body_prefix_utf8']), 8192)
    def test_expired_deadline_and_post_never_reach_network(self):
        opener = SequenceOpener([Response()])
        with self.assertRaises(ValueError):
            open_with_bounded_recovery(opener, urllib.request.Request('http://127.0.0.1', method='POST'), time.monotonic()+20, [])
        with self.assertRaises(ValueError):
            open_with_bounded_recovery(opener, urllib.request.Request('http://127.0.0.1'), time.monotonic()-1, [])
        self.assertEqual(opener.calls, 0)
