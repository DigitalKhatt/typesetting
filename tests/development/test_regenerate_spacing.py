import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fontTools.misc.roundTools import otRound

from fonts.portable.development import regenerate_spacing
from fonts.portable.development.spacing_curves import (context_key, curve, fit_curves, interpolate,
                                                          key_of, logical_glyphs, rule_index, simplify)
from fonts.portable.development.spacing_history import PUBLISHED, replay
from fonts.portable.development.project import DATA_DIR


class SpacingRegenerationTest(unittest.TestCase):
    def test_replay_from_calibration_inputs(self):
        bundle = DATA_DIR / 'spacing-checkpoints.zip'
        for family, counts in {'oldmadina': (273, 251), 'madina': (89, 1), 'indopak': (84, 0)}.items():
            with self.subTest(family=family):
                data = replay(family, bundle)
                self.assertEqual(hashlib.sha256(data).hexdigest(), PUBLISHED[family])
                rows = json.loads(data)
                self.assertEqual((len(rows), len({w for r in rows if len(r) > 5 for w, _ in r[5]})), counts)

    def test_context_uses_logical_order_and_keeps_real_spaces(self):
        names = ['base', 'space', 'smalllowmeem.hahfina', 'kasra']
        record = '1,5,220,0,0,0|2,4,0,0,0,0|1,3,0,0,0,0|3,2,0,0,0,0|0,1,300,0,0,0'
        sequence, indices = logical_glyphs(record, names)
        self.assertEqual(sequence, ('base', 'kasra', 'smalllowmeem', 'space'))
        self.assertEqual(indices, {0: 0, 1: 1, 3: 2, 4: 3})
        parent = [['base', 'kasra'], 1, 'mark', [0, 0], [2, 2]]
        self.assertEqual(context_key(sequence, 1, 1, 2, 'mark', rule_index([parent]), 0),
                         (('base', 'kasra', 'smalllowmeem'), 1, 'mark'))

    def test_rounding_preserves_every_sample_and_seed_knot(self):
        nodes = {w: (otRound((w - 400) / 3), -(w // 6) % 3) for w in range(400, 701)}
        kept = simplify(nodes, [417, 535])
        self.assertIn(417, kept)
        self.assertIn(535, kept)
        self.assertLess(len(kept), len(nodes))
        for w, observed in nodes.items():
            self.assertEqual(tuple(map(otRound, interpolate(kept, w))), observed)

    def test_narrower_context_inherits_the_prior_curve_and_votes(self):
        parent = [['base', 'mark'], 1, 'mark', [1, 2], [7, 8], [[450, [3, -2]]]]
        key = (('base', 'mark', 'dot'), 1, 'mark')
        votes = {key: {550: {(4, 2): 1, (-1, 0): 3, (0, -1): 3}}}
        rows, conflicts = fit_curves([parent], votes, [400, 550, 700])
        self.assertEqual(conflicts, 1)
        self.assertIn(parent, rows)
        nodes = curve(next(row for row in rows if key_of(row) == key))
        self.assertEqual((nodes[400], nodes[450], nodes[550], nodes[700]),
                         ((1, 2), (3, -2), (4, 2), (7, 8)))

    def test_kern_can_gain_vertical_placement(self):
        parent = [['a', 'b'], 1, 'kern', [0], [4]]
        key = key_of(parent)
        rows, _ = fit_curves([parent], {key: {700: {(2, -1): 1}}}, [400, 700])
        self.assertEqual(rows, [[['a', 'b'], 1, 'kern', [0, 0], [6, -1]]])

    def test_trapped_mark_records_both_moves_on_the_same_context(self):
        names = ['base', 'dot', 'kasra', 'mim']
        line = '3,3,0,0,0,0|2,2,0,0,0,0|1,1,0,0,0,0|0,0,300,0,0,0\n'
        with TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / 'records').write_text(line * 3)
            (work / 'contacts').write_text('2\t0\t3\n')
            moves = '1\t3\t0\t3\t16\t8\t0\n1\t1\t0\t3\t14\t4\t0\n'
            with patch.object(regenerate_spacing.subprocess, 'check_output', side_effect=['', moves]) as run:
                votes = regenerate_spacing.local_moves(work, work / 'font.ttf', 700, 10, work, work / 'records',
                                                       work / 'contacts', work / 'outlines', names, {}, 0)
            self.assertEqual(run.call_args.args[0][-3:], ['--outlines', work / 'outlines', '--paired-marks'])
            self.assertEqual(run.call_args.kwargs['input'], line)
        self.assertEqual(votes, {(tuple(names), 3, 'mark'): {(16, 8): 1},
                                 (tuple(names), 1, 'mark'): {(14, 4): 1}})
