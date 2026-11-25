"""Build compatible quadratic masters and share exactly translated contours."""
from collections import Counter

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.pens.cu2quPen import Cu2QuMultiPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables import otTables
from fontTools.ttLib.tables._g_l_y_f import (
    Glyph, GlyphComponent, GlyphCoordinates, OVERLAP_COMPOUND,
    dropImpliedOnCurvePoints, flagOnCurve, flagOverlapSimple,
)
from fontTools.ttLib.tables.TupleVariation import TupleVariation
from fontTools.varLib.builder import buildVarIdxMap
from fontTools.varLib.models import VariationModel
from fontTools.varLib.varStore import OnlineVarStoreBuilder


def quadratic_pair(regular, bold, error):
    pens = [TTGlyphPen(None, outputImpliedClosingLine=True) for _ in range(2)]
    pen = Cu2QuMultiPen(pens, error, reverse_direction=True)
    for (op, a), (other_op, b) in zip(regular.value, bold.value, strict=True):
        if op != other_op or len(a) != len(b):
            raise ValueError('Incompatible cubic drawing operations')
        if op == 'closePath':
            # Reversal must emit the same closing line even when it collapses
            # to a point in just one master.
            if ends != starts:
                pen.lineTo(starts)
            pen.closePath()
        elif op in ('moveTo', 'lineTo', 'curveTo'):
            if op == 'moveTo':
                starts = (a, b)
            ends = ((a[-1],), (b[-1],))
            getattr(pen, op)((a, b))
        else:
            raise ValueError(f'Unexpected cubic drawing operation {op}')
    pair = tuple(p.glyph() for p in pens)
    if (pair[0].endPtsOfContours, pair[0].flags) != (pair[1].endPtsOfContours, pair[1].flags):
        raise ValueError('Incompatible quadratic points')
    dropImpliedOnCurvePoints(*pair)
    for glyph in pair:
        if glyph.numberOfContours > 1:
            glyph.flags[0] |= flagOverlapSimple
    return pair


def read_masters(path, error_per_em):
    masters, advances = {}, {}
    with TTFont(path) as font:
        error = error_per_em * font['head'].unitsPerEm / 1000
        regular = font.getGlyphSet()
        bold = font.getGlyphSet(location={'wght': 700})
        for name in font.getGlyphOrder():
            first, last = RecordingPen(), RecordingPen()
            regular[name].draw(first)
            bold[name].draw(last)
            try:
                masters[name] = quadratic_pair(first, last, error)
            except ValueError as exc:
                raise ValueError(f'{name}: {exc}') from exc
            advances[name] = (regular[name].width, otRound(bold[name].width))
    return masters, advances


def contour_keys(regular, bold):
    start = 0
    for end in regular.endPtsOfContours:
        stop = end + 1
        x, y = regular.coordinates[start]
        # Use the same origin for BOTH masters. Two occurrences may share
        # only if their translation stays constant for the entire axis.
        points = tuple(tuple((px - x, py - y) for px, py in glyph.coordinates[start:stop])
                       for glyph in (regular, bold))
        flags = bytes(v & flagOnCurve for v in regular.flags[start:stop])
        yield (flags, *points), (x, y)
        start = stop


def helper_pair(key):
    flags, *masters = key
    pair = []
    for coordinates in masters:
        glyph = TTGlyphPen(None).glyph()
        glyph.numberOfContours = 1
        glyph.endPtsOfContours = [len(flags) - 1]
        glyph.flags = bytearray(flags)
        glyph.coordinates = GlyphCoordinates(coordinates)
        pair.append(glyph)
    return tuple(pair)


def composite(parts):
    glyph = Glyph()
    glyph.numberOfContours = -1
    glyph.components = []
    for name, dx, dy in parts:
        component = GlyphComponent()
        component.glyphName = name
        component.x, component.y = dx, dy
        component.flags = OVERLAP_COMPOUND if len(parts) > 1 and not glyph.components else 0
        glyph.components.append(component)
    return glyph


def share_contours(masters):
    keys, ids, placements, origins = [], {}, {}, {}
    for name, pair in masters.items():
        parts = []
        for key, position in contour_keys(*pair):
            shape = ids.setdefault(key, len(keys))
            if shape == len(keys):
                keys.append(key)
            parts.append((shape, position))
        placements[name] = parts
        if len(parts) == 1:
            shape, position = parts[0]
            origins.setdefault(shape, (name, position))

    # A TrueType composite cannot contain inline contours. Retain a flat
    # glyph if it would need a helper used only once. Removing a candidate
    # can in turn leave another candidate as the only user of a helper.
    eligible = {name for name, parts in placements.items() if len(parts) > 1}
    while eligible:
        uses = Counter(shape for name in eligible for shape, _ in placements[name])
        keep = {name for name in eligible
                if all(shape in origins or uses[shape] >= 2 for shape, _ in placements[name])}
        if keep == eligible:
            break
        eligible = keep

    shared = dict(masters)
    count = references = 0
    for name, parts in placements.items():
        if len(parts) == 1 and origins[parts[0][0]][0] == name:
            continue
        if len(parts) != 1 and name not in eligible:
            continue
        components = []
        for shape, (x, y) in parts:
            if shape not in origins:
                helper = f'portable.part.{shape}'
                if helper in shared:
                    raise ValueError(f'Component name collision: {helper}')
                shared[helper] = helper_pair(keys[shape])
                origins[shape] = (helper, (0, 0))
            base, (base_x, base_y) = origins[shape]
            components.append((base, x - base_x, y - base_y))
        shared[name] = (composite(components), composite(components))
        count += 1
        references += len(components)
    return shared, dict(compositeGlyphs=count, componentReferences=references,
                        unencodedHelpers=len(shared) - len(masters))


def write_ttf(reference, path, masters, advances):
    order = list(masters)
    regular = {n: pair[0] for n, pair in masters.items()}
    with TTFont(reference, recalcTimestamp=False) as font:
        # Read glyph-indexed metadata against the original glyph order first.
        font['post']
        font['hmtx']
        del font['CFF2']
        if 'DSIG' in font:
            del font['DSIG']
        font.sfntVersion = '\x00\x01\x00\x00'
        font['head'].glyphDataFormat = 0
        font['head'].flags |= 2  # left sidebearing equals xMin
        font.setGlyphOrder(order)
        font['glyf'] = newTable('glyf')
        builder = FontBuilder(font=font)
        builder.setupMaxp()
        builder.setupGlyf(regular)
        variations, hmetrics = {}, {}
        store = OnlineVarStoreBuilder(['wght'])
        store.setModel(VariationModel([{}, {'wght': 1}]))
        widths = []
        for name, (a, b) in masters.items():
            w0, w1 = advances.get(name, (0, 0))
            dw = w1 - w0
            hmetrics[name] = (w0, a.xMin)
            widths.append(store.storeDeltas([dw]))
            # Component placements are constant by construction. A composite
            # still needs its own advance delta, independent of its children.
            if a.isComposite():
                points = GlyphCoordinates((c.x, c.y) for c in a.components)
                deltas = [(0, 0)] * len(points)
                ends = list(range(len(points)))
            else:
                points = a.coordinates.copy()
                deltas = list(b.coordinates - a.coordinates)
                ends = a.endPtsOfContours
            deltas.extend([(0, 0), (dw, 0), (0, 0), (0, 0)])
            if any(dx or dy for dx, dy in deltas):
                points.extend([(0, 0), (w0, 0), (0, 0), (0, 0)])
                variation = TupleVariation({'wght': (0, 1, 1)}, deltas)
                variation.optimize(points, ends, tolerance=0, isComposite=a.isComposite())
                variations[name] = [variation]
        builder.setupHorizontalMetrics(hmetrics)
        builder.setupGvar(variations)
        hvar = font['HVAR'].table = otTables.HVAR()
        hvar.Version = 0x10000
        hvar.VarStore = store.finish()
        hvar.AdvWidthMap = buildVarIdxMap(widths, order)
        # A different control point can become the leftmost or rightmost
        # between masters. glyf and its phantom points give the actual bounds.
        hvar.LsbMap = hvar.RsbMap = None
        font.save(path)
