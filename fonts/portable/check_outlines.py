"""Report loops and crossings introduced in the saved cubic weight reference."""
import argparse
import json
import math
from pathlib import Path

from fontTools.misc.bezierTools import calcCubicBounds, cubicPointAtT, segmentSegmentIntersections, splitCubicAtT
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont

from .font_checks import file_hash


MINIMUM_EXTENT = 0.001  # Font units. Ignore loops at the scale of coordinate noise.


def cross(first, second):
    return first.real * second.imag - first.imag * second.real


def cubic_loop(points):
    p0, p1, p2, p3 = (complex(*point) for point in points)
    # For B(t) = a*t³ + b*t² + c*t + d, divide B(t) - B(u) by t - u.
    # A crossing satisfies a*(t² + t*u + u²) + b*(t + u) + c = 0.
    a = p3 - p0 + 3 * (p1 - p2)
    b = 3 * ((p2 - p1) - (p1 - p0))
    c = 3 * (p1 - p0)
    determinant = cross(a, b)
    if abs(determinant) <= 1e-12 * abs(a) * abs(b):
        return None
    parameter_sum = -cross(a, c) / determinant
    discriminant = 4 * cross(b, c) / determinant - 3 * parameter_sum**2
    if discriminant <= 0:
        return None
    separation = math.sqrt(discriminant)
    start = (parameter_sum - separation) / 2
    end = (parameter_sum + separation) / 2
    if not 1e-9 < start < end < 1 - 1e-9:
        return None

    point = cubicPointAtT(*points, start)
    if math.dist(point, cubicPointAtT(*points, end)) > 1e-7:
        return None
    _, loop, _ = splitCubicAtT(*points, start, end)
    left, bottom, right, top = calcCubicBounds(*loop)
    extent = max(right - left, top - bottom)
    if extent < MINIMUM_EXTENT:
        return None
    return {"t": [start, end], "point": point, "extent": extent}


def segments(glyph):
    pen = RecordingPen()
    glyph.draw(pen)
    paths = []
    for op, points in pen.value:
        if op == "moveTo":
            start = points[0]
            paths.append([])
        elif op in ("lineTo", "curveTo"):
            paths[-1].append((previous, *points))
        elif op == "closePath" and previous != start:
            paths[-1].append((previous, start))
        if points:
            previous = points[-1]
    return paths


def evaluate(curve, t):
    if len(curve) == 2:
        a, b = curve
        return a + t*(b-a), b-a
    a, b, c, d = curve
    s = 1-t
    return (s**3*a + 3*s*s*t*b + 3*s*t*t*c + t**3*d,
            3*(s*s*(b-a) + 2*s*t*(c-b) + t*t*(d-c)))


def proper_crossings(first, second):
    if first == second or first == second[::-1]:
        return []
    a, b = [tuple(complex(*p) for p in curve) for curve in (first, second)]
    result = []
    for seed in segmentSegmentIntersections(first, second):
        # FontTools supplies approximate candidates. Refine each pair of
        # parameters and verify that the two actual curves pass through it.
        t, u = seed.t1, seed.t2
        for _ in range(30):
            if not (0 <= t <= 1 and 0 <= u <= 1):
                break
            p, v = evaluate(a, t)
            q, w = evaluate(b, u)
            determinant = cross(v, w)
            if abs(determinant) <= 1e-8*abs(v)*abs(w):
                break
            error = q-p
            dt, du = cross(error, w)/determinant, cross(error, v)/determinant
            if abs(dt) + abs(du) < 1e-10 and abs(error) < 1e-7:
                ends = (a[0], a[-1], b[0], b[-1])
                if (min(abs(p-e) for e in ends) >= 0.001 and
                        all(abs(t-hit["t"]) + abs(u-hit["u"]) > 1e-6 for hit in result)):
                    result.append({"t": t, "u": u, "point": (p.real, p.imag)})
                break
            t += dt
            u += du
    return result


def possible_pairs(path):
    boxes = []
    for i, curve in enumerate(path):
        x, y = zip(*curve)
        boxes.append((min(x), max(x), min(y), max(y), i))
    active = []
    for left, right, bottom, top, i in sorted(boxes):
        active = [item for item in active if item[0] > left]
        for _, low, high, j in active:
            if high > bottom and top > low:
                yield tuple(sorted((i, j)))
        active.append((right, bottom, top, i))


def loops(paths):
    for contour, path in enumerate(paths):
        for index, segment in enumerate(path):
            if len(segment) == 4:
                loop = cubic_loop(segment)
                if loop is not None:
                    yield (contour, index), loop


def check_outlines(font, weights):
    regular = font.getGlyphSet()
    instances = {weight: font.getGlyphSet(location={'wght': weight}) for weight in weights}
    findings = {weight: {'loops': [], 'crossings': []} for weight in weights}
    regular_loops = 0
    for name in font.getGlyphOrder():
        reference = segments(regular[name])
        authored = {location for location, _ in loops(reference)}
        regular_loops += len(authored)
        original_crossings = {}
        for weight, instance in instances.items():
            paths = segments(instance[name])
            if paths == reference:
                continue
            for (contour, index), hit in loops(paths):
                if (contour, index) not in authored:
                    findings[weight]['loops'].append(dict(glyph=name, contour=contour, segment=index, **hit))
            for contour, (before, after) in enumerate(zip(reference, paths, strict=True)):
                if before == after:
                    continue
                for i, j in possible_pairs(after):
                    hits = proper_crossings(after[i], after[j])
                    if not hits:
                        continue
                    key = contour, i, j
                    if key not in original_crossings:
                        original_crossings[key] = len(proper_crossings(before[i], before[j])) if j < len(before) else 0
                    if len(hits) > original_crossings[key]:
                        findings[weight]['crossings'].append(dict(
                            glyph=name, contour=contour, segments=[i, j],
                            regularCrossings=original_crossings[key], crossings=hits))
    for result in findings.values():
        result['loops'].sort(key=lambda hit: hit['extent'], reverse=True)
    return dict(regularLoops=regular_loops, minimumExtent=MINIMUM_EXTENT, findings=findings)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference', type=Path, help='weight-reference.otf from a build with --keep-intermediates')
    parser.add_argument('--weights', type=float, nargs='+', default=[550, 700])
    parser.add_argument('--output', type=Path, default=Path('dist/portable-outlines.json'))
    args = parser.parse_args()
    with TTFont(args.reference) as font:
        axes = font['fvar'].axes if 'fvar' in font else ()
        axis = next((axis for axis in axes if axis.axisTag == 'wght'), None)
        if 'CFF2' not in font or axis is None:
            parser.error('Expected the cubic weight reference from the portable build')
        if not all(axis.minValue <= w <= axis.maxValue for w in args.weights):
            parser.error(f'Weights must lie between {axis.minValue:g} and {axis.maxValue:g}')
        report = check_outlines(font, sorted(set(args.weights)))
    report['fontSha256'] = file_hash(args.reference)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    for weight, result in report['findings'].items():
        print(f'{weight:g}: {len(result["loops"])} new loops, {len(result["crossings"])} pairs with additional crossings')
    print(args.output)


if __name__ == '__main__':
    main()
