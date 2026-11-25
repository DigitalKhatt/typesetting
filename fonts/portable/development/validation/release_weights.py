"""Select test weights from the compiled OpenType layout and screen the original corpus."""
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from pathlib import Path
import platform
import subprocess

from fontTools.ttLib.tables import otTables as ot
from fontTools.varLib.models import piecewiseLinearMap

from .check_mark_spacing import check
from ...font_checks import file_hash
from tools.font_validation.validate import save


F2DOT14 = 16384


def variation_indices(table):
    pending, seen, result = [table], set(), set()
    while pending:
        obj = pending.pop()
        if isinstance(obj, ot.Device):
            if obj.DeltaFormat == 0x8000:
                result.add((obj.StartSize, obj.EndSize))
        elif isinstance(obj, (list, tuple)):
            pending.extend(obj)
        elif hasattr(obj, '__dict__') and id(obj) not in seen:
            seen.add(id(obj))
            pending.extend(vars(obj).values())
    return result


def coverage_plan(font):
    axes = font['fvar'].axes
    if len(axes) != 1 or axes[0].axisTag != 'wght':
        raise ValueError('Expected a single portable wght axis')
    axis = axes[0]
    low, default, high = axis.minValue, axis.defaultValue, axis.maxValue
    mapping = {-1: -1, 0: 0, 1: 1}
    if 'avar' in font:
        if font['avar'].majorVersion != 1:
            raise ValueError('The release weight planner supports avar version 1')
        mapping.update(font['avar'].segments['wght'])
    points = sorted(mapping.items())
    if any(b[1] <= a[1] for a, b in zip(points, points[1:])):
        raise ValueError('Cannot invert this non-increasing avar map')
    inverse = {y: x for x, y in points}
    def user(value):
        n = piecewiseLinearMap(value, inverse)
        return default + n * (high - default if n >= 0 else default - low)

    lower = -1 if low < default else 0
    upper = 1 if high > default else 0
    reasons = defaultdict(set)
    def include(n, reason):
        if lower <= n <= upper:
            reasons[user(n)].add(reason)
    for w in (low, default, high, *range(int(low), int(high) + 1, 25)):
        if low <= w <= high:
            reasons[w].add('axis/grid')
    for _, y in points:
        include(y, 'axis/avar')
    indices = variation_indices(font['GPOS'].table)
    if indices:
        store = font['GDEF'].table.VarStore
        active = set()
        for outer, inner in indices:
            if (outer, inner) == (65535, 65535):  # NO_VARIATION_INDEX
                continue
            data = store.VarData[outer]
            active.update(i for i, delta in zip(data.VarRegionIndex, data.Item[inner], strict=True) if delta)
        for idx in active:
            region = store.VarRegionList.Region[idx].VarRegionAxis[0]
            for n in (region.StartCoord, region.PeakCoord, region.EndCoord):
                include(n, 'GPOS variation region')
    for tag in ('GSUB', 'GPOS'):
        variations = getattr(font[tag].table, 'FeatureVariations', None)
        for record in variations.FeatureVariationRecord if variations else ():
            for condition in record.ConditionSet.ConditionTable if record.ConditionSet else ():
                if condition.Format != 1 or condition.AxisIndex != 0:
                    raise ValueError(f'Unsupported {tag} variation condition')
                for n in (condition.FilterRangeMinValue, condition.FilterRangeMaxValue):
                    for neighbor in (-1, 0, 1):
                        include(n + neighbor / F2DOT14, tag + ' feature switch')
    return {w: sorted(reasons[w]) for w in sorted(reasons)}


def adaptive_sweep(weights, measure, depth, minimum_step=.5):
    """Bisect near-threshold intervals, including intervals with a reported collision."""
    grid = sorted(weights)
    measured = {w: r for w, r in zip(grid, measure(grid), strict=True)}
    intervals = list(zip(grid, grid[1:]))
    for _ in range(depth):
        refine = [(a, b) for a, b in intervals if (b-a)/2 >= minimum_step and
                  (measured[a]['reviewPairs'] or measured[b]['reviewPairs'])]
        if not refine:
            break
        middle = [(a+b)/2 for a, b in refine]
        measured.update(zip(middle, measure(middle), strict=True))
        intervals = [pair for (a, b), m in zip(refine, middle) for pair in ((a, m), (m, b))]
    return [measured[w] for w in sorted(measured)]


def screen_engine(engine, plan, folder, probes, inputs, font, places, em, corpus, gap, args, dw):
    implementation = Path(__file__).parent
    identity = dict(font=file_hash(font), corpus=corpus, engine=engine, gap=gap, margin=args.review_margin,
                    platform=platform.platform(), harfbuzz=subprocess.check_output(['pkg-config', '--modversion',
                    'harfbuzz'], text=True).strip(), trace=file_hash(probes/'trace'),
                    geometry=file_hash(probes/'mark_contacts'), checker=file_hash(implementation/'check_mark_spacing.py'),
                    directWrite=dw.provenance if engine == 'directwrite' else None)
    digest = sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    dest = folder / engine / digest
    dest.mkdir(parents=True, exist_ok=True)
    save(dest/'inputs.json', identity)
    def sample(w):
        tag = f'{engine}-{w}'
        complete = dest / (tag + '.complete.json')
        if complete.is_file() and all((dest / (tag + ext)).is_file() for ext in ('.tsv.gz', '-nearby.tsv.gz')):
            return json.loads(complete.read_text())
        result = check(probes, inputs, font, engine, w, em, gap, places, dest, False,
                       directwrite=dw, tajweed=corpus['tajweed'], review_margin=args.review_margin)
        save(complete, result)
        return result
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        rows = adaptive_sweep(plan, lambda batch: pool.map(sample, batch), args.refine_depth)
    total = sum(row['contacts'] for row in rows)
    return dict(status='failed' if total else 'passed', weights=[r['weight'] for r in rows], contacts=total,
                fullCorpusLines=len(places), evidence=str(dest), screenedBelow=gap + args.review_margin)
