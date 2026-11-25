import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

from fonts.portable.development.project import VALIDATION_DIR
from fonts.portable.development.validation.check_mark_spacing import write_glyph_metadata
from fonts.portable.development.validation.probes import compile_probes


class KernTargetTest(unittest.TestCase):
    def test_native_proposals_on_a_base_contact(self):
        bases = ['previous.fina', 'target.isol.alt', 'target.init.beforeseen', 'unanchored.init', 'exitOnly',
                 'entry.isol', 'incoming.init', 'target.medi', 'target.fina', 'symbol', 'target.isolate',
                 'configuredMark.isol']
        names = ['.notdef', *bases, 'ligature.isol', 'mark']
        builder = FontBuilder(1000)
        builder.setupGlyphOrder(names)
        builder.setupCharacterMap({})
        pen = TTGlyphPen(None)
        pen.moveTo((0, 0))
        pen.lineTo((0, 10))
        pen.lineTo((20, 10))
        pen.lineTo((20, 0))
        pen.closePath()
        box = pen.glyph()
        builder.setupGlyf({name: box for name in names})
        builder.setupHorizontalMetrics({name: (40, 0) for name in names})
        builder.setupHorizontalHeader(ascent=800, descent=-200)
        builder.setupNameTable({'familyName': 'Kern target test', 'styleName': 'Regular'})
        builder.setupOS2()
        builder.setupPost()
        addOpenTypeFeaturesFromString(builder.font, f"""
            table GDEF {{ GlyphClassDef [{' '.join(bases)}], [ligature.isol], [mark], ; }} GDEF;
            lookup anchors useExtension {{
                pos cursive target.init.beforeseen <anchor NULL> <anchor 0 0>;
                pos cursive exitOnly <anchor NULL> <anchor 0 0>;
                pos cursive entry.isol <anchor 20 0> <anchor NULL>;
                pos cursive incoming.init <anchor 20 0> <anchor 0 0>;
            }} anchors;
            feature curs {{ lookup anchors; }} curs;
        """)
        with TemporaryDirectory() as temp:
            work = Path(temp)
            profiles = json.loads((VALIDATION_DIR / 'config/profiles.json').read_text())
            try:
                compile_probes(work, profiles)
            except FileNotFoundError as error:
                self.skipTest(str(error))
            font = work / 'test.ttf'
            metadata = work / 'glyphs.txt'
            builder.save(font)
            write_glyph_metadata(builder.font, {'configuredMark.isol'}, metadata)
            # The two boxes have a six-unit vertical gap. A short vertical move
            # clears an isolated target, while a run start requires a larger X move.
            records = ''.join(f'{gid},1,0,0,0,16|1,0,0,0,0,0\n' for gid in range(2, len(names)))
            proposals = subprocess.check_output([work / 'mark_contacts', font, '470', str(len(names)-2),
                                                 metadata, '10', '11'], input=records, text=True)
            moves = {}
            for row in proposals.splitlines():
                line, target, _, _, dx, dy, kern = map(int, row.split())
                self.assertEqual(target, 1)
                moves[names[line+1]] = (dx, dy, kern)
        self.assertEqual(set(moves), {'target.isol.alt', 'target.init.beforeseen', 'unanchored.init', 'exitOnly',
                                      'configuredMark.isol', 'mark'})
        self.assertEqual(moves['target.isol.alt'], (0, 6, 1))
        for name in ('target.init.beforeseen', 'unanchored.init', 'exitOnly'):
            dx, dy, kern = moves[name]
            self.assertNotEqual(dx, 0)
            self.assertEqual((dy, kern), (0, 1))
        for name in ('configuredMark.isol', 'mark'):
            self.assertEqual(moves[name], (0, 6, 0))
