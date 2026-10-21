"""Local Comfy transport contracts using exclusively owned HTTP servers.

The rejecting proxy is also ours. These tests never contact a real ComfyUI,
external host, GPU, user image, model or encoder. Real frame bytes exercise the
production download loop while generation and encoding remain fixture stubs.
"""
from __future__ import annotations

from contextlib import ExitStack
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import shutil
import socket
import ssl
import sys
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.parse
import urllib.request
import uuid

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'work/v2_source'))
import comfy_video


class LocalHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        self.server.observations.append(dict(method=self.command, path=self.path,
            body=body, content_type=self.headers.get('Content-Type'),
            authorization=self.headers.get('Authorization'),
            proxy_authorization=self.headers.get('Proxy-Authorization')))
        route = urllib.parse.urlsplit(self.path).path
        status, headers = 200, {}
        if self.server.reject_proxy:
            status, response = 407, b'This exclusively owned proxy rejects traffic.'
        elif route == '/queue':
            response = json.dumps(self.server.queue).encode('utf-8')
        elif route == '/system_stats':
            status, response = 503, b'The readiness check must not query GPU statistics.'
        elif route == '/upload/image':
            response = b'{"name":"fixture.png","subfolder":"input_fixture"}'
        elif route == '/view':
            response = self.server.png
        elif route == '/redirect':
            status, response = 302, b''
            headers['Location'] = self.server.redirect_target
        elif route == '/slow':
            self.server.slow_release.wait(timeout=.5)
            response = b'{}'
        elif route == '/object_info':
            response = b'{}'
        elif route == '/prompt':
            response = b'{"prompt_id":"fixture-job"}'
        elif route == '/history/fixture-job':
            images = [dict(filename='fixture_%05d.png' % index,
                           subfolder='owned_fixture', type='output') for index in range(5)]
            response = json.dumps({'fixture-job': {
                'status': {'status_str': 'success', 'completed': True},
                'outputs': {'10': {'images': images}}}}).encode('utf-8')
        else:
            response = b'{}'
        self.send_response(status)
        self.send_header('Content-Length', str(len(response)))
        self.send_header('Content-Type', 'image/png' if route == '/view' else 'application/json')
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(response)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # A finite-timeout test deliberately closes its own connection.
            pass


class ComfyLocalHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.servers = []
        for reject in (False, True):
            server = ThreadingHTTPServer(('127.0.0.1', 0), LocalHandler)
            server.daemon_threads = True
            server.reject_proxy = reject
            server.observations = []
            server.slow_release = threading.Event()
            thread = threading.Thread(target=server.serve_forever,
                                      kwargs={'poll_interval': .05}, daemon=True)
            thread.start()
            cls.servers.append((server, thread))
        cls.server, cls.proxy = cls.servers[0][0], cls.servers[1][0]
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_address[1]
        cls.proxy_base = 'http://127.0.0.1:%d' % cls.proxy.server_address[1]
        image = Image.new('RGB', (32, 32))
        image.putdata([(x * 7, y * 7, (x + y) * 3)
                       for y in range(32) for x in range(32)])
        output = io.BytesIO()
        image.save(output, format='PNG')
        cls.server.png = output.getvalue()

    @classmethod
    def tearDownClass(cls):
        for server, thread in cls.servers:
            server.slow_release.set()
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def setUp(self):
        self.server.observations.clear()
        self.proxy.observations.clear()
        self.server.queue = {'queue_running': [], 'queue_pending': []}
        self.server.redirect_target = self.base + '/redirect_destination'
        self.server.slow_release.clear()
        self.workspace_root = ROOT / 'work/comfy_local_http_tests'
        self.workspace = self.workspace_root / uuid.uuid4().hex
        self.workspace.mkdir(parents=True)
        self.addCleanup(self.remove_workspace)
        # Force the ordinary urllib proxy path to be observable even for local
        # URLs. The application must explicitly opt out of this proxy path.
        self.proxy_stack = ExitStack()
        self.proxy_stack.enter_context(patch.object(urllib.request, '_opener', None))
        self.proxy_stack.enter_context(patch.object(urllib.request, 'getproxies',
            return_value={'http': self.proxy_base, 'https': self.proxy_base}))
        self.proxy_stack.enter_context(patch.object(urllib.request, 'proxy_bypass', return_value=False))
        self.addCleanup(self.proxy_stack.close)

    def remove_workspace(self):
        self.assertTrue(self.workspace.resolve().is_relative_to(self.workspace_root.resolve()))
        shutil.rmtree(self.workspace)

    def assert_no_proxy_traffic(self):
        self.assertEqual(self.proxy.observations, [], 'A local AI request reached the proxy.')
        for entry in self.server.observations:
            self.assertIsNone(entry['authorization'])
            self.assertIsNone(entry['proxy_authorization'])

    def test_proxy_fixture_really_rejects_an_ordinary_urllib_request(self):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler())
        with self.assertRaises(urllib.error.HTTPError) as caught:
            opener.open(self.base + '/queue', timeout=1)
        self.assertEqual(caught.exception.code, 407)
        caught.exception.close()
        self.assertEqual(len(self.proxy.observations), 1)
        self.assertEqual(self.server.observations, [])

    def test_json_get_bypasses_rejecting_proxy(self):
        result = comfy_video._request(self.base, '/queue', timeout=1)
        self.assertEqual(result, self.server.queue)
        self.assertEqual([entry['path'] for entry in self.server.observations], ['/queue'])
        self.assert_no_proxy_traffic()

    def test_json_post_bypasses_proxy_and_preserves_payload(self):
        payload = {'prompt': {'1': {'class_type': 'Fixture'}}, 'client_id': 'owned-fixture'}
        self.assertEqual(comfy_video._request(self.base, '/prompt', payload, timeout=1),
                         {'prompt_id': 'fixture-job'})
        observed = self.server.observations[0]
        self.assertEqual(observed['method'], 'POST')
        self.assertEqual(observed['content_type'], 'application/json')
        self.assertEqual(json.loads(observed['body']), payload)
        self.assert_no_proxy_traffic()

    def test_reference_upload_bypasses_proxy_and_preserves_image(self):
        reference = self.workspace / 'fixture.png'
        reference.write_bytes(self.server.png)
        self.assertEqual(comfy_video._upload_image(self.base, reference),
                         'input_fixture/fixture.png')
        self.assertEqual(len(self.server.observations), 1)
        observed = self.server.observations[0]
        self.assertEqual(observed['path'], '/upload/image')
        self.assertEqual(observed['method'], 'POST')
        self.assertTrue(observed['content_type'].startswith('multipart/form-data; boundary='))
        self.assertIn(self.server.png, observed['body'])
        self.assert_no_proxy_traffic()

    def test_local_view_returns_exact_binary_bytes_without_proxy(self):
        request = urllib.request.Request(self.base + '/view?filename=owned.png&type=output')
        with comfy_video._local_open(request, timeout=1) as response:
            self.assertEqual(response.read(), self.server.png)
        self.assert_no_proxy_traffic()

    def test_local_requests_do_not_construct_https_or_load_certificate_context(self):
        reference = self.workspace / 'fixture.png'
        reference.write_bytes(self.server.png)
        missing = FileNotFoundError('owned fixture: bundled certificate context is unavailable')
        with patch.object(urllib.request, 'HTTPSHandler', side_effect=missing) as https_handler:
            with patch.object(ssl, 'create_default_context', side_effect=missing) as ssl_context:
                self.assertEqual(comfy_video._request(self.base, '/queue', timeout=1), self.server.queue)
                self.assertEqual(comfy_video._upload_image(self.base, reference),
                                 'input_fixture/fixture.png')
                with comfy_video._local_open(self.base + '/view?filename=owned.png', timeout=1) as response:
                    self.assertEqual(response.read(), self.server.png)
        https_handler.assert_not_called()
        ssl_context.assert_not_called()
        self.assertEqual([urllib.parse.urlsplit(entry['path']).path
                          for entry in self.server.observations],
                         ['/queue', '/upload/image', '/view'])
        self.assert_no_proxy_traffic()

    def test_production_frame_download_loop_uses_local_transport(self):
        work = self.workspace / 'render'
        work.mkdir()
        engine = SimpleNamespace(check=lambda: None, log=lambda message: None)
        def encode(arguments):
            source = Path(arguments[arguments.index('-i') + 1]).parent
            frames = sorted(source.glob('frame_*.png'))
            self.assertEqual(len(frames), 5)
            self.assertTrue(all(frame.read_bytes() == self.server.png for frame in frames))
            Path(arguments[-1]).write_bytes(b'exclusively-owned-encoder-fixture')
        engine.run = encode
        project = SimpleNamespace(ai_root=str(self.workspace), ai_quality='Normál • 832×480',
            video_prompt='A synthetic plant moves.', title='HTTP fixture', format='720p • 16:9')
        graph = {'9': {'class_type': 'VAEDecode', 'inputs': {}}}
        progress = Mock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(comfy_video, 'engine_files'))
            stack.enter_context(patch.object(comfy_video, '_ensure_server', return_value=self.base))
            stack.enter_context(patch.object(comfy_video, '_workflow', return_value=graph))
            stack.enter_context(patch('gpu_health.begin_monitor', return_value=Mock()))
            stack.enter_context(patch('comfy_progress.JobProgress', return_value=progress))
            stack.enter_context(patch('imageio_ffmpeg.get_ffmpeg_exe', return_value='owned-fixture-encoder'))
            result = comfy_video.generate(engine, project, '', None, 0, work, .1, canvas=(32, 32))
        self.assertEqual(result.read_bytes(), b'exclusively-owned-encoder-fixture')
        views = [entry for entry in self.server.observations if entry['path'].startswith('/view?')]
        self.assertEqual(len(views), 5)
        for index, observed in enumerate(views):
            values = urllib.parse.parse_qs(urllib.parse.urlsplit(observed['path']).query)
            self.assertEqual(values, {'filename': ['fixture_%05d.png' % index],
                'subfolder': ['owned_fixture'], 'type': ['output']})
        progress.start.assert_called_once()
        progress.close.assert_called_once()
        self.assert_no_proxy_traffic()

    def test_probe_uses_queue_and_does_not_request_gpu_statistics(self):
        self.assertTrue(comfy_video._probe(self.base))
        self.assertEqual([entry['path'] for entry in self.server.observations], ['/queue'])
        self.assert_no_proxy_traffic()

    def test_probe_accepts_a_valid_busy_queue(self):
        self.server.queue = {'queue_running': [[1, 'other-job', {}, {}]],
                             'queue_pending': [[2, 'waiting-job', {}, {}]]}
        self.assertTrue(comfy_video._probe(self.base))
        self.assert_no_proxy_traffic()

    def test_probe_rejects_incomplete_or_invalid_queue_schema(self):
        invalid = [None, [], {}, {'queue_running': []}, {'queue_pending': []},
            {'queue_running': None, 'queue_pending': []},
            {'queue_running': [], 'queue_pending': {}},
            {'queue_running': ['not a queue entry'], 'queue_pending': []},
            {'queue_running': [], 'queue_pending': [[1, '']]}]
        for value in invalid:
            self.server.queue = value
            with self.subTest(queue=value):
                self.assertFalse(comfy_video._probe(self.base))
        self.assert_no_proxy_traffic()

    def test_redirects_are_not_followed_even_to_another_local_path(self):
        with self.assertRaises((OSError, RuntimeError, ValueError)) as caught:
            with comfy_video._local_open(self.base + '/redirect', timeout=1):
                self.fail('Redirecting response was incorrectly accepted.')
        if hasattr(caught.exception, 'close'):
            caught.exception.close()
        self.assertEqual([entry['path'] for entry in self.server.observations], ['/redirect'])
        self.assert_no_proxy_traffic()

    def test_redirects_are_not_forwarded_to_the_proxy_endpoint(self):
        self.server.redirect_target = self.proxy_base + '/must-never-be-contacted'
        with self.assertRaises((OSError, RuntimeError, ValueError)) as caught:
            comfy_video._local_open(self.base + '/redirect', timeout=1)
        if hasattr(caught.exception, 'close'):
            caught.exception.close()
        self.assert_no_proxy_traffic()

    def test_offloopback_non_http_or_credentialed_urls_fail_before_connect(self):
        invalid = ['https://127.0.0.1:1/queue', 'http://192.0.2.20:1/queue',
            'http://example.invalid/queue', 'file:///C:/unused',
            'http://localhost.example.invalid/queue', 'http://127.0.0.1.example.invalid/queue',
            'http://user:secret@127.0.0.1:1/queue', 'http://user@localhost:1/queue',
            'http://127.0.0.1:1@192.0.2.20/queue']
        with patch('socket.create_connection', side_effect=AssertionError('Invalid URL reached connect')):
            for url in invalid:
                for argument in (url, urllib.request.Request(url)):
                    with self.subTest(url=url, request=type(argument).__name__):
                        with self.assertRaises((ValueError, RuntimeError)):
                            comfy_video._local_open(argument, timeout=1)
        self.assertEqual(self.server.observations, [])
        self.assert_no_proxy_traffic()

    def test_localhost_hostname_is_accepted_without_proxy(self):
        base = 'http://localhost:%d' % self.server.server_address[1]
        self.assertEqual(comfy_video._request(base, '/queue', timeout=1), self.server.queue)
        self.assert_no_proxy_traffic()

    def test_ipv6_loopback_is_accepted_when_available(self):
        class IPv6Server(ThreadingHTTPServer):
            address_family = socket.AF_INET6
        try:
            server = IPv6Server(('::1', 0), LocalHandler)
        except OSError as error:
            self.skipTest('IPv6 loopback is unavailable: ' + type(error).__name__)
        server.daemon_threads = True
        server.reject_proxy = False
        server.observations = []
        server.queue = dict(self.server.queue)
        thread = threading.Thread(target=server.serve_forever,
                                  kwargs={'poll_interval': .05}, daemon=True)
        thread.start()
        try:
            base = 'http://[::1]:%d' % server.server_address[1]
            self.assertEqual(comfy_video._request(base, '/queue', timeout=1), server.queue)
            self.assertEqual([entry['path'] for entry in server.observations], ['/queue'])
            self.assert_no_proxy_traffic()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    def test_nonfinite_or_missing_timeouts_fail_before_connect(self):
        with patch('socket.create_connection', side_effect=AssertionError('Invalid timeout reached connect')):
            for timeout in (None, 0, -.5, float('inf'), float('-inf'), float('nan')):
                with self.subTest(timeout=timeout):
                    with self.assertRaises((ValueError, RuntimeError)):
                        comfy_video._local_open(self.base + '/queue', timeout=timeout)
        self.assertEqual(self.server.observations, [])
        self.assert_no_proxy_traffic()

    def test_small_finite_timeout_is_enforced_by_the_socket(self):
        started = time.monotonic()
        try:
            with self.assertRaises((TimeoutError, urllib.error.URLError)):
                comfy_video._local_open(self.base + '/slow', timeout=.03)
        finally:
            self.server.slow_release.set()
        self.assertLess(time.monotonic() - started, 1.5)
        self.assert_no_proxy_traffic()


if __name__ == '__main__':
    unittest.main()
