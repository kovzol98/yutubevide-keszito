"""HTTP response contracts against an exclusively owned loopback test server.

No live ComfyUI endpoint, model, GPU or encoder is contacted. The local server
checks every method, exact route, payload and JSON content type before replying.
"""
from __future__ import annotations

import json
import sys
import threading
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'work/v2_source'))
import comfy_video
from ytmuhely import Cancelled

FREE_PAYLOAD = {'unload_models': True, 'free_memory': True}


class ContractHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        content_type = self.headers.get('Content-Type')
        try:
            payload = json.loads(raw) if raw else None
        except (ValueError, UnicodeError):
            payload = '<invalid JSON>'
        observation = dict(method=self.command, route=self.path, payload=payload,
                           content_type=content_type)
        self.server.observations.append(observation)
        expected = self.server.expected
        valid = (self.command == expected['method'] and self.path == expected['route']
                 and payload == expected['payload']
                 and (payload is None or content_type == 'application/json'))
        status, body = (self.server.status, self.server.body) if valid else (
            422, b'The request did not match this owned server fixture.')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ComfyRequestHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), ContractHandler)
        cls.server.daemon_threads = True
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def setUp(self):
        self.server.observations = []

    def response(self, route='/free', payload=FREE_PAYLOAD, status=200, body=b''):
        self.server.expected = dict(method='GET' if payload is None else 'POST',
                                    route=route, payload=payload)
        self.server.status, self.server.body = status, body

    def assert_request(self, route='/free', payload=FREE_PAYLOAD):
        self.assertEqual(len(self.server.observations), 1)
        observed = self.server.observations[0]
        self.assertEqual(observed['route'], route)
        self.assertEqual(observed['payload'], payload)
        self.assertEqual(observed['method'], 'GET' if payload is None else 'POST')
        if payload is not None:
            self.assertEqual(observed['content_type'], 'application/json')

    def test_free_post_with_json_payload_accepts_comfy_empty_200(self):
        self.response()
        self.assertEqual(comfy_video._request(self.base, '/free', FREE_PAYLOAD), {})
        self.assert_request()

    def test_free_successful_json_response_is_preserved(self):
        self.response(body=b'{"released":true,"models":2}')
        self.assertEqual(comfy_video._request(self.base, '/free', FREE_PAYLOAD),
                         {'released': True, 'models': 2})
        self.assert_request()

    def test_free_json_null_is_preserved_as_none(self):
        self.response(body=b'null')
        self.assertIsNone(comfy_video._request(self.base, '/free', FREE_PAYLOAD))
        self.assert_request()

    def test_queue_successful_json_response_remains_strict(self):
        body = {'queue_running': [], 'queue_pending': []}
        self.response('/queue', None, body=json.dumps(body).encode('utf-8'))
        self.assertEqual(comfy_video._request(self.base, '/queue'), body)
        self.assert_request('/queue', None)

    def test_empty_queue_response_is_rejected(self):
        self.response('/queue', None)
        with self.assertRaises(json.JSONDecodeError):
            comfy_video._request(self.base, '/queue')
        self.assert_request('/queue', None)

    def test_empty_history_response_is_rejected(self):
        self.response('/history/foo', None)
        with self.assertRaises(json.JSONDecodeError):
            comfy_video._request(self.base, '/history/foo')
        self.assert_request('/history/foo', None)

    def test_empty_free_query_route_is_not_treated_as_exact_free_route(self):
        self.response('/free?unexpected=1')
        with self.assertRaises(json.JSONDecodeError):
            comfy_video._request(self.base, '/free?unexpected=1', FREE_PAYLOAD)
        self.assert_request('/free?unexpected=1')

    def test_malformed_free_response_is_rejected(self):
        self.response(body=b'{not json')
        with self.assertRaises(json.JSONDecodeError):
            comfy_video._request(self.base, '/free', FREE_PAYLOAD)
        self.assert_request()

    def test_whitespace_only_free_response_is_rejected(self):
        self.response(body=b' \r\n\t ')
        with self.assertRaises(json.JSONDecodeError):
            comfy_video._request(self.base, '/free', FREE_PAYLOAD)
        self.assert_request()

    def test_http_400_free_retains_runtime_error_code_detail_and_cause(self):
        self.response(status=400, body=b'invalid release request')
        with self.assertRaises(RuntimeError) as caught:
            comfy_video._request(self.base, '/free', FREE_PAYLOAD)
        self.assertIn('(400)', str(caught.exception))
        self.assertIn('invalid release request', str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, urllib.error.HTTPError)
        self.assert_request()

    def test_http_500_empty_free_is_still_an_error(self):
        self.response(status=500, body=b'')
        with self.assertRaises(RuntimeError) as caught:
            comfy_video._request(self.base, '/free', FREE_PAYLOAD)
        self.assertIn('(500)', str(caught.exception))
        self.assertIsInstance(caught.exception.__cause__, urllib.error.HTTPError)
        self.assert_request()

    def test_http_500_free_retains_server_diagnostic(self):
        self.response(status=500, body=b'model unload failure')
        with self.assertRaisesRegex(RuntimeError, r'\(500\).*model unload failure'):
            comfy_video._request(self.base, '/free', FREE_PAYLOAD)
        self.assert_request()


class ComfyRequestTransportTests(unittest.TestCase):
    # Transport-only mocks avoid making any external or live-server requests.
    base = 'http://127.0.0.1:1'

    def test_explicit_timeout_and_json_request_are_forwarded(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{}'
        with patch.object(comfy_video, '_local_open', return_value=response) as opening:
            self.assertEqual(comfy_video._request(self.base, '/free', FREE_PAYLOAD, timeout=1.75), {})
        request = opening.call_args.args[0]
        self.assertEqual(opening.call_args.kwargs, {'timeout': 1.75})
        self.assertEqual(request.full_url, self.base + '/free')
        self.assertEqual(request.get_method(), 'POST')
        self.assertEqual(json.loads(request.data), FREE_PAYLOAD)

    def test_default_timeout_is_preserved(self):
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'{}'
        with patch.object(comfy_video, '_local_open', return_value=response) as opening:
            comfy_video._request(self.base, '/queue')
        self.assertEqual(opening.call_args.kwargs, {'timeout': 15})

    def test_timeout_cancelled_and_oserror_propagate_unchanged(self):
        for error in (TimeoutError('fixture timeout'), Cancelled(), OSError('fixture I/O failure')):
            with self.subTest(error=type(error).__name__):
                with patch.object(comfy_video, '_local_open', side_effect=error):
                    with self.assertRaises(type(error)) as caught:
                        comfy_video._request(self.base, '/free', FREE_PAYLOAD, timeout=0.25)
                self.assertIs(caught.exception, error)


if __name__ == '__main__':
    unittest.main()
