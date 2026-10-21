"""Dance choices must reach the existing video engines and durable queue intact.

These are protocol and persistence checks. They do not start a model or establish
that a generated person performs the requested choreography accurately.
"""
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'work/packages'), str(ROOT / 'work/v2_source')]

from PIL import Image
import comfy_video
import dance_moves as dance
from image_motion import (DANCE_MOTION, DEFAULT_IMAGE_MOTION,
                          IMAGE_MOTION_STYLES, image_motion_prompt, resolved_motion)
import realistic_video
import render_queue
import ytmuhely as app


def variants():
    for label in dance.DANCE_MOVES:
        for group in (False, True):
            for intensity in dance.DANCE_INTENSITIES:
                yield label, group, intensity, dance.dance_prompt(
                    label, group=group, intensity=intensity)


class DanceEngineFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.info = json.loads((ROOT / 'work/realism/quality_object_info.json').read_text(
            encoding='utf-8'))
        cls.reference = cls.info['LoadImage']['input']['required']['image'][0][0]

    def setUp(self):
        self.parent = ROOT / 'work/dance_flow_tests'
        self.folder = self.parent / uuid.uuid4().hex
        self.folder.mkdir(parents=True)
        self.photo = self.folder / 'source.png'
        Image.new('RGB', (96, 160), (140, 110, 75)).save(self.photo)

    def tearDown(self):
        self.assertEqual(self.folder.resolve().parent, self.parent.resolve())
        shutil.rmtree(self.folder)

    def project(self, prompt, **changes):
        return app.Project(**dict(dict(title='Dance flow', images=[str(self.photo)],
            image_motion=DANCE_MOTION, video_prompt=prompt, narration='Nincs narráció',
            output=str(self.folder / 'exports'), lighting='Eredeti világítás'), **changes))

    def test_every_catalog_variant_resolves_to_whole_body_and_keeps_choreography(self):
        fallback = IMAGE_MOTION_STYLES[DANCE_MOTION]['fallback']
        self.assertEqual(len(list(variants())), 32)
        for label, group, intensity, selected in variants():
            with self.subTest(label=label, group=group, intensity=intensity):
                self.assertEqual(resolved_motion(DEFAULT_IMAGE_MOTION, selected), DANCE_MOTION)
                actual = image_motion_prompt(DANCE_MOTION, selected)
                self.assertTrue(actual.startswith(selected))
                self.assertNotIn(fallback, actual)
                self.assertIn('Fixed camera', actual)
                self.assertIn('When the full body and feet are visible, keep them visible throughout.', actual)
                self.assertIn('preserve that crop', actual)
                self.assertIn('clothing', actual)

    def test_legacy_generic_dance_still_gets_a_complete_body_action(self):
        actual = image_motion_prompt(DANCE_MOTION, 'dance')
        self.assertIn(IMAGE_MOTION_STYLES[DANCE_MOTION]['fallback'], actual)
        self.assertIn('both feet stay visible', actual)

    def test_appended_notes_keep_catalog_action_without_a_conflicting_generic_dance(self):
        fallback = IMAGE_MOTION_STYLES[DANCE_MOTION]['fallback']
        for label, group, intensity, selected in variants():
            with self.subTest(label=label, group=group, intensity=intensity):
                edited = selected + ' A bird flies behind the dancer.'
                self.assertFalse(dance.is_dance_prompt(edited))
                actual = image_motion_prompt(DANCE_MOTION, edited)
                self.assertEqual(actual, edited)
                self.assertNotIn(fallback, actual)
                self.assertNotIn('feet step side to side', actual)
                self.assertIn('preserve that crop', actual)
                self.assertIn('do not merge or duplicate them' if group else
                              'without adding or removing anyone', actual)

    def test_edited_catalog_action_reaches_both_workflows_and_cloud_preparation(self):
        fallback = IMAGE_MOTION_STYLES[DANCE_MOTION]['fallback']
        action = 'Keep both feet planted and trace a slow circle with one hand.'
        for label, group, intensity, selected in variants():
            edited = selected.replace(dance.DANCE_MOVES[label]['action'], action)
            with self.subTest(label=label, group=group, intensity=intensity):
                self.assertFalse(dance.is_dance_prompt(edited))
                self.assertTrue(dance.has_dance_preservation(edited))
                actual = image_motion_prompt(DANCE_MOTION, edited)
                self.assertEqual(actual, edited)
                self.assertIn(action, actual)
                self.assertNotIn(fallback, actual)
                self.assertNotIn('feet step side to side', actual)
                self.assertIn('preserve that crop', actual)
                self.assertIn('do not merge or duplicate them' if group else
                              'without adding or removing anyone', actual)
                for builder in (comfy_video._workflow, realistic_video.workflow):
                    graph = builder(self.info, actual, 480, 640, 81, 4, 42,
                        'offline-edited-dance-check', self.reference, image_motion=DANCE_MOTION)
                    self.assertEqual(graph['5']['inputs']['text'], edited)
                self.assertEqual(app.Engine().grok_prompt(self.project(edited),
                    'Unrelated speech.', self.photo, 0), edited)

    def test_all_variants_reach_both_real_workflow_positive_encoders(self):
        fallback = IMAGE_MOTION_STYLES[DANCE_MOTION]['fallback']
        for label, group, intensity, selected in variants():
            prompt = image_motion_prompt(DANCE_MOTION, selected)
            for builder in (comfy_video._workflow, realistic_video.workflow):
                with self.subTest(label=label, group=group, intensity=intensity,
                                  engine=builder.__module__):
                    graph = builder(self.info, prompt, 480, 640, 81, 4, 42,
                        'offline-dance-check', self.reference, image_motion=DANCE_MOTION)
                    self.assertEqual(graph['5']['class_type'], 'CLIPTextEncode')
                    self.assertEqual(graph['5']['inputs']['text'], prompt)
                    self.assertNotIn(fallback, graph['5']['inputs']['text'])
                    self.assertIn('LoadImage', [node['class_type'] for node in graph.values()])
                    self.assertGreaterEqual(graph['9']['inputs']['temporal_size'], 81)

    def test_local_generation_preparation_passes_the_selected_move_to_graph_builder(self):
        class CapturedBeforeSampling(Exception):
            pass

        # Exercise the production prompt planning and reference preparation.
        # The fake builder stops execution before any sampling/network request.
        for index, (label, group, intensity, selected) in enumerate(variants()):
            with self.subTest(label=label, group=group, intensity=intensity):
                project = self.project(selected, ai_quality='Normál • 832×480')
                work = self.folder / ('clip_' + str(index)) / 'temporary'
                work.mkdir(parents=True)
                captured = []

                def builder(info, prompt, *args, **kwargs):
                    captured.append(prompt)
                    raise CapturedBeforeSampling()

                with patch.object(comfy_video, 'engine_files'), \
                        patch.object(comfy_video, 'selected_models', return_value=comfy_video.MODEL_NAMES), \
                        patch.object(comfy_video, '_ensure_server', return_value='http://127.0.0.1:1'), \
                        patch.object(comfy_video, '_request', return_value=self.info) as request, \
                        patch.object(comfy_video, '_upload_image', return_value=self.reference), \
                        patch.object(comfy_video, '_workflow', side_effect=builder):
                    with self.assertRaises(CapturedBeforeSampling):
                        comfy_video.generate(app.Engine(), project, 'Speech must not become the dance.',
                            self.photo, 0, work, 2)
                self.assertEqual(captured, [image_motion_prompt(DANCE_MOTION, selected)])
                self.assertNotIn('Speech must not become', captured[0])
                self.assertEqual([call.args[1] for call in request.call_args_list],
                                 ['/object_info', '/object_info/LoadImage'])
                saved = work.parent / 'AI_jelenetek/jelenet_001/prompt.txt'
                self.assertEqual(saved.read_text(encoding='utf-8'), captured[0])

    def test_engine_dispatch_never_selects_head_only_animation_for_a_catalog_move(self):
        for label, group, intensity, selected in variants():
            with self.subTest(label=label, group=group, intensity=intensity):
                project = self.project(selected)
                with patch('portrait_video.generate') as portrait, \
                        patch('realistic_video.generate', return_value='whole-scene') as quality:
                    result = app.Engine().generate_clip(project, '', self.photo, 0, self.folder, 5)
                self.assertEqual(result, 'whole-scene')
                self.assertEqual(quality.call_args.args[1].video_prompt, selected)
                portrait.assert_not_called()

    def test_optional_cloud_prompt_preserves_all_variants_without_any_cloud_request(self):
        fallback = IMAGE_MOTION_STYLES[DANCE_MOTION]['fallback']
        for label, group, intensity, selected in variants():
            with self.subTest(label=label, group=group, intensity=intensity):
                actual = app.Engine().grok_prompt(self.project(selected), 'Unrelated speech.',
                                                   self.photo, 0)
                self.assertEqual(actual, image_motion_prompt(DANCE_MOTION, selected))
                self.assertNotIn(fallback, actual)

    def test_multiple_photos_and_scene_specific_moves_keep_their_order(self):
        selected = [value[3] for value in list(variants())[::4]]
        project = self.project('\n\n'.join(selected), images=[str(self.photo)] * 8)
        chunks, lengths = app.scene_chunks(project, {})
        self.assertEqual(len(chunks), 8)
        self.assertEqual(lengths, [])
        for index, prompt in enumerate(selected):
            self.assertEqual(app.Engine().grok_prompt(project, '', self.photo, index),
                             image_motion_prompt(DANCE_MOTION, prompt))

    def test_dance_project_json_roundtrip_uses_only_existing_project_fields(self):
        original_fields = set(asdict(app.Project()))
        for label, group, intensity, selected in variants():
            with self.subTest(label=label, group=group, intensity=intensity):
                data = asdict(self.project(selected))
                self.assertEqual(set(data), original_fields)
                restored = app.Project(**json.loads(json.dumps(data, ensure_ascii=False)))
                self.assertEqual(restored.video_prompt, selected)
                self.assertEqual(restored.image_motion, DANCE_MOTION)
                self.assertEqual(app.Engine().grok_prompt(restored, '', self.photo, 0),
                                 image_motion_prompt(DANCE_MOTION, selected))

    def test_all_moves_survive_persistent_queue_and_the_worker_receives_exact_prompts(self):
        path = self.folder / 'queue.json'
        store = render_queue.QueueStore(path)
        try:
            for label, group, intensity, selected in variants():
                store.add(asdict(self.project(selected)))
        finally:
            store.close()
        received = []

        class FakeEngine:
            def __init__(engine, log, progress):
                engine.log, engine.progress = log, progress
                import threading
                engine.cancel = threading.Event()

            def build(engine, project):
                self.assertIsInstance(project, app.Project)
                self.assertEqual(project.image_motion, DANCE_MOTION)
                self.assertTrue(dance.is_dance_prompt(project.video_prompt))
                received.append(project.video_prompt)
                result = self.folder / ('completed_' + str(len(received)))
                result.mkdir()
                (result / 'video.mp4').write_bytes(b'queue result fixture; no media rendered')
                return result

        store = render_queue.QueueStore(path)
        try:
            runner = render_queue.QueueRunner(store, FakeEngine)
            with patch.object(render_queue, '_keep_awake', return_value=True):
                runner.start()
                self.assertTrue(runner.join(15))
            self.assertFalse(runner.last_error)
            self.assertEqual([job['status'] for job in store.items()], ['complete'] * 32)
        finally:
            store.close()
        self.assertEqual(received, [selected for _, _, _, selected in variants()])
        self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['schema'], 1)

    def test_old_schema_one_queue_keeps_plain_custom_prompts_unchanged(self):
        path = self.folder / 'queue.json'
        old = self.project('The person waves slowly.', image_motion='Jelenetleírás szerinti mozgás')
        store = render_queue.QueueStore(path)
        try:
            store.add(asdict(old))
        finally:
            store.close()
        before = path.read_bytes()
        store = render_queue.QueueStore(path)
        try:
            saved = store.items()[0]['project']
            self.assertEqual(saved['video_prompt'], old.video_prompt)
            self.assertEqual(saved['image_motion'], old.image_motion)
        finally:
            store.close()
        self.assertEqual(path.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
