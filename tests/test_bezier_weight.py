from io import BytesIO
import unittest

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.ttLib import TTFont
from fontTools.varLib.varStore import VarStoreInstancer

from fonts.portable.bezier_weight import bold_outline, build, check_correspondence, contours
from fonts.portable.curve_constraints import constrain_contour, limit_handles
from fonts.portable.check_outlines import cubic_loop, proper_crossings


def polygons(*rings):
    pen = RecordingPen()
    for start, *corners in rings:
        pen.moveTo(start)
        for corner in corners:
            pen.lineTo(corner)
        pen.closePath()
    return pen


class OutlineWeightTest(unittest.TestCase):
    def test_short_mim_handles_across_weight(self):
        # Initial proposal for the short cubic on final mīm, in font units.
        regular = (519.540+34.733j, 518.504+36.265j, 517.900+37.394j, 515.998+38.967j)
        proposed = (516.834+23.979j, 508.810+35.841j, 516.372+25.883j, 516.276+25.962j)

        def instance(bold, strength):
            points = [a + (b-a)*strength for a, b in zip(regular, bold)]
            return [(p.real, p.imag) for p in points]

        self.assertIsNotNone(cubic_loop(instance(proposed, 0.5)))
        fitted = limit_handles(regular, proposed)
        self.assertEqual((fitted[0], fitted[-1]), (proposed[0], proposed[-1]))
        for knot, handle in ((0, 1), (3, 2)):
            ratio = (fitted[handle]-fitted[knot]) / (proposed[handle]-proposed[knot])
            self.assertAlmostEqual(ratio.imag, 0)
            self.assertGreater(ratio.real, 0)
        for weight in range(301):
            self.assertIsNone(cubic_loop(instance(fitted, weight/300)))

    def test_narrow_notch_keeps_both_sides_in_order(self):
        # The 10-unit notch is narrower than two 12-unit exterior offsets.
        regular = polygons([(0, 0), (0, 100), (45, 100), (45, 20),
                            (55, 20), (55, 100), (100, 100), (100, 0)])
        bold = bold_outline(regular, 12, 10, 1, 0.5)
        check_correspondence("notch", regular, bold)
        x = [points[0][0] for _, points in bold.value if points]
        self.assertLess(max(x[2:4]), min(x[4:6]))
        self.assertAlmostEqual(x[0], -12)
        self.assertAlmostEqual(x[-1], 112)
        # Preserve the prescribed joining edge and move the opposite wall
        # with it. Both ends of the narrowed passage must remain open.
        joined = bold_outline(regular, 12, 10, 1, 0.5, [(45+100j, 45+20j)])
        knots = [points[0] for _, points in joined.value if points]
        self.assertEqual(knots[2], (57, 110))
        self.assertAlmostEqual(knots[3][0], 48.25)
        self.assertLess(knots[2][0], knots[5][0])
        self.assertLess(knots[3][0], knots[4][0])

    def test_final_waw_join_stretch_stays_away_from_tail(self):
        # New Madina waw.fina. The inner tail knot shares a cubic with the
        # tight turn beside the join, so the separation check reduces its fit.
        regular = RecordingPen()
        regular.moveTo((0.428, -204.516))
        for controls in (
            ((0.428, -199.421), (10.765, -195.319), (16.193, -195.319)),
            ((24.307, -195.319), (115.153, -211.267), (165.271, -211.267)),
            ((402.314, -211.267), (518.575, -13.747), (518.575, 30.885)),
            ((518.575, 90.562), (466.821, 157.302), (441.755, 157.302)),
            ((432.073, 157.302), (399.230, 141.431), (399.230, 121.400)),
            ((399.230, 106.200), (422.138, 94.180), (443.384, 90.288)),
            ((455.873, 88.001), (482.404, 88), (502, 88)),
            ((507, 88), (507, 88), (499, 44)),
            ((491, 0), (491, 0), (486, 0)),
            ((462.008, 0), (432.476, 2.039), (414.658, 6.866)),
            ((375.438, 17.493), (357.037, 48.313), (357.037, 107.253)),
            ((357.037, 169.869), (409.501, 250.010), (441.395, 250.010)),
            ((505.577, 250.010), (556.112, 113.287), (556.112, 40.316)),
            ((556.112, -35.669), (491.447, -167.682), (395.158, -223.573)),
            ((349.030, -250.348), (295.166, -258.102), (241.876, -258.102)),
            ((136.016, -258.102), (0.428, -230.517), (0.428, -204.516)),
        ):
            regular.curveTo(*controls)
        regular.closePath()

        bold = bold_outline(regular, 12, 10, 1, 0.5, [(502+88j, 486+0j)])
        knots = [points[-1] for _, points in bold.value if points]
        self.assertGreater(knots[2][1], -211.267)
        self.assertAlmostEqual(knots[15][1], -268.102)
        self.assertAlmostEqual(knots[7][0], 502)
        self.assertAlmostEqual(knots[7][1], 98)
        self.assertAlmostEqual(knots[9][0], 486)
        self.assertAlmostEqual(knots[9][1], -10)

    def test_coincident_knots_follow_local_correction(self):
        regular = polygons([(0, 0), (0, 100), (45, 100), (45, 100), (45, 20),
                            (55, 20), (55, 100), (100, 100), (100, 0)])
        bold = bold_outline(regular, 12, 10, 1, 0.5)
        self.assertEqual(bold.value[2][1], bold.value[3][1])

    def test_closing_knot_does_not_hide_second_crossing(self):
        # The authored closing ends on this bāʾ variant differ by one CFF2
        # rounding step. Away from the seam, the proposed curves cross twice.
        first = (206.802+267.05801j, 204.90324+267.05801j, 200.29858+243.93204j, 195.698+230.78601j)
        last = (228.81898+184.89801j, 225.64911+198.82811j, 210.75946+267.05801j, 206.80199+267.05801j)
        first_bold = (206.802+257.05801j, 206.70706+257.05801j, 214.72278+249.43068j, 207.21809+227.98630j)
        last_bold = (217.02907+183.03488j, 212.07893+204.78839j, 206.99985+257.05798j, 206.80197+257.05798j)
        def ring(a, b):
            return [a, (a[-1], b[0]), b]
        def crossings(curves):
            a, b = [tuple((p.real, p.imag) for p in curves[i]) for i in (0, -1)]
            return proper_crossings(a, b)
        regular, proposed = ring(first, last), ring(first_bold, last_bold)
        self.assertEqual(len(crossings(proposed)), 2)
        self.assertEqual(crossings(constrain_contour(regular, proposed)), [])

    def test_authored_crossing_survives_translation(self):
        points = (0j, 10+10j, 10j, 10+0j, 0j)
        regular = list(zip(points, points[1:]))
        proposed = [tuple(p+2j for p in line) for line in regular]
        self.assertEqual(constrain_contour(regular, proposed), proposed)

    def test_interior_loop_and_closed_cubic(self):
        # This curve visits (-9, -9) at both one quarter and three quarters.
        loop = cubic_loop(((0, 0), (-16, -13), (-16, -26), (0, 9)))
        self.assertEqual(loop["t"], [0.25, 0.75])
        self.assertEqual(loop["point"], (-9, -9))
        self.assertGreater(loop["extent"], 1)
        self.assertIsNone(cubic_loop(((0, 0), (5, 0), (5, 5), (0, 0))))
        self.assertIsNone(cubic_loop(((0, 0), (2, 0), (-1, 0), (3, 0))))

    def test_hole_and_opposite_winding_island(self):
        regular = polygons([(0, 0), (0, 200), (200, 200), (200, 0)],
                           [(40, 40), (160, 40), (160, 160), (40, 160)],
                           [(300, 50), (400, 50), (400, 150), (300, 150)])
        bold = bold_outline(regular, 12, 10, 1, 0.5)
        check_correspondence('rings', regular, bold)
        self.assertEqual([tuple(round(n) for n in p.bounds) for p in contours(bold)],
                         [(-12, -10, 212, 210), (46, 45, 154, 155), (288, 40, 412, 160)])

    def test_short_closing_span_at_concave_join(self):
        # A short span before the first knot in medial ha reversed when its
        # neighbouring corner received a larger offset than the span length.
        regular = polygons([(61, 6), (41, 2), (-5, 0), (0, 105), (125, 125),
                            (140, -55), (60, -25), (68.5, 8.25)])
        bold = bold_outline(regular, 12, 10, 1, 0.5)
        def closing_edge(outline):
            return complex(*outline.value[0][1][0]) - complex(*outline.value[-2][1][0])
        edge = closing_edge(regular)
        self.assertGreater((edge.conjugate() * closing_edge(bold)).real, 0.25 * abs(edge)**2)


class AdvanceWidthTest(unittest.TestCase):
    def test_serialized_hvar_and_outline_overrides(self):
        widths = {'.notdef': 400, 'base': 146, 'edited': 146, 'mark': 0,
                  'spacingMark': 80, 'overlay': 0, 'space': 100}
        regular = polygons([(0, 0), (0, 200), (145, 200), (145, 0)])
        edited = polygons([(-3, 0), (-3, 200), (155, 200), (155, 0)])
        for fixed in (False, True):
            fb = FontBuilder(1000, isTTF=False)
            fb.setupGlyphOrder(list(widths))
            fb.setupCharacterMap({32: 'space', 65: 'base'})
            fb.setupHorizontalMetrics({name: (width, 0) for name, width in widths.items()})
            fb.setupHorizontalHeader(ascent=800, descent=-200)
            fb.setupNameTable({'familyName': 'Advance test', 'styleName': 'Regular'})
            fb.setupOS2()
            charstrings = {}
            for name in widths:
                pen = T2CharStringPen(None, None, CFF2=True)
                if name not in ('.notdef', 'space'):
                    regular.replay(pen)
                charstrings[name] = pen.getCharString()
            fb.setupCFF2(charstrings)
            fb.setupPost()
            addOpenTypeFeaturesFromString(fb.font, 'table GDEF { GlyphClassDef [base edited], , [mark spacingMark], ; } GDEF;')
            build(fb.font, 12, 10, 0.45, 0.5, [], {'edited': edited}, fixed_advances=fixed)
            data = BytesIO()
            fb.font.save(data)
            data.seek(0)
            with TTFont(data) as font:
                hvar = font['HVAR'].table
                # (advance delta, LSB delta, RSB delta) for each Bold glyph.
                expected = {'base': (0, -12, -12) if fixed else (24, -12, 12),
                            'edited': (0, -3, -10) if fixed else (13, -3, 3),
                            'mark': (0, -5, -5), 'spacingMark': (0, -5, -5),
                            'overlay': (0, -12, -12), 'space': (0, 0, 0), '.notdef': (0, 0, 0)}
                for weight, factor in [(400, 0), (550, 0.5), (700, 1)]:
                    glyphs = font.getGlyphSet(location={'wght': weight})
                    instance = VarStoreInstancer(hvar.VarStore, font['fvar'].axes, {'wght': factor})
                    for name, (advance, left, right) in expected.items():
                        with self.subTest(fixed=fixed, weight=weight, glyph=name):
                            self.assertEqual(glyphs[name].width, widths[name] + factor * advance)
                            self.assertEqual(instance[hvar.LsbMap.mapping[name]], factor * left)
                            self.assertEqual(instance[hvar.RsbMap.mapping[name]], factor * right)


if __name__ == '__main__':
    unittest.main()
