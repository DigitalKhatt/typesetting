"""Bound the ink distance of selected pairs from a saved DirectWrite clearance run."""
import argparse
from collections import Counter
import csv
import gzip
from heapq import heappop, heappush
from itertools import count
import json
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

from fontTools.pens.recordingPen import RecordingPen
from fontTools.pens.transformPen import TransformPen
from fontTools.svgLib.path import parse_path
from fontTools.ttLib import TTFont

from tools.font_validation.validate import save

from ..project import FAMILIES, FONTS_DIR, VALIDATION_DIR, built_ttf
from .check_mark_spacing import prepare
from ...font_checks import file_hash


def positioned_path(path, origin):
    drawing = RecordingPen()
    parse_path(path, TransformPen(drawing, (1, 0, 0, 1, *origin)))
    return drawing


def segments(drawing):
    curves, starts = [], []
    current = start = None
    for op, args in drawing.value:
        points = tuple(complex(x, y) for x, y in args)
        if op == 'moveTo':
            current = start = points[0]
            starts.append(start)
        elif op == 'closePath':
            if current != start:
                curves.append((current, start))
            current = start = None
        elif op in ('lineTo', 'qCurveTo', 'curveTo') and current is not None:
            curves.append((current, *points))
            current = points[-1]
        else:
            raise ValueError(f'Unexpected native path command {op}')
    if current is not None or not curves:
        raise ValueError('Expected a closed glyph with ink')
    return curves, starts


def cross(a, b):
    return a.real * b.imag - a.imag * b.real


def point_segment(p, a, b):
    d = b - a
    if not d:
        return abs(p - a)
    t = max(0, min(1, ((p - a).conjugate() * d).real / abs(d)**2))
    return abs(p - (a + t * d))


def chord_distance(a, b, c, d):
    if (cross(b-a, c-a) * cross(b-a, d-a) < 0 and
            cross(d-c, a-c) * cross(d-c, b-c) < 0):
        return 0.
    return min(point_segment(a, c, d), point_segment(b, c, d),
               point_segment(c, a, b), point_segment(d, a, b))


def chord_error(curve):
    # Write the chord in the same Bernstein basis as the curve. At each t the
    # difference is a convex combination of these control-point differences.
    degree = len(curve) - 1
    return max((abs(p - (curve[0] + (curve[-1]-curve[0]) * i/degree))
                for i, p in enumerate(curve[1:-1], 1)), default=0.)


def split(curve):
    left, right = [curve[0]], [curve[-1]]
    while len(curve) > 1:
        curve = tuple((p + q) * .5 for p, q in zip(curve, curve[1:]))
        left.append(curve[0])
        right.append(curve[-1])
    return tuple(left), tuple(reversed(right))


def curve_info(curve):
    bounds = (min(p.real for p in curve), min(p.imag for p in curve),
              max(p.real for p in curve), max(p.imag for p in curve))
    return curve, chord_error(curve), bounds


def boundary_distance(outline_a, outline_b, tolerance):
    if not isfinite(tolerance) or tolerance < 1e-6:
        raise ValueError('Use a finite tolerance of at least 0.000001 font unit')
    pending, serial = [], count()
    best = float('inf')

    def consider(first, second):
        nonlocal best
        a, ea, box_a = first
        b, eb, box_b = second
        if (box_a[2] + best < box_b[0] or box_b[2] + best < box_a[0] or
                box_a[3] + best < box_b[1] or box_b[3] + best < box_a[1]):
            return
        chord = chord_distance(a[0], a[-1], b[0], b[-1])
        # Each curve stays within its chord error in both directions. The
        # minimum curve distance thus differs from the chord gap by at most
        # ea + eb. Allow for floating-point arithmetic in font units.
        error = ea + eb + 1e-9
        lower = max(0., chord - error)
        best = min(best, chord + error)
        if lower < best:
            heappush(pending, (lower, next(serial), first, second))

    first = [curve_info(c) for c in outline_a]
    second = [curve_info(c) for c in outline_b]
    for a in first:
        for b in second:
            consider(a, b)
    while pending and best - pending[0][0] > tolerance:
        _, _, a, b = heappop(pending)
        if a[1] >= b[1]:
            for child in split(a[0]):
                consider(curve_info(child), b)
        else:
            for child in split(b[0]):
                consider(a, curve_info(child))
    return min(best, pending[0][0]) if pending else best, best


def contains_point(curves, point, max_error):
    # A half-open ray crossing counts a shared vertex exactly once. A chord
    # approximation finer than the point's distance to the boundary preserves
    # its winding throughout the deformation from each curve to its chord.
    winding = 0
    pending = list(curves)
    while pending:
        curve = pending.pop()
        if (all(p.imag > point.imag for p in curve) or all(p.imag < point.imag for p in curve)
                or all(p.real < point.real for p in curve)):
            continue
        if chord_error(curve) > max_error:
            pending.extend(split(curve))
            continue
        a, b = curve[0], curve[-1]
        side = cross(b - a, point - a)
        if a.imag <= point.imag < b.imag and side > 0:
            winding += 1
        elif b.imag <= point.imag < a.imag and side < 0:
            winding -= 1
    return winding != 0


def ink_distance(first, second, tolerance):
    a, a_starts = segments(first)
    b, b_starts = segments(second)
    lower, upper = boundary_distance(a, b, tolerance)
    if lower == 0:
        return lower, upper
    # Two disjoint boundaries can still have overlapping fills if one glyph
    # contains the other. Nonzero winding also handles marks inside counters.
    if any(contains_point(b, p, lower/2) for p in a_starts) or any(contains_point(a, p, lower/2) for p in b_starts):
        return 0., 0.
    return lower, upper


def read_origins(row, selected):
    x = y = 0.
    result = {}
    # Native files list visual glyph order. The RTL pen advances in logical
    # order; the returned offsets use the draw origin after each advance.
    for i, cell in enumerate(reversed(row.rstrip().split('|'))):
        gid, cluster, ax, ay, dx, dy = cell.split(',')
        x -= float(ax)
        y -= float(ay)
        if i in selected:
            result[i] = (int(gid), int(cluster), (x + float(dx), y + float(dy)))
    if set(result) != selected:
        raise ValueError('Glyph index outside the selected line')
    return result


def review_cases(run, tag, check, numbers):
    if 'reviewPairs' not in check:
        raise ValueError('Generate this run with --review-margin and --keep-records')
    candidates = {}
    with gzip.open(run / f'{tag}-nearby.tsv.gz', 'rt') as src:
        for row in csv.DictReader(src, delimiter='\t'):
            page, line, i, j = (int(row[k]) for k in ('page', 'line', 'firstIndex', 'secondIndex'))
            if not 0 <= i < j:
                raise ValueError('Invalid glyph indices in the measured candidates')
            candidates.setdefault(numbers[page, line], set()).add((page, line, i, j))
    if sum(map(len, candidates.values())) != check['reviewPairs']:
        raise ValueError('The candidate file differs from its summary')
    return candidates


def check_weight(run, check, chosen, names, tolerance, minimum, numbers, nearby):
    tag = f"directwrite-{check['weight']}"
    records, outlines = (run / (tag + ext) for ext in ('.records', '.outlines'))
    hashes = dict(recordsSha256=file_hash(records), outlinesSha256=file_hash(outlines))
    if any(field in check and check[field] != value for field, value in hashes.items()):
        raise ValueError('Native records or outlines differ from the saved run')
    cases = review_cases(run, tag, check, numbers) if nearby else {}
    for number, requests in chosen.items():
        cases.setdefault(number, set()).update(requests)
    origins, needed = {}, set()
    line_count = 0
    with records.open() as src:
        for line_count, row in enumerate(src, 1):
            if line_count in cases:
                indices = {index for _, _, i, j in cases[line_count] for index in (i, j)}
                origins[line_count] = read_origins(row, indices)
                needed.update(gid for gid, _, _ in origins[line_count].values())
    if line_count != len(numbers) or set(cases) != set(origins):
        raise ValueError('DirectWrite records differ from the expected lines')
    paths = {}
    with outlines.open() as src:
        for row in src:
            gid, _, path = row.strip().partition(' ')
            if int(gid) in needed:
                if int(gid) in paths:
                    raise ValueError(f'Duplicate outline for glyph {gid}')
                paths[int(gid)] = path
    drawings = {gid: positioned_path(path, (0, 0)) for gid, path in paths.items()}
    bounds_by_placement, counts, results = {}, Counter(), []
    for number, requests in cases.items():
        for page, line, i, j in sorted(requests):
            a, b = (origins[number][index] for index in (i, j))
            dx, dy = (q - p for p, q in zip(a[2], b[2]))
            key = a[0], b[0], dx, dy
            if key not in bounds_by_placement:
                second = RecordingPen()
                drawings[b[0]].replay(TransformPen(second, (1, 0, 0, 1, dx, dy)))
                precision = tolerance
                lower, upper = ink_distance(drawings[a[0]], second, precision)
                while lower < minimum <= upper and precision > 1e-6:
                    precision = max(1e-6, precision / 10)
                    lower, upper = ink_distance(drawings[a[0]], second, precision)
                bounds_by_placement[key] = lower, upper
            lower, upper = bounds_by_placement[key]
            if lower >= minimum:
                status = 'pass'
            elif upper < minimum:
                status = 'belowMinimum'
            else:
                status = 'indeterminate'
            counts[status] += 1
            if status != 'pass' or (page, line, i, j) in chosen.get(number, ()):
                results.append(dict(page=page, line=line, indices=[i, j], glyphs=[names[a[0]], names[b[0]]],
                                    utf16Clusters=[a[1], b[1]], lower=lower, upper=upper, status=status))
    return dict(weight=check['weight'], **hashes, measuredPairs=counts.total(),
                distinctPlacements=len(bounds_by_placement), outcomes=dict(counts), pairs=results)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--family', choices=FAMILIES, required=True)
    parser.add_argument('--fonts', type=Path, default=FONTS_DIR)
    parser.add_argument('--corpus-root', type=Path, help='Prepared original corpus directory')
    parser.add_argument('--run', type=Path, required=True, help='DirectWrite stage saved with --keep-records')
    parser.add_argument('--weight', type=float, action='append', required=True)
    parser.add_argument('--case', type=int, nargs=4, metavar=('PAGE', 'LINE', 'FIRST', 'SECOND'),
                        action='append', default=[], help='Page, line and two zero-based logical glyph indices')
    parser.add_argument('--nearby', action='store_true', help='Remeasure every candidate saved by --review-margin')
    parser.add_argument('--tolerance', type=float, default=.005, help='Maximum width of the distance interval')
    parser.add_argument('--output', type=Path, help='Write the JSON measurements to this file')
    args = parser.parse_args()
    if not args.case and not args.nearby:
        parser.error('Select at least one --case or use --nearby')
    if not isfinite(args.tolerance) or args.tolerance < 1e-6:
        parser.error('Tolerance must be finite and at least 0.000001')
    profiles = json.loads((VALIDATION_DIR/'config/profiles.json').read_text())
    common = json.loads((VALIDATION_DIR/'config/common.settings.json').read_text())
    font = built_ttf(args.fonts / args.family, args.family)
    summary = json.loads((args.run/'summary.json').read_text())
    if 'directWrite' not in summary:
        parser.error('Choose a run with native DirectWrite outline records')
    with TemporaryDirectory() as tmp:
        places, _, corpus = prepare(args.family, Path(tmp), font, profiles, common, args.corpus_root)
    if corpus != summary['corpus']:
        parser.error('The saved run uses a different corpus or layout')
    numbers = {(p['page'], p['line']): n for n, p in enumerate(places, 1)}
    cases = {}
    for page, line, i, j in args.case:
        if (page, line) not in numbers or not 0 <= i < j:
            parser.error('Choose an existing page and line with logical indices 0 <= FIRST < SECOND')
        cases.setdefault(numbers[page, line], set()).add((page, line, i, j))
    weights = set(args.weight)
    checks = [r for r in summary['checks'] if r['engine'] == 'directwrite' and r['weight'] in weights]
    digest = file_hash(font)
    if {r['weight'] for r in checks} != weights or any(r['fontSha256'] != digest for r in checks):
        parser.error('Requested weights and font hash must match the saved DirectWrite run')
    with TTFont(font) as opened:
        names = opened.getGlyphOrder()
    minimum = common['xpbd']['collisionReportMinGap']
    results = [check_weight(args.run, r, cases, names, args.tolerance, minimum, numbers, args.nearby) for r in checks]
    report = dict(family=args.family, fontSha256=digest, corpus=corpus, directWrite=summary['directWrite'],
                  minimumGap=minimum, tolerance=args.tolerance, checks=results)
    if args.nearby:
        report['reviewGaps'] = sorted({r['reviewGap'] for r in checks})
    if args.output:
        save(args.output, report)
    else:
        print(json.dumps(report, indent=2))
    return int(any(any(k != 'pass' for k in r['outcomes']) for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
