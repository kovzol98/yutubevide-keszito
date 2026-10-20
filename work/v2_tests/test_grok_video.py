"""Offline protocol/lifecycle tests. No sockets, keys, account or paid requests."""
from __future__ import annotations
import base64
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch
import urllib.error
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]
from PIL import Image
import grok_video as grok

FIXTURES = ROOT / 'work/grok_tests'
FAKE_KEY = 'test-only-never-a-real-xai-key-71928'
PROMPT = 'The person makes one gentle sidestep in natural daylight.'
VIDEO_URL = 'https://vidgen.x.ai/xai-vidgen-bucket/offline-fixture.mp4'
REQUEST_ID = '41eb9a5f-cbd4-9f21-8d59-79005f1e61b7'


class Cancelled(Exception):
    pass


class Engine:
    def __init__(self):
        self.messages = []
        self.stopped = False
    def check(self):
        if self.stopped:
            raise Cancelled('Megszakítva.')
    def log(self, value):
        self.messages.append(value)


class Response:
    def __init__(self, data, status=200, headers=None, after_read=None):
        self.data = io.BytesIO(data)
        self.status = status
        self.headers = headers or {}
        self.after_read = after_read
    def read(self, count=-1):
        chunk = self.data.read(count)
        if self.after_read:
            self.after_read()
        return chunk
    def __enter__(self):
        return self
    def __exit__(self, *args):
        self.close()
    def close(self):
        self.data.close()


def json_response(value):
    return Response(json.dumps(value).encode('utf-8'), headers={'Content-Type': 'application/json'})


class OfflineHTTP:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []
    def __call__(self, request, timeout):
        self.requests.append(request)
        if not self.responses:
            raise AssertionError('Unexpected network operation: offline fixture exhausted.')
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        if callable(response):
            return response(request)
        return response
    @property
    def methods(self):
        return [r.get_method() for r in self.requests]


class GrokTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        FIXTURES.mkdir(parents=True, exist_ok=True)
        cls.fixture_dir = FIXTURES / ('fixture_' + uuid.uuid4().hex)
        cls.fixture_dir.mkdir()
        import imageio_ffmpeg
        cls.ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
        cls.tiny_video = cls.fixture_dir / 'two_real_frames.mp4'
        subprocess.run([str(cls.ffmpeg), '-v', 'error', '-f', 'lavfi', '-i',
            'testsrc2=size=32x32:rate=2', '-frames:v', '2', '-c:v', 'libx264',
            '-threads', '1', '-pix_fmt', 'yuv420p', str(cls.tiny_video)], check=True,
            capture_output=True, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        cls.video_bytes = cls.tiny_video.read_bytes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.fixture_dir)

    def setUp(self):
        self.folder = FIXTURES / ('case_' + uuid.uuid4().hex)
        self.folder.mkdir()
        self.photo = self.folder / 'source.jpg'
        # EXIF 6 rotates the entire 60x100 photo into a 100x60 photo.
        exif = Image.Exif()
        exif[274] = 6
        Image.new('RGB', (60, 100), (130, 75, 40)).save(self.photo, exif=exif)
        self.job = self.folder / 'grok_job.json'
        self.engine = Engine()

    def tearDown(self):
        self.assertTrue(self.folder.resolve().is_relative_to(FIXTURES.resolve()))
        shutil.rmtree(self.folder)

    def media(self, body=None, **kwargs):
        body = self.video_bytes if body is None else body
        headers = kwargs.pop('headers', {'Content-Type': 'video/mp4', 'Content-Length': str(len(body))})
        return Response(body, headers=headers, **kwargs)

    def done(self, video=None):
        return json_response({'status': 'done', 'video': video if video is not None else {
            'url': VIDEO_URL, 'respect_moderation': True}})

    def generate(self, http, **kwargs):
        arguments = dict(engine=self.engine, prompt=PROMPT, image=self.photo,
            duration=5.1, resolution='720p', job_file=self.job, key=FAKE_KEY,
            poll_interval=.001, _opener=http)
        arguments.update(kwargs)
        return grok.generate(**arguments)

    def submit_done(self, *extras):
        return OfflineHTTP(json_response({'request_id': REQUEST_ID}), self.done(), self.media(), *extras)

    def state(self):
        return json.loads(self.job.read_text(encoding='utf-8'))

    def assert_no_secret(self, *errors):
        self.assertNotIn(FAKE_KEY, '\n'.join(self.engine.messages))
        for error in errors:
            self.assertNotIn(FAKE_KEY, str(error))
        for path in self.folder.glob('*.json'):
            self.assertNotIn(FAKE_KEY, path.read_text(encoding='utf-8'))

    def test_payload_full_exif_photo_and_real_mp4_download(self):
        http = OfflineHTTP(json_response({'request_id': REQUEST_ID}), json_response({'status': 'pending'}),
                           self.done(), self.media())
        output, manifest = self.generate(http)
        self.assertEqual(http.methods, ['POST', 'GET', 'GET', 'GET'])
        payload = json.loads(http.requests[0].data)
        self.assertEqual(payload['model'], grok.MODEL)
        self.assertEqual(payload['duration'], 6)
        self.assertEqual(payload['resolution'], '720p')
        self.assertIs(payload['generate_audio'], False)
        self.assertNotIn('aspect_ratio', payload)
        data_url = payload['image']['url']
        uploaded = Image.open(io.BytesIO(base64.b64decode(data_url.split(',', 1)[1])))
        self.assertEqual(uploaded.size, (100, 60))
        self.assertEqual(manifest['uploaded_image_size'], [100, 60])
        self.assertIs(manifest['face_replacement'], False)
        self.assertEqual(output.read_bytes(), self.video_bytes)
        self.assertEqual(manifest['status'], 'downloaded')
        self.assertNotIn(PROMPT, self.job.read_text(encoding='utf-8'))
        self.assertNotIn('data:image', self.job.read_text(encoding='utf-8'))
        for request in http.requests[:-1]:
            self.assertTrue(request.full_url.startswith('https://api.x.ai/v1/videos/'))
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + FAKE_KEY)
        self.assertIsNone(http.requests[-1].get_header('Authorization'))
        decoded = subprocess.run([str(self.ffmpeg), '-v', 'error', '-i', str(output),
            '-f', 'null', '-'], capture_output=True,
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(decoded.returncode, 0, decoded.stderr)
        self.assert_no_secret()

    def test_public_adapter_returns_path(self):
        output = grok.generate_scene(self.engine, self.photo, PROMPT, 5, '1080p',
            self.folder, FAKE_KEY, _opener=self.submit_done(), poll_interval=.001)
        self.assertIsInstance(output, Path)
        self.assertTrue(output.is_file())
        self.assertEqual(self.state()['resolution'], '1080p')

    def test_cached_repeat_and_resume_do_not_contact_service(self):
        output, _ = self.generate(self.submit_done())
        no_http = OfflineHTTP()
        repeated, _ = self.generate(no_http)
        resumed = grok.resume_existing(self.engine, self.job, FAKE_KEY, _opener=no_http)
        self.assertEqual(output, repeated)
        self.assertEqual(output, resumed)
        self.assertEqual(no_http.methods, [])

    def test_cancelled_pending_job_resumes_with_get_only(self):
        def cancel_after_poll(request):
            self.engine.stopped = True
            return json_response({'status': 'pending'})
        http = OfflineHTTP(json_response({'request_id': REQUEST_ID}), cancel_after_poll)
        with self.assertRaises(Cancelled):
            self.generate(http)
        state = self.state()
        self.assertEqual(state['request_id'], REQUEST_ID)
        self.assertEqual(state['status'], 'pending')
        self.assertEqual(state['local_status'], 'waiting_stopped')
        self.assertIn(grok.REMOTE_CONTINUES, self.engine.messages)
        self.engine.stopped = False
        resume_http = OfflineHTTP(self.done(), self.media())
        output = grok.resume_existing(self.engine, self.job, FAKE_KEY, _opener=resume_http)
        self.assertTrue(output.is_file())
        self.assertEqual(resume_http.methods, ['GET', 'GET'])
        self.assert_no_secret()

    def test_timed_out_pending_job_keeps_id_and_never_posts_again(self):
        http = OfflineHTTP(json_response({'request_id': REQUEST_ID}), json_response({'status': 'pending'}))
        with self.assertRaises(grok.GrokError) as error:
            self.generate(http, timeout=.002, poll_interval=.01)
        self.assertEqual(self.state()['request_id'], REQUEST_ID)
        self.assertEqual(self.state()['local_status'], 'timed_out')
        self.assertIn('számlázható', str(error.exception))
        self.assertEqual(http.methods, ['POST', 'GET'])
        resume_http = OfflineHTTP(self.done(), self.media())
        self.generate(resume_http)
        self.assertEqual(resume_http.methods, ['GET', 'GET'])

    def test_invalid_duration_resolution_key_and_prompt_never_post(self):
        cases = [dict(duration=x) for x in (0, -1, 15.1, float('nan'), float('inf'), True, '5')]
        cases += [dict(resolution='4k'), dict(key='short'), dict(key='valid\nheader-key'),
                  dict(prompt=''), dict(prompt='x' * 16001), dict(timeout=0), dict(poll_interval=61)]
        for case in cases:
            with self.subTest(case=case):
                http = OfflineHTTP()
                with self.assertRaises(ValueError):
                    self.generate(http, **case)
                self.assertEqual(http.methods, [])
                self.assertFalse(self.job.exists())

    def test_preflight_is_local_and_exposes_no_key_or_base64(self):
        metadata = grok.preflight_image(self.photo)
        self.assertEqual(metadata['uploaded_image_size'], [100, 60])
        self.assertEqual(grok.validate_options(PROMPT, 5.01, '720p', FAKE_KEY), (PROMPT, 6, '720p'))
        self.assertNotIn('data:image', json.dumps(metadata))
        self.assertFalse(self.job.exists())

    def test_bad_oversized_and_animated_images_never_post(self):
        corrupt = self.folder / 'corrupt.png'
        corrupt.write_bytes(b'not-an-image')
        empty = self.folder / 'empty.png'
        empty.write_bytes(b'')
        animated = self.folder / 'animation.gif'
        Image.new('RGB', (10, 10), 'red').save(animated, save_all=True,
            append_images=[Image.new('RGB', (10, 10), 'green')], duration=100, loop=0)
        for photo in (corrupt, empty, animated, self.folder / 'missing.jpg'):
            with self.subTest(photo=photo.name):
                http = OfflineHTTP()
                with self.assertRaises(ValueError):
                    self.generate(http, image=photo)
                self.assertEqual(http.methods, [])
        with patch.object(grok, 'MAX_IMAGE_BYTES', 12):
            with self.assertRaises(ValueError):
                self.generate(OfflineHTTP())
        with patch.object(grok, 'MAX_IMAGE_PIXELS', 12):
            with self.assertRaises(ValueError):
                self.generate(OfflineHTTP())

    def test_http_post_failure_is_sanitized_and_cannot_duplicate(self):
        body = io.BytesIO(('reflected-secret:' + FAKE_KEY).encode('ascii'))
        error = urllib.error.HTTPError('https://api.x.ai/v1/videos/generations', 503,
            'server reflected ' + FAKE_KEY, {}, body)
        http = OfflineHTTP(error)
        with self.assertRaises(grok.GrokError) as failure:
            self.generate(http)
        self.assertEqual(http.methods, ['POST'])
        self.assertEqual(self.state()['status'], 'submission_unknown')
        second = OfflineHTTP()
        with self.assertRaises(grok.GrokError):
            self.generate(second)
        self.assertEqual(second.methods, [])
        self.assert_no_secret(failure.exception)

    def test_network_post_failure_does_not_retry(self):
        http = OfflineHTTP(urllib.error.URLError(FAKE_KEY))
        with self.assertRaises(grok.GrokError) as failure:
            self.generate(http)
        self.assertEqual(http.methods, ['POST'])
        self.assertEqual(self.state()['status'], 'submission_unknown')
        self.assert_no_secret(failure.exception)

    def test_missing_invalid_or_secret_request_id_is_not_persisted(self):
        for value in (None, 'bad/id', FAKE_KEY, {'bad': 'shape'}):
            with self.subTest(value=repr(value)):
                self.job.unlink(missing_ok=True)
                http = OfflineHTTP(json_response({'request_id': value}))
                with self.assertRaises(grok.GrokError) as failure:
                    self.generate(http)
                self.assertIsNone(self.state()['request_id'])
                self.assertEqual(http.methods, ['POST'])
                self.assert_no_secret(failure.exception)

    def test_response_json_is_bounded_and_must_be_object(self):
        for raw in (b'', b'[]', b'<html>error</html>', b'x' * (grok.MAX_JSON_BYTES + 1)):
            with self.subTest(length=len(raw)):
                self.job.unlink(missing_ok=True)
                with self.assertRaises(grok.GrokError):
                    self.generate(OfflineHTTP(Response(raw)))
                self.assertEqual(self.state()['status'], 'submission_unknown')

    def test_changed_request_does_not_overwrite_or_resubmit(self):
        self.generate(self.submit_done())
        original = self.job.read_bytes()
        http = OfflineHTTP()
        with self.assertRaises(grok.GrokError):
            self.generate(http, duration=9)
        self.assertEqual(http.methods, [])
        self.assertEqual(self.job.read_bytes(), original)

    def test_terminal_and_unknown_states_are_not_success(self):
        for status in ('failed', 'expired', 'unsupported', None):
            with self.subTest(status=status):
                self.job.unlink(missing_ok=True)
                http = OfflineHTTP(json_response({'request_id': REQUEST_ID}), json_response({'status': status}))
                with self.assertRaises(grok.GrokError):
                    self.generate(http)
                self.assertEqual(http.methods, ['POST', 'GET'])
                expected = status if status in ('failed', 'expired') else 'unknown_status'
                self.assertEqual(self.state()['status'], expected)
                resume = OfflineHTTP()
                with self.assertRaises(grok.GrokError):
                    grok.resume_existing(self.engine, self.job, FAKE_KEY, _opener=resume)
                self.assertEqual(resume.methods, [])

    def test_done_requires_moderation_approval_and_valid_url(self):
        cases = [{'url': VIDEO_URL}, {'url': VIDEO_URL, 'respect_moderation': False},
                 {'url': VIDEO_URL, 'respect_moderation': 'true'}, {'respect_moderation': True}, None]
        for video in cases:
            with self.subTest(video=video):
                self.job.unlink(missing_ok=True)
                http = OfflineHTTP(json_response({'request_id': REQUEST_ID}),
                    json_response({'status': 'done', 'video': video}))
                with self.assertRaises(grok.GrokError):
                    self.generate(http)
                self.assertEqual(http.methods, ['POST', 'GET'])

    def test_untrusted_media_urls_never_download(self):
        for url in ('http://vidgen.x.ai/video.mp4', 'https://localhost/video.mp4',
                    'https://vidgen.x.ai.evil.example/video.mp4', 'https://vidgen.x.ai:444/video.mp4',
                    'https://secret@vidgen.x.ai/video.mp4', 'https://vidgen.x.ai/video.mp4#fragment',
                    'https://127.0.0.1/video.mp4', 'https://VIDGEN.X.AI/video.mp4\nBad: header'):
            with self.subTest(url=url):
                self.job.unlink(missing_ok=True)
                http = OfflineHTTP(json_response({'request_id': REQUEST_ID}),
                    self.done({'url': url, 'respect_moderation': True}))
                with self.assertRaises(grok.GrokError):
                    self.generate(http)
                self.assertEqual(http.methods, ['POST', 'GET'])

    def test_download_rejects_bad_type_length_header_and_truncation(self):
        cases = [(b'<html>not-video</html>' * 3, {'Content-Type': 'text/html'}),
                 (self.video_bytes, {'Content-Type': 'video/mp4', 'Content-Length': '0'}),
                 (self.video_bytes, {'Content-Type': 'video/mp4', 'Content-Length': str(grok.MAX_VIDEO_BYTES + 1)}),
                 (self.video_bytes, {'Content-Type': 'video/mp4', 'Content-Length': 'invalid'}),
                 (b'', {'Content-Type': 'video/mp4'}),
                 (b'x' * 50, {'Content-Type': 'video/mp4'}),
                 (self.video_bytes, {'Content-Type': 'video/mp4', 'Content-Length': str(len(self.video_bytes) + 20)})]
        for data, headers in cases:
            with self.subTest(headers=headers, size=len(data)):
                self.job.unlink(missing_ok=True)
                http = OfflineHTTP(json_response({'request_id': REQUEST_ID}), self.done(), self.media(data, headers=headers))
                with self.assertRaises(grok.GrokError):
                    self.generate(http)
                self.assertEqual(self.state()['status'], 'done')
                self.assertFalse(list(self.folder.glob('*.part')))
                self.assertFalse((self.folder / 'grok_job_video.mp4').exists())

    def test_download_without_content_length_is_still_bounded(self):
        http = OfflineHTTP(json_response({'request_id': REQUEST_ID}), self.done(),
            self.media(headers={'Content-Type': 'video/mp4'}))
        with patch.object(grok, 'MAX_VIDEO_BYTES', len(self.video_bytes) - 1):
            with self.assertRaises(grok.GrokError):
                self.generate(http)
        self.assertFalse(list(self.folder.glob('*.part')))

    def test_media_redirects_keep_auth_absent_and_stay_on_allowlist(self):
        http = OfflineHTTP(json_response({'request_id': REQUEST_ID}), self.done(),
            Response(b'', 302, {'Location': '/next.mp4'}), self.media())
        self.generate(http)
        self.assertEqual(len(http.requests), 4)
        self.assertEqual(http.requests[-1].full_url, 'https://vidgen.x.ai/next.mp4')
        self.assertIsNone(http.requests[-2].get_header('Authorization'))
        self.assertIsNone(http.requests[-1].get_header('Authorization'))

    def test_untrusted_redirect_is_not_followed(self):
        http = OfflineHTTP(json_response({'request_id': REQUEST_ID}), self.done(),
            Response(b'', 302, {'Location': 'https://localhost/private.mp4'}))
        with self.assertRaises(grok.GrokError):
            self.generate(http)
        self.assertEqual(len(http.requests), 3)
        self.assertEqual(self.state()['status'], 'done')

    def test_redirect_limit_and_http_error_redirect_handling(self):
        redirect = lambda: urllib.error.HTTPError(VIDEO_URL, 302, 'redirect', {'Location': '/next.mp4'}, io.BytesIO())
        http = OfflineHTTP(json_response({'request_id': REQUEST_ID}), self.done(),
            redirect(), redirect(), redirect(), redirect())
        with self.assertRaises(grok.GrokError):
            self.generate(http)
        self.assertEqual(len(http.requests), 6)
        self.assertFalse(list(self.folder.glob('*.part')))

    def test_api_redirect_is_never_followed(self):
        error = urllib.error.HTTPError('https://api.x.ai/v1/videos/generations', 307,
            FAKE_KEY, {'Location': 'https://evil.example/'}, io.BytesIO())
        http = OfflineHTTP(error)
        with self.assertRaises(grok.GrokError) as failure:
            self.generate(http)
        self.assertEqual(http.methods, ['POST'])
        self.assertIsNone(grok._NoRedirect().redirect_request(None, None, 307, '', {}, 'https://evil.example'))
        self.assert_no_secret(failure.exception)

    def test_cancel_download_preserves_done_id_and_removes_partial(self):
        http = OfflineHTTP(json_response({'request_id': REQUEST_ID}), self.done(),
            self.media(after_read=lambda: setattr(self.engine, 'stopped', True)))
        with self.assertRaises(Cancelled):
            self.generate(http)
        self.assertEqual(self.state()['status'], 'done')
        self.assertEqual(self.state()['request_id'], REQUEST_ID)
        self.assertEqual(self.state()['local_status'], 'waiting_stopped')
        self.assertFalse(list(self.folder.glob('*.part')))
        self.assertFalse((self.folder / 'grok_job_video.mp4').exists())
        self.engine.stopped = False
        resume = OfflineHTTP(self.done(), self.media())
        grok.resume_existing(self.engine, self.job, FAKE_KEY, _opener=resume)
        self.assertEqual(resume.methods, ['GET', 'GET'])

    def test_tampered_cached_video_redownloads_without_post(self):
        output, _ = self.generate(self.submit_done())
        output.write_bytes(b'tampered cache')
        resume = OfflineHTTP(self.done(), self.media())
        result = grok.resume_existing(self.engine, self.job, FAKE_KEY, _opener=resume)
        self.assertEqual(result.read_bytes(), self.video_bytes)
        self.assertEqual(resume.methods, ['GET', 'GET'])

    def test_missing_and_corrupt_job_never_submit(self):
        http = OfflineHTTP()
        with self.assertRaises(grok.GrokError):
            grok.resume_existing(self.engine, self.job, FAKE_KEY, _opener=http)
        self.job.write_text('not json', encoding='utf-8')
        with self.assertRaises(grok.GrokError):
            grok.resume_existing(self.engine, self.job, FAKE_KEY, _opener=http)
        self.assertEqual(http.methods, [])

    def test_text_to_video_omits_image_and_preserves_shorts_ratio(self):
        http=self.submit_done()
        output,state=self.generate(http,image=None,aspect_ratio='9:16')
        payload=json.loads(http.requests[0].data)
        self.assertNotIn('image',payload)
        self.assertEqual(payload['aspect_ratio'],'9:16')
        self.assertFalse(payload['generate_audio'])
        self.assertFalse(state['image_input'])
        self.assertEqual(output.read_bytes(),self.video_bytes)
        self.assert_no_secret()

    def test_invalid_text_aspect_ratio_never_submits(self):
        http=OfflineHTTP()
        with self.assertRaises(ValueError):
            self.generate(http,image=None,aspect_ratio='untrusted')
        self.assertEqual(http.methods,[])
        self.assertFalse(self.job.exists())

    def test_text_and_photo_jobs_cannot_share_a_paid_request(self):
        self.generate(self.submit_done())
        http=OfflineHTTP()
        with self.assertRaises(grok.GrokError):
            self.generate(http,image=None)
        self.assertEqual(http.methods,[])

    def test_os_lock_prevents_duplicate_submission(self):
        http = OfflineHTTP()
        with grok._job_lock(self.job):
            with self.assertRaises(grok.GrokError):
                self.generate(http)
        self.assertEqual(http.methods, [])
        self.assertFalse(self.job.exists())


if __name__ == '__main__':
    unittest.main(verbosity=2)
