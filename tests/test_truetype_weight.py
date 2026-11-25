import unittest

from fontTools.pens.recordingPen import RecordingPen

from fonts.portable.truetype_weight import quadratic_pair, share_contours


def rectangles(*boxes):
    pen = RecordingPen()
    for x, y, width, height in boxes:
        pen.moveTo((x, y))
        pen.lineTo((x + width, y))
        pen.lineTo((x + width, y + height))
        pen.lineTo((x, y + height))
        pen.closePath()
    return pen


class ComponentSharingTest(unittest.TestCase):
    def test_regular_duplicates_with_different_bold_motion(self):
        def master_pair(x, bold_x):
            return quadratic_pair(rectangles((x, 0, 10, 20)), rectangles((bold_x, 0, 14, 22)), .5)

        masters = {'base': master_pair(0, 0), 'copy': master_pair(30, 30), 'moving': master_pair(80, 81)}
        shared, stats = share_contours(masters)
        self.assertEqual(stats['compositeGlyphs'], 1)
        component = shared['copy'][0].components[0]
        self.assertEqual((component.glyphName, component.x, component.y), ('base', 30, 0))
        self.assertFalse(shared['moving'][0].isComposite())

    def test_shared_contours_expand_in_order_at_both_masters(self):
        def master_pair(x):
            regular = rectangles((x, 0, 5, 9), (x + 17, 3, 10, 8))
            bold = rectangles((x - 2, 0, 8, 12), (x + 18, 4, 13, 12))
            return quadratic_pair(regular, bold, .5)

        masters = {'first': master_pair(0), 'second': master_pair(27)}
        shared, stats = share_contours(masters)
        self.assertEqual(stats, dict(compositeGlyphs=2, componentReferences=4, unencodedHelpers=2))
        self.assertEqual(list(shared)[:2], list(masters))
        for i in (0, 1):
            glyf = {n: pair[i] for n, pair in shared.items()}
            for name, pair in masters.items():
                coords, ends, flags = glyf[name].getCoordinates(glyf)
                self.assertEqual(coords, pair[i].coordinates)
                self.assertEqual(list(ends), pair[i].endPtsOfContours)
                self.assertEqual([f & 1 for f in flags], [f & 1 for f in pair[i].flags])


if __name__ == '__main__':
    unittest.main()
