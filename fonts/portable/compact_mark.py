"""Old Madina's contextual small low mīm, using ordinary variable-font tables."""
from copy import copy

from fontTools.otlLib.builder import buildCoverage, buildLookup, buildSingleSubstSubtable
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib.tables import otTables as ot
from fontTools.varLib.featureVars import addFeatureVariationsRaw


SOURCE = 'smalllowmeem'
ALTERNATE = 'smalllowmeem.hahfina'
BOLD_SCALE = 0.94


def add_regular_copy(font):
    order = font.getGlyphOrder()
    top = font['CFF2'].cff.topDictIndex[0]
    chars = top.CharStrings
    if ALTERNATE in order or getattr(top, 'FDSelect', None) is not None:
        raise ValueError('Expected the portable Old Madina single-FD CFF2 export')
    metrics = font['hmtx'].metrics
    metrics[ALTERNATE] = metrics[SOURCE]
    chars.charStrings[ALTERNATE] = len(chars.charStringsIndex)
    chars.charStringsIndex.append(chars[SOURCE])
    top.charset = [*order, ALTERNATE]
    font.setGlyphOrder(top.charset)
    font['maxp'].numGlyphs = len(top.charset)
    classes = font['GDEF'].table.GlyphClassDef.classDefs
    classes[ALTERNATE] = classes[SOURCE]


def compact_bold(drawing):
    bounds = BoundsPen(None)
    drawing.replay(bounds)
    x0, y0, x1, y1 = bounds.bounds
    dx = (1 - BOLD_SCALE) * (x0 + x1) / 2
    dy = (1 - BOLD_SCALE) * (y0 + y1) / 2
    result = RecordingPen()
    drawing.replay(TransformPen(result, (BOLD_SCALE, 0, 0, BOLD_SCALE, dx, dy)))
    return result


def add_substitution(font, gsub):
    order = font.getReverseGlyphMap()
    lookups = gsub.LookupList.Lookup
    replace = len(lookups)
    lookups.append(buildLookup([buildSingleSubstSubtable({SOURCE: ALTERNATE})]))
    chain = ot.ChainContextSubst()
    chain.Format = 3
    chain.BacktrackCoverage = [buildCoverage([name], order) for name in ('kasra', 'hah.fina')]
    chain.InputCoverage = [buildCoverage([SOURCE], order)]
    chain.LookAheadCoverage = []
    record = ot.SubstLookupRecord()
    record.SequenceIndex, record.LookupListIndex = 0, replace
    chain.SubstLookupRecord = [record]
    lookups.append(buildLookup([chain], table='GSUB', extension=True))
    gsub.LookupList.LookupCount = len(lookups)
    # Retain the native glyph ID at the default. The first representable
    # positive normalized weight selects the interpolating alternate.
    addFeatureVariationsRaw(font, gsub, [({'wght': (1 / 16384, 1)}, [replace + 1])], 'calt')


def add_positioning_membership(font):
    """Copy every mark anchor, position and context in which this mark participates."""
    def includes(coverage):
        return SOURCE in coverage.glyphs

    def duplicate(coverage):
        # Do not edit a shared Coverage in place. Some native subtables share
        # coverage offsets while keeping their own parallel record arrays.
        result = copy(coverage)
        result.glyphs = [*coverage.glyphs, ALTERNATE]
        return result

    sets = font['GDEF'].table.MarkGlyphSetsDef
    sets.Coverage = [duplicate(c) if includes(c) else c for c in sets.Coverage]
    for lookup in font['GPOS'].table.LookupList.Lookup:
        for wrapper in lookup.SubTable:
            sub = getattr(wrapper, 'ExtSubTable', wrapper)
            for field, coverage in list(vars(sub).items()):
                if isinstance(coverage, list) and any(isinstance(c, ot.Coverage) for c in coverage):
                    setattr(sub, field, [duplicate(c) if includes(c) else c for c in coverage])
                elif isinstance(coverage, ot.Coverage) and includes(coverage):
                    index = coverage.glyphs.index(SOURCE)
                    if field in ('MarkCoverage', 'Mark1Coverage'):
                        array_field = 'MarkArray' if field == 'MarkCoverage' else 'Mark1Array'
                        array = copy(getattr(sub, array_field))
                        array.MarkRecord = [*array.MarkRecord, array.MarkRecord[index]]
                        array.MarkCount = len(array.MarkRecord)
                        setattr(sub, array_field, array)
                    elif isinstance(sub, ot.SinglePos) and field == 'Coverage':
                        if sub.Format == 2:
                            sub.Value = [*sub.Value, sub.Value[index]]
                            sub.ValueCount = len(sub.Value)
                    else:
                        raise ValueError(f'Unhandled small low mīm coverage in {type(sub).__name__}.{field}')
                    setattr(sub, field, duplicate(coverage))
