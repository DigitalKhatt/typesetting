import unittest
from types import SimpleNamespace

from fontTools.feaLib.builder import addOpenTypeFeaturesFromString
from fontTools.fontBuilder import FontBuilder
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import newTable
from fontTools.varLib.varStore import OnlineVarStoreBuilder, VarStoreInstancer

from fonts.portable.compact_mark import compact_bold
from fonts.portable.mark_spacing import apply_spacing, context_subtables, positioning_value


class MarkSpacingTest(unittest.TestCase):
    def test_compaction_keeps_the_bold_ink_center_and_point_correspondence(self):
        drawing = RecordingPen()
        drawing.moveTo((10, -5))
        drawing.lineTo((110, -5))
        drawing.lineTo((110, 195))
        drawing.closePath()
        result = compact_bold(drawing)
        self.assertEqual([op for op, _ in drawing.value], [op for op, _ in result.value])
        bounds = BoundsPen(None)
        result.replay(bounds)
        for actual, expected in zip(bounds.bounds, (13, 1, 107, 189)):
            self.assertAlmostEqual(actual, expected)
        self.assertEqual(drawing.value[0][1], ((10, -5),))

    def test_midweight_deltas_return_to_the_authored_bold_position(self):
        builder = OnlineVarStoreBuilder(['wght'])
        value = positioning_value(builder, 'mark', (2, -3), (8, -3), [(550, (12, 1))])
        store = builder.finish()

        def offset(field, device, normalized):
            instancer = VarStoreInstancer(store, [SimpleNamespace(axisTag='wght')], {'wght': normalized})
            var_index = (device.StartSize << 16) | device.EndSize
            return field + instancer[var_index]

        for coordinate, expected in [(0, (2, -3)), (.25, (7, -1)), (.5, (12, 1)),
                                     (.75, (10, -1)), (1, (8, -3))]:
            with self.subTest(coordinate=coordinate):
                self.assertEqual(expected, (offset(value.XPlacement, value.XPlaDevice, coordinate),
                                            offset(value.YPlacement, value.YPlaDevice, coordinate)))

    def test_chained_rules_share_actions_and_preserve_each_glyphs_precedence(self):
        builder = FontBuilder(1000)
        builder.setupGlyphOrder(['.notdef', 'prefix', 'base', 'fatha', 'child', 'child.alt'])
        font = builder.font
        addOpenTypeFeaturesFromString(font, """
            table GDEF { GlyphClassDef [prefix base], , [fatha child child.alt], ; } GDEF;
            feature mkmk { pos fatha <0 1 0 0>; } mkmk;
        """)
        apply_spacing(font, [
            [['base', 'fatha'], 1, 'mark', [1, 2], [1, 2]],
            [['prefix', 'base', 'fatha', 'child'], 2, 'mark', [3, 4], [3, 4]],
            [['prefix', 'base', 'fatha', 'child'], 3, 'mark', [7, 8], [7, 8]],
        ], glyph_alternates={'child': 'child.alt'})
        table = newTable('GPOS')
        table.decompile(font.getTableData('GPOS'), font)
        native, first_action, second_action, dispatcher = table.table.LookupList.Lookup
        self.assertEqual(table.table.FeatureList.FeatureRecord[0].Feature.LookupListIndex, [0, 3])
        self.assertEqual(first_action.SubTable[0].Coverage.glyphs, ['fatha', 'child', 'child.alt'])
        self.assertEqual([v.YPlacement for v in first_action.SubTable[0].Value], [2, 8, 8])
        self.assertEqual(second_action.SubTable[0].Value.YPlacement, 4)

        chain, = [sub.ExtSubTable for sub in dispatcher.SubTable]
        self.assertEqual(chain.Format, 1)
        self.assertEqual(chain.Coverage.glyphs, ['fatha', 'child', 'child.alt'])
        fatha_rules, child_rules, alternate_rules = [s.ChainPosRule for s in chain.ChainPosRuleSet]
        self.assertEqual([(r.Backtrack, r.LookAhead) for r in fatha_rules], [
            (['base', 'prefix'], ['child']), (['base', 'prefix'], ['child.alt']), (['base'], []),
        ])
        self.assertEqual([r.PosLookupRecord[0].LookupListIndex for r in fatha_rules], [2, 2, 1])
        for rule in fatha_rules + child_rules + alternate_rules:
            self.assertEqual(rule.InputGlyphCount, 1)
            self.assertEqual(rule.PosLookupRecord[0].SequenceIndex, 0)
        self.assertEqual(alternate_rules[0].Backtrack, child_rules[0].Backtrack)
        self.assertEqual(alternate_rules[0].PosLookupRecord[0].LookupListIndex, 1)

    def test_repeated_alternate_keeps_coverage_rule_in_order(self):
        order = {'base': 0, 'mark': 1, 'mark.alt': 2, 'target': 3}
        first, middle, last = context_subtables([
            (['base', 'base', 'target', 'base'], 2, 0),
            (['mark', 'target', 'mark'], 1, 1),
            (['base', 'target'], 1, 2),
        ], order, {'mark': 'mark.alt'})
        self.assertEqual([first.Format, middle.Format, last.Format], [1, 3, 1])
        self.assertEqual(middle.BacktrackCoverage[0].glyphs, ['mark', 'mark.alt'])
        self.assertEqual(middle.LookAheadCoverage[0].glyphs, ['mark', 'mark.alt'])
        self.assertEqual(middle.PosLookupRecord[0].LookupListIndex, 1)
