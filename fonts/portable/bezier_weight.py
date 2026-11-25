"""Generate a cubic weight master and its metrics from the portable Regular."""
from collections import defaultdict
from dataclasses import dataclass
from fnmatch import fnmatchcase
import math
from types import SimpleNamespace

from fontTools.fontBuilder import FontBuilder
from fontTools.misc.roundTools import otRound
from fontTools.pens.areaPen import AreaPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.pointInsidePen import PointInsidePen
from fontTools.pens.pointPen import PointToSegmentPen
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import newTable
from fontTools.ttLib.tables import otTables
from fontTools.ufoLib import UFOReader
from fontTools.varLib.builder import buildVarIdxMap
from fontTools.varLib.cff import CFF2CharStringMergePen
from fontTools.varLib.models import VariationModel
from fontTools.varLib.varStore import OnlineVarStoreBuilder
from .curve_constraints import constrain_contour, dot, is_join_cap


def unit(z):
    return z / abs(z) if abs(z) > 1e-10 else 0j


def tangent(points):
    origin = points[0]
    return next((unit(p - origin) for p in points[1:] if abs(p - origin) > 1e-8), 0j)


def corner_offset(incoming, outgoing):
    # Intersect the two unit offset tangent lines, limiting spikes at cusps.
    bisector = 1j * (incoming + outgoing)
    if abs(bisector) < 1e-7:
        return incoming
    return unit(bisector) * min(2.0, 2 / abs(bisector))


def limit_short_spans(segments, live, shifts):
    # At a tight corner the miter can travel farther than the next short
    # segment is long (e.g. the medial ha join). Move the two knots together
    # when necessary so that their original direction survives interpolation.
    limits = []
    for k, i in enumerate(live):
        points = segments[i]
        chord = abs(points[-1] - points[0])
        polygon = sum(abs(b-a) for a, b in zip(points, points[1:]))
        if chord > 0 and chord >= polygon * 0.7:
            limits.append((i, live[(k+1) % len(live)], 0.65 * chord))
    for _ in range(12):
        adjusted = False
        for i, j, limit in limits:
            delta = shifts[j] - shifts[i]
            size = abs(delta)
            if size > limit + 1e-7:
                correction = 0.5 * (1 - limit/size) * delta
                shifts[i] += correction
                shifts[j] -= correction
                adjusted = True
        if not adjusted:
            break


def fit_handles(curve, start_shift, end_shift, radius):
    """Fit the offset at interior samples with two tangent-aligned handles."""
    p0, p1, p2, p3 = curve
    forward, backward = tangent(curve), tangent(curve[::-1])
    aa = ab = bb = ar = br = 0.0
    for t in (0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875):
        s = 1 - t
        b1, b2 = 3*s*s*t, 3*s*t*t
        derivative = s*s*(p1-p0) + 2*s*t*(p2-p1) + t*t*(p3-p2)
        target = 1j * unit(derivative) * radius
        residual = target - (s**3 + b1)*start_shift - (t**3 + b2)*end_shift
        a, b = b1*forward, b2*backward
        aa += dot(a, a)
        ab += dot(a, b)
        bb += dot(b, b)
        ar += dot(a, residual)
        br += dot(b, residual)
    determinant = aa*bb - ab*ab
    if determinant <= 1e-10:
        return p1 + start_shift, p2 + end_shift

    def adjustment(value, length):
        if length < 1e-8:
            return 0.0
        limit = 2 * abs(radius)
        return min(limit, max(-limit, -0.95 * length, value))

    first = adjustment((ar*bb - br*ab) / determinant, abs(p1-p0))
    last = adjustment((br*aa - ar*ab) / determinant, abs(p2-p3))
    return p1 + start_shift + first*forward, p2 + end_shift + last*backward


@dataclass
class Contour:
    path: RecordingPen
    area: float
    bounds: tuple


def contours(recording):
    result = []
    for op, pts in recording.value:
        if op == 'moveTo':
            path = RecordingPen()
        getattr(path, op)(*pts)
        if op == 'closePath':
            area, bounds = AreaPen(), BoundsPen(None)
            path.replay(area)
            path.replay(bounds)
            result.append(Contour(path, area.value, bounds.bounds))
    return result


def is_counter(contour, all_contours):
    # A separately drawn component may wind the other way. Determine the
    # local fill from enclosing contours, rather than using a font-wide sign.
    x, y = contour.path.value[0][1][0]
    winding = 0
    for other in all_contours:
        if abs(other.area) <= abs(contour.area):
            continue
        left, bottom, right, top = other.bounds
        if left <= x <= right and bottom <= y <= top:
            probe = PointInsidePen(None, (x, y))
            other.path.replay(probe)
            winding += probe.getWinding()
    return winding == (-1 if contour.area > 0 else 1)


def grid_point(point):
    return complex(otRound(point.real), otRound(point.imag))


def cursive_edges(font, glyphs):
    """Measure the opposite edge of this font's join at its authored GPOS anchors."""
    if 'GPOS' not in font:
        return {}
    # Read a detached copy so the source positioning table stays byte-identical.
    gpos = newTable('GPOS')
    gpos.decompile(font.getTableData('GPOS'), font)
    if gpos.table.LookupList is None:
        return {}
    anchors = defaultdict(set)
    for lookup in gpos.table.LookupList.Lookup:
        for subtable in lookup.SubTable:
            subtable = getattr(subtable, 'ExtSubTable', subtable)
            if hasattr(subtable, 'EntryExitRecord'):
                for name, record in zip(subtable.Coverage.glyphs, subtable.EntryExitRecord):
                    for anchor in (record.EntryAnchor, record.ExitAnchor):
                        if anchor is not None:
                            anchors[name].add(complex(anchor.XCoordinate, anchor.YCoordinate))
    offsets = set()
    for name, locations in anchors.items():
        path = RecordingPen()
        glyphs[name].draw(path)
        previous = ()
        for operation, points in path.value:
            if operation == 'curveTo':
                curve = (current, *(complex(*p) for p in points))
                if is_join_cap(previous, curve):
                    a, b = grid_point(previous[0]), grid_point(curve[-1])
                    if a in locations:
                        offsets.add(b-a)
                    if b in locations:
                        offsets.add(a-b)
                previous = curve
            else:
                previous = ()
            if points:
                current = complex(*points[-1])
    offsets = sorted(offsets or {0j}, key=lambda p: (p.real, p.imag))
    return {name: [(a, a+d) for a in sorted(points, key=lambda p: (p.real, p.imag)) for d in offsets]
            for name, points in anchors.items()}


def offset_contour(contour, dx, dy, radius, output, joins=()):
    """Work in scaled coordinates where one unit of offset is circular."""
    if radius == 0 or abs(contour.area) < 1e-6:
        contour.path.replay(output)
        return
    path = contour.path.value
    def scaled(p):
        return complex(p[0] / dx, p[1] / dy)
    def unscaled(p):
        return p.real * dx, p.imag * dy

    start = current = scaled(path[0][1][0])
    segments = []
    for op, pts in path[1:-1]:
        if op not in ('curveTo', 'lineTo'):
            raise ValueError(f'Expected a cubic contour, got {op}')
        segment = (current, *(scaled(p) for p in pts))
        segments.append(segment)
        current = segment[-1]
    drawn = len(segments)
    if not drawn:
        contour.path.replay(output)
        return
    # Type 2 closes contours implicitly. Accumulated 16.16 rounding can
    # also leave a microscopic seam in an explicitly closed cubic contour.
    if abs(complex((current.real-start.real)*dx, (current.imag-start.imag)*dy)) > 0.01:
        segments.append((current, start))

    heads = [tangent(s) for s in segments]
    tails = [-tangent(s[::-1]) for s in segments]
    live = [i for i, h in enumerate(heads) if h]
    if not live:
        contour.path.replay(output)
        return
    shifts = [0j] * len(segments)
    for k, index in enumerate(live):
        shifts[index] = radius * corner_offset(tails[live[k-1]], heads[index])
    limit_short_spans(segments, live, shifts)
    proposed = []
    for k, index in enumerate(live):
        segment = segments[index]
        first, last = shifts[index], shifts[live[(k+1) % len(live)]]
        controls = fit_handles(segment, first, last, radius) if len(segment) == 4 else ()
        proposed.append((segment[0] + first, *controls, segment[-1] + last))
    active = [segments[i] for i in live]
    knots = {grid_point(complex(*unscaled(s[0]))): i for i, s in enumerate(active)}
    paired = set()
    singles = set()
    for first, last in joins:
        ids = [knots[p] for p in (first, last) if p in knots]
        if len(ids) == 2 and ids[0] != ids[1]:
            paired.add(tuple(sorted(ids)))
        else:
            singles.update(ids)
    edges = sorted(paired) + [(i,) for i in sorted(singles - {i for pair in paired for i in pair})]
    fitted = dict(zip(live, constrain_contour(active, proposed, edges)))
    for k, index in enumerate(live):
        # Every coincident knot in a run follows the same final displacement,
        # including when collision prevention reduces a neighbour's offset.
        shift = fitted[index][0] - segments[index][0]
        j = (live[k-1] + 1) % len(segments)
        while j != index:
            fitted[j] = tuple(p + shift for p in segments[j])
            j = (j + 1) % len(segments)
    output.moveTo(unscaled(fitted[0][0]))
    for i in range(drawn):
        segment = fitted[i]
        if len(segment) == 4:
            output.curveTo(*(unscaled(p) for p in segment[1:]))
        else:
            output.lineTo(unscaled(segment[-1]))
    output.closePath()


def bold_outline(regular, dx, dy, strength, counter_scale, joins=()):
    bold = RecordingPen()
    parts = contours(regular)
    for part in parts:
        hole = is_counter(part, parts)
        radius = strength
        if hole:
            x0, y0, x1, y1 = part.bounds
            radius = min(radius * counter_scale, 0.12 * min((x1-x0)/dx, (y1-y0)/dy))
        # Clockwise exteriors grow to the left of the path. A hole with
        # the opposite winding moves into its own interior.
        direction = (1 if part.area < 0 else -1) * (-1 if hole else 1)
        offset_contour(part, dx, dy, radius * direction, bold, joins)
    return bold


def set_variable_names(font):
    names = font['name']
    family = names.getDebugName(16) or names.getDebugName(1)
    ps_family = ''.join(c for c in family if c.isascii() and c.isalnum())[:52]
    replacements = {1: family, 2: 'Regular', 3: ps_family + '-Regular-' + str(font['head'].fontRevision),
                    4: family + ' Regular', 6: ps_family + '-Regular', 16: family,
                    17: 'Regular', 21: family, 22: 'Regular', 25: ps_family}
    platforms = {(r.platformID, r.platEncID, r.langID) for r in names.names if r.nameID == 1}
    for platform in platforms:
        for name_id, value in replacements.items():
            names.setName(value, name_id, *platform)
    return ps_family


def read_overrides(path, font):
    edits = {}
    if path is None:
        return edits
    with UFOReader(path) as reader:
        info = SimpleNamespace()
        reader.readInfo(info)
        if getattr(info, 'unitsPerEm', None) != font['head'].unitsPerEm:
            raise ValueError('The UFO and the source font must use the same units per em')
        layer = reader.getGlyphSet()
        unknown = sorted(set(layer.keys()) - set(font.getGlyphOrder()))
        if unknown:
            raise ValueError(f'Unknown glyphs in the override layer: {unknown}')
        for name in layer.keys():
            edits[name] = RecordingPen()
            layer.readGlyph(name, pointPen=PointToSegmentPen(edits[name]))
    return edits


def check_correspondence(name, regular, bold):
    def signature(outline):
        return [(op, len(points)) for op, points in outline.value]
    if signature(regular) != signature(bold):
        raise ValueError(f'{name}: edited contours must retain the original segment types and order')
    if not all(math.isfinite(v) for _, pts in bold.value for point in pts for v in point):
        raise ValueError(f'{name}: nonfinite coordinate in edited outline')


def build(font, dx, dy, mark_scale, counter_scale, scales, edits, *, fixed_advances=False,
          bold_variants=None):
    if 'CFF2' not in font or 'fvar' in font:
        raise ValueError('Use a static CFF2 export from digitalkhatt_generate_opentype --no-variable')
    glyphs = font.getGlyphSet()
    order = font.getGlyphOrder()
    # Read the classes without making FontTools reserialize the original
    # layout table. Only outline, metric-variation and naming data change.
    gdef = newTable('GDEF')
    gdef.decompile(font.getTableData('GDEF'), font)
    classes = gdef.table.GlyphClassDef.classDefs
    builder = FontBuilder(font=font)
    family = set_variable_names(font)
    weights = [(400, 'Regular'), (500, 'Medium'), (700, 'Bold')]
    builder.setupFvar([('wght', 400, 400, 700, 'Weight')], [
        dict(location={'wght': w}, stylename=n, postscriptfontname=family + '-' + n)
        for w, n in weights])
    builder.setupStat([dict(tag='wght', name='Weight', values=[
        dict(value=400, linkedValue=700, name='Regular', flags=2),
        dict(value=500, name='Medium'), dict(value=700, name='Bold')])])

    model = VariationModel([{}, {'wght': 1}])
    top = font['CFF2'].cff.topDictIndex[0]
    joins = cursive_edges(font, glyphs)
    metrics = OnlineVarStoreBuilder(['wght'])
    metrics.setModel(model)
    zero = metrics.storeDeltas([0])
    advances, left, right = [], [], []
    changed = 0
    for name in order:
        source_name, transform = (bold_variants or {}).get(name, (name, None))
        is_mark = classes.get(name) == 3
        strength = mark_scale if is_mark else 1.0
        for pattern, multiplier in scales:
            if fnmatchcase(source_name, pattern):
                strength *= multiplier
                break
        regular = RecordingPen()
        glyph = glyphs[name]
        glyph.draw(regular)
        advance_index = zero
        left_delta = right_delta = 0
        if (regular.value and (strength or transform)) or source_name in edits:
            if source_name in edits:
                bold = edits[source_name]
                check_correspondence(name, regular, bold)
            else:
                bold = bold_outline(regular, dx, dy, strength, counter_scale, joins.get(name, ()))
            if transform is not None:
                bold = transform(bold)
            if bold.value != regular.value:
                old = top.CharStrings[name]
                pen = CFF2CharStringMergePen([], name, 2, 0, roundTolerance=0)
                regular.replay(pen)
                pen.restart(1)
                bold.replay(pen)
                top.CharStrings[name] = pen.getCharString(old.private, old.globalSubrs, model, optimize=False)
                regular_bounds, bold_bounds = BoundsPen(None), BoundsPen(None)
                regular.replay(regular_bounds)
                bold.replay(bold_bounds)
                left_delta = otRound(bold_bounds.bounds[0]) - otRound(regular_bounds.bounds[0])
                right_edge_delta = otRound(bold_bounds.bounds[2]) - otRound(regular_bounds.bounds[2])
                advance_delta = 0
                if not fixed_advances and glyph.width > 0 and not is_mark:
                    advance_delta = right_edge_delta - left_delta
                    if not 0 <= glyph.width + advance_delta <= 65535:
                        raise ValueError(f'{name}: Bold advance {glyph.width + advance_delta} is outside the hmtx range')
                    advance_index = metrics.storeDeltas([advance_delta])
                # The right sidebearing is measured from the ink to the next origin.
                right_delta = advance_delta - right_edge_delta
                changed += 1
        advances.append(advance_index)
        left.append(metrics.storeDeltas([left_delta]))
        right.append(metrics.storeDeltas([right_delta]))
    if not changed:
        raise ValueError('No outlines changed at the requested strength')
    builder.setupCFF2Regions(model.supports[1:])
    font['HVAR'] = newTable('HVAR')
    hvar = font['HVAR'].table = otTables.HVAR()
    hvar.Version = 0x10000
    hvar.VarStore = metrics.finish()
    hvar.AdvWidthMap = buildVarIdxMap(advances, order)
    hvar.LsbMap = buildVarIdxMap(left, order)
    hvar.RsbMap = buildVarIdxMap(right, order)
    return changed
