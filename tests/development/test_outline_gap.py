import contextlib
import csv
import gzip
import io
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fonts.portable.development.validation.check_mark_spacing import report_contacts, weight_grid
from fonts.portable.development.validation.verify_outline_gap import ink_distance, positioned_path, read_origins


class OutlineGapTest(unittest.TestCase):
    def check_bounds(self, first, second, expected):
        lo, hi = ink_distance(positioned_path(first, (0, 0)), positioned_path(second, (0, 0)), .005)
        self.assertLessEqual(lo, expected)
        self.assertGreaterEqual(hi, expected)
        self.assertLessEqual(hi - lo, .005)
        return lo, hi

    def test_displaced_mark_crosses_the_ten_unit_threshold(self):
        # The quadratic has its horizontal tangent at (50, 50).
        base = 'M0 0 Q50 100 100 0 V-20 H0 Z'
        for y, should_pass in ((59.8, False), (60.2, True)):
            with self.subTest(y=y):
                lo, hi = self.check_bounds(base, f'M44 {y} H56 V80 H44 Z', y - 50)
                self.assertTrue(lo >= 10 if should_pass else hi < 10)

    def test_containment_and_counter_have_different_ink_distances(self):
        outer = 'M0 0 H100 V100 H0 Z'
        mark = 'M40 40 H60 V60 H40 Z'
        self.check_bounds(outer, mark, 0)
        self.check_bounds(outer + ' M20 20 V80 H80 V20 Z', mark, 20)

    def test_crossing_without_any_contour_start_inside(self):
        self.check_bounds('M-40 -1 H40 V1 H-40 Z', 'M-1 -40 H1 V40 H-1 Z', 0)

    def test_a_ray_through_a_curve_endpoint_preserves_the_gap(self):
        self.check_bounds('M0 0 C0 0 10 20 20 0 V-15 H0 Z', 'M-10 0 H-15 V-5 H-10 Z', 10)

    def test_curves_with_an_interior_minimum(self):
        # Degree-elevated quadratic with maximum y=75 at t=.5.
        lo, hi = self.check_bounds('M0 0 C20 100 40 100 60 0 Z', 'M24 84 H36 V90 H24 Z', 9)
        self.assertGreater(lo, 8.99)
        self.assertLess(hi, 9.01)

    def test_fractional_rtl_offsets_and_advances(self):
        row = '8,2,0,0,1.25,2.75|4,0,12.5,0,-0.5,0'
        self.assertEqual(read_origins(row, {0, 1}), {0: (4, 0, (-13, 0)), 1: (8, 2, (-11.25, 2.75))})
        with self.assertRaises(ValueError):
            read_origins(row, {2})

    def test_midpoints_supplement_the_full_axis_grid(self):
        self.assertEqual(weight_grid([], 25, True), [412.5 + 25*i for i in range(12)])
        self.assertEqual(weight_grid([450.5, 450, 450], None, True), [450.25])
        self.assertEqual(weight_grid([], 240, False), [400, 640, 700])
        with self.assertRaises(ValueError):
            weight_grid([500], None, True)

    def test_review_candidates_preserve_the_gui_failure_threshold(self):
        with TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            root = Path(tmp)
            font, raw = root/'font.ttf', root/'positions.contacts'
            font.write_bytes(b'font hash fixture')
            raw.write_text(''.join(f'1\t1\t{index}\t0\t1\tbase\tmark\t0\t1\t{gap}\t1\t0\n'
                                   for index, gap in enumerate([-1, 9.99999999, 10, 12.5], 2)))
            record = report_contacts(font, 'ot', 550, [dict(page=7, line=3)], root, 'ot-550', raw, False, 10, 5)
            self.assertEqual(record['kinds'], {'mark.intersection': 1, 'mark.belowGap': 1})
            self.assertEqual((record['contacts'], record['reviewPairs']), (2, 4))
            for filename, expected in [('ot-550.tsv.gz', 2), ('ot-550-nearby.tsv.gz', 4)]:
                with gzip.open(root / filename, 'rt') as source:
                    rows = list(csv.DictReader(source, delimiter='\t'))
                self.assertEqual(len(rows), expected)
                self.assertTrue(all((r['page'], r['line']) == ('7', '3') for r in rows))


if __name__ == '__main__':
    unittest.main()
