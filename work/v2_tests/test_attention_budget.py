"""A valódi WAN-figyelemszámítás CPU-s és GPU nélküli regressziói.

Az AST-ből pontosan a kiadott függvényeket töltjük be; ComfyUI-t, modelleket
és GPU-t nem indítunk. A tervező és a két modell bekötése Torch nélkül is
ellenőrizhető. A külön AI-motor Pythonjával a numerikus tesztek is lefutnak.
"""
import ast
import math
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

try:
    import torch
except ImportError:
    torch = None


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'work/v2_source/tools/quality_nodes/__init__.py'


def load_attention(interrupt=None):
    """A függvények forrása a kiadandó modul, függőségeik helyi csonkok."""
    tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
    names = {'_attention_tile_shape', '_attention_mask', 'bounded_attention'}
    selected = [node for node in tree.body if (
        isinstance(node, ast.FunctionDef) and node.name in names
    ) or (
        isinstance(node, ast.Assign) and any(isinstance(target, ast.Name)
            and target.id == 'ATTENTION_SCORE_BYTES' for target in node.targets)
    )]
    context = dict(torch=torch, math=math, comfy=SimpleNamespace(
        model_management=SimpleNamespace(
            throw_exception_if_processing_interrupted=interrupt or (lambda: None))))
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(SOURCE), 'exec'), context)
    return context


class AttentionPlannerTests(unittest.TestCase):
    def setUp(self):
        self.context = load_attention()
        self.plan = self.context['_attention_tile_shape']

    def test_exact_failed_portrait_shape_never_requests_a_full_score_matrix(self):
        heads, queries, keys = 40, 30888, 30888
        head_tile, query_tile = self.plan(heads, queries, keys)
        self.assertEqual((head_tile, query_tile), (4, 271))
        self.assertGreater(heads * queries * keys * 2, 71 * 1024**3)
        self.assertLessEqual(head_tile * query_tile * keys * 4,
                             self.context['ATTENTION_SCORE_BYTES'])
        self.assertNotEqual(queries % query_tile, 0)

    def test_nondivisible_heads_and_tokens_cover_each_coordinate_once(self):
        heads, queries, keys = 7, 37, 41
        head_tile, query_tile = self.plan(heads, queries, keys, 4096)
        visited = []
        for hstart in range(0, heads, head_tile):
            hstop = min(hstart + head_tile, heads)
            for qstart in range(0, queries, query_tile):
                qstop = min(qstart + query_tile, queries)
                self.assertLessEqual((hstop - hstart) * (qstop - qstart) * keys * 4, 4096)
                visited.extend((head, query) for head in range(hstart, hstop)
                               for query in range(qstart, qstop))
        self.assertEqual(len(visited), heads * queries)
        self.assertEqual(set(visited), {(h, q) for h in range(heads) for q in range(queries)})

    def test_many_sizes_stay_within_budget_without_divisibility_assumptions(self):
        for heads in (1, 3, 7, 40):
            for queries in (1, 37, 1025, 30888):
                for keys in (1, 41, 30888):
                    with self.subTest(heads=heads, queries=queries, keys=keys):
                        htile, qtile = self.plan(heads, queries, keys)
                        self.assertTrue(1 <= htile <= min(heads, 4))
                        self.assertTrue(1 <= qtile <= min(queries, 1024))
                        self.assertLessEqual(htile * qtile * keys * 4,
                                             self.context['ATTENTION_SCORE_BYTES'])

    def test_too_small_budget_fails_before_any_tensor_allocation(self):
        with self.assertRaises(ValueError):
            self.plan(3, 37, 41, 163)

    def test_invalid_dimensions_and_budgets_are_rejected(self):
        for position in range(4):
            for invalid in (0, -1, True, 1.5, None):
                arguments = [3, 37, 41, 4096]
                arguments[position] = invalid
                with self.subTest(position=position, value=invalid), self.assertRaises(ValueError):
                    self.plan(*arguments)


class SamplerAttentionBindingTests(unittest.TestCase):
    def setUp(self):
        self.context = load_attention()
        self.events = []
        self.models = []
        self.attention = self.context['bounded_attention']
        events, models = self.events, self.models

        class Loader:
            def load(inner, name, dtype):
                events.append(('load', name, dtype))
                model = SimpleNamespace(name=name)
                models.append(model)
                return (model,)

        class Shift:
            def patch(inner, model, shift):
                events.append(('shift', model.name, shift))
                shifted = SimpleNamespace(name=model.name, attention=None)
                def bind(attention):
                    shifted.attention = attention
                    events.append(('bind', model.name, attention))
                shifted.set_model_optimized_attention = bind
                return (shifted,)

        def sample(model, seed, steps, cfg, sampler, scheduler, positive, negative,
                   latent, **options):
            self.assertIs(model.attention, self.attention)
            self.events.append(('sample', model.name, options))
            return ({'phase': model.name},)

        self.sample = Mock(side_effect=sample)
        self.context.update(
            YouTubeGGUFRamLoader=Loader,
            ModelSamplingSD3=Shift,
            nodes=SimpleNamespace(common_ksampler=self.sample),
            quality_phase=Mock(),
            require_finite_latent=Mock(),
            logging=SimpleNamespace(info=Mock()),
            gc=SimpleNamespace(collect=Mock()),
        )
        self.context['comfy'].model_management.unload_all_models = Mock()
        self.context['comfy'].model_management.soft_empty_cache = Mock()
        tree = ast.parse(SOURCE.read_text(encoding='utf-8'))
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                   and node.name == 'YouTubeWan14RamSampler')
        exec(compile(ast.Module(body=[cls], type_ignores=[]), str(SOURCE), 'exec'), self.context)

    def run_sample(self):
        return self.context['YouTubeWan14RamSampler']().sample(
            'high.gguf', 'low.gguf', 'positive', 'negative', {'phase': 'input'}, 42)

    def test_both_owned_models_bind_attention_before_their_two_sampling_steps(self):
        self.assertEqual(self.run_sample(), ({'phase': 'low.gguf'},))
        relevant = [(entry[0], entry[1]) for entry in self.events]
        self.assertEqual(relevant, [
            ('load', 'high.gguf'), ('shift', 'high.gguf'),
            ('bind', 'high.gguf'), ('sample', 'high.gguf'),
            ('load', 'low.gguf'), ('shift', 'low.gguf'),
            ('bind', 'low.gguf'), ('sample', 'low.gguf')])
        self.assertIsNot(self.models[0], self.models[1])
        first, second = self.sample.call_args_list
        self.assertEqual(first.args[1:6], (42, 4, 1.0, 'euler', 'simple'))
        self.assertEqual(first.kwargs, dict(disable_noise=False, start_step=0,
            last_step=2, force_full_denoise=False))
        self.assertEqual(second.kwargs, dict(disable_noise=True, start_step=2,
            last_step=4, force_full_denoise=True))
        self.assertEqual(self.context['require_finite_latent'].call_count, 2)
        self.assertEqual(self.context['comfy'].model_management.unload_all_models.call_count, 2)

    def test_failure_in_the_second_phase_still_releases_both_models(self):
        original = self.sample.side_effect
        def fail_second(*args, **kwargs):
            if args[0].name == 'low.gguf':
                raise RuntimeError('fixture sampling failure')
            return original(*args, **kwargs)
        self.sample.side_effect = fail_second
        with self.assertRaisesRegex(RuntimeError, 'fixture sampling failure'):
            self.run_sample()
        self.assertEqual(self.context['comfy'].model_management.unload_all_models.call_count, 2)
        self.assertEqual(self.context['comfy'].model_management.soft_empty_cache.call_count, 2)
        self.assertEqual(self.context['require_finite_latent'].call_count, 1)


@unittest.skipIf(torch is None, 'A numerikus CPU-próbához az AI-motor Torch környezete kell.')
class AttentionNumericalTests(unittest.TestCase):
    def setUp(self):
        torch.manual_seed(1729)
        self.context = load_attention()
        original = self.context['_attention_tile_shape']
        # Csak a tesztnévtérben csökkentjük a keretet; kis tenzoroknál is
        # több rész és rövidebb utolsó rész keletkezik.
        self.context['_attention_tile_shape'] = lambda h, q, k: original(h, q, k, 4096)
        self.bounded = self.context['bounded_attention']
        self.q, self.k, self.v = [torch.randn(2, length, 24) for length in (37, 41, 41)]

    def oracle(self, q=None, k=None, v=None, heads=3, mask=None,
               skip_reshape=False, skip_output_reshape=False, **options):
        q, k, v = (self.q if q is None else q,
                   self.k if k is None else k, self.v if v is None else v)
        with torch.inference_mode():
            actual = self.bounded(q, k, v, heads, mask=mask,
                skip_reshape=skip_reshape, skip_output_reshape=skip_output_reshape, **options)
            if not skip_reshape:
                channels = q.shape[-1] // heads
                kv_heads = k.shape[-1] // channels
                arranged = [q.reshape(q.shape[0], q.shape[1], heads, channels).transpose(1, 2),
                    k.reshape(k.shape[0], k.shape[1], kv_heads, channels).transpose(1, 2),
                    v.reshape(v.shape[0], v.shape[1], kv_heads, v.shape[-1] // kv_heads).transpose(1, 2)]
            else:
                arranged = [q, k, v]
            if options.get('enable_gqa', False):
                repeats = heads // arranged[1].shape[1]
                arranged[1:] = [t.repeat_interleave(repeats, 1) for t in arranged[1:]]
            reference_mask = mask.unsqueeze(1) if mask is not None and mask.ndim == 3 else mask
            sdpa_options = {name: value for name, value in options.items() if name == 'scale'}
            expected = torch.nn.functional.scaled_dot_product_attention(
                *arranged, attn_mask=reference_mask, **sdpa_options)
            if not skip_output_reshape:
                expected = expected.transpose(1, 2).reshape(q.shape[0], -1, heads * arranged[2].shape[-1])
            self.assertEqual(actual.device.type, 'cpu')
            self.assertEqual(actual.dtype, q.dtype)
            self.assertEqual(actual.shape, expected.shape)
            self.assertTrue(torch.allclose(actual, expected, rtol=2e-5, atol=2e-6),
                'max_absolute_error=%s' % (actual - expected).abs().max().item())
            return actual

    def test_nondivisible_plain_attention_matches_sdpa_for_two_batches(self):
        self.oracle()

    def test_noncontiguous_input_layout_matches_sdpa(self):
        tensors = [torch.randn(2, 24, length).transpose(1, 2) for length in (37, 41, 41)]
        self.assertTrue(all(not tensor.is_contiguous() for tensor in tensors))
        self.oracle(*tensors)

    def test_prearranged_input_and_both_output_layouts_match_sdpa(self):
        tensors = [t.reshape(2, t.shape[1], 3, 8).transpose(1, 2)
                   for t in (self.q, self.k, self.v)]
        for arranged_output in (False, True):
            with self.subTest(skip_output_reshape=arranged_output):
                self.oracle(*tensors, skip_reshape=True, skip_output_reshape=arranged_output)

    def test_nondivisible_head_tail_and_gqa_match_sdpa(self):
        # Hét fej esetén a második fej-rész is rövidebb.
        self.oracle(*[torch.randn(2, length, 56) for length in (37, 41, 41)], heads=7)
        self.oracle(torch.randn(2, 6, 37, 8), torch.randn(2, 3, 41, 8),
                    torch.randn(2, 3, 41, 5), heads=6, skip_reshape=True,
                    skip_output_reshape=True, enable_gqa=True)

    def test_gqa_in_flat_layout_matches_sdpa(self):
        self.oracle(torch.randn(2, 37, 48), torch.randn(2, 41, 24),
                    torch.randn(2, 41, 15), heads=6, enable_gqa=True)

    def test_boolean_mask_and_fully_masked_rows_match_sdpa(self):
        mask = torch.ones(37, 41, dtype=torch.bool)
        mask[:, ::3] = False
        mask[0, :] = False
        actual = self.oracle(mask=mask)
        self.assertTrue(torch.equal(actual[:, 0], torch.zeros_like(actual[:, 0])))

    def test_additive_batch_mask_and_fully_masked_rows_match_sdpa(self):
        mask = torch.randn(2, 37, 41) * .2
        mask[:, :, -4:] = -math.inf
        mask[1, 11, :] = -math.inf
        actual = self.oracle(mask=mask)
        self.assertTrue(torch.equal(actual[1, 11], torch.zeros_like(actual[1, 11])))

    def test_head_query_and_key_broadcast_masks_match_sdpa(self):
        for shape in ((1, 3, 1, 41), (2, 1, 37, 1), (1, 1, 1, 1)):
            with self.subTest(shape=shape):
                self.oracle(mask=torch.randn(*shape) * .2)

    def test_zero_and_negative_scales_match_sdpa(self):
        for scale in (0.0, -.13):
            with self.subTest(scale=scale):
                self.oracle(scale=scale)

    def test_cancellation_stops_before_the_next_tile_matrix_multiplication(self):
        class Cancelled(RuntimeError):
            pass
        calls = []
        def interrupt():
            calls.append(1)
            if len(calls) == 4:
                raise Cancelled('fixture cancellation')
        self.context['comfy'].model_management.throw_exception_if_processing_interrupted = interrupt
        with torch.inference_mode(), patch.object(torch, 'bmm', wraps=torch.bmm) as multiply:
            with self.assertRaises(Cancelled):
                self.bounded(self.q, self.k, self.v, 3)
        self.assertEqual(len(calls), 4)
        self.assertEqual(multiply.call_count, 6)  # Három befejezett rész, részenként két szorzás.


if __name__ == '__main__':
    unittest.main(verbosity=2)
