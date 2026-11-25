"""Compare two finished variable TTFs on the same captured Mushaf page plan."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from itertools import product
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from fontTools.ttLib import TTFont
from fontTools.ttLib.tables._g_l_y_f import flagOnCurve
from fontTools.varLib.instancer import instantiateVariableFont

from tools.font_validation.compare_opentype_runs import comparable_names

from ..project import FAMILIES, GLYPH_USAGE_DIR, VALIDATION_DIR, built_ttf
from .audit_portable_usage import save_json, verify_inputs
from ...font_checks import WEIGHTS, file_hash
from .probes import compile_corpus_trace


def records(probe, font, engine, mode, size, weight, plan, line_count, gid_map=None):
    args = [probe, font, engine, mode, str(size), f'--records={weight}']
    if gid_map:
        args.append(gid_map)
    rows = subprocess.check_output(args, input=plan).splitlines()
    if len(rows) != line_count:
        raise ValueError(f'Incomplete {engine} {mode} trace at {weight}')
    return rows


def differences(before, after, places):
    count, examples = 0, []
    for a, b, place in zip(before, after, places, strict=True):
        if a != b:
            count += 1
            if len(examples) < 5:
                examples.append(dict(page=place['page'], line=place['line'], before=a.decode(), after=b.decode()))
    return dict(changedLines=count, firstDifferences=examples)


def geometry(before, after, retained):
    with TTFont(before) as a, TTFont(after) as b:
        a_order, b_order = a.getGlyphOrder(), b.getGlyphOrder()
        results = {}
        for weight in WEIGHTS:
            with (instantiateVariableFont(a, {'wght': weight}) as first,
                  instantiateVariableFont(b, {'wght': weight}) as second):
                left, right = first['glyf'], second['glyf']
                changed = []
                for bid, aid in retained:
                    an, bn = a_order[aid], b_order[bid]
                    ac, ae, af = left[an].getCoordinates(left)
                    bc, be, bf = right[bn].getCoordinates(right)
                    if (ac != bc or ae != be or [v & flagOnCurve for v in af] != [v & flagOnCurve for v in bf]
                            or first['hmtx'][an] != second['hmtx'][bn]):
                        changed.append(an)
                results[weight] = changed
        return results


def compare(args, key, probe, work, profiles):
    folders = (args.before / key, args.after / key)
    builds = [json.loads((folder / 'report.json').read_text()) for folder in folders]
    paths = [built_ttf(folder, key) for folder in folders]
    for build, path in zip(builds, paths):
        if file_hash(path) != build['fontSha256'] or path.stat().st_size != build['stages']['packedTTF']['bytes']:
            raise ValueError(f'TTF differs from its build report: {path}')
    if builds[0]['shaping']['corpusSha256'] != builds[1]['shaping']['corpusSha256']:
        raise ValueError(f'{key}: different corpora')
    plan, places, size, corpus = verify_inputs(key, args.usage / key, paths[0], builds[0], profiles['fonts'][key]['layout'])
    counts = [build['checks']['glyphsChecked'] for build in builds]
    with TTFont(paths[0]) as a, TTFont(paths[1]) as b:
        names_a, names_b = comparable_names((a, b))
        identities = {name: i for i, name in enumerate(names_a[:counts[0]])}
        mapping = [identities.get(name, len(names_a) + i) if i < counts[1] else len(names_a) + i
                   for i, name in enumerate(names_b)]
        retained = [(i, old) for i, old in enumerate(mapping) if old < counts[0]]
        if len(set(mapping)) != len(mapping):
            raise ValueError('Ambiguous glyph identity after GID renumbering')
        cmap_a = {cp: a.getGlyphID(name) for cp, name in a.getBestCmap().items()}
        cmap_b = {cp: mapping[b.getGlyphID(name)] for cp, name in b.getBestCmap().items()}
        feature_sets = [{rec.FeatureTag for tag in ('GSUB', 'GPOS') for rec in f[tag].table.FeatureList.FeatureRecord}
                        for f in (a, b)]
    gid_map = work / f'{key}-gids.txt'
    gid_map.write_text('\n'.join(map(str, mapping)))
    selected = [tag for tag in dict.fromkeys(args.feature) if any(tag in s for s in feature_sets)]

    def check(pair):
        engine, weight = pair
        common = (size, weight, plan, len(places))
        def run(mode):
            return (records(probe, paths[0], engine, mode, *common),
                    records(probe, paths[1], engine, mode, *common, gid_map))
        default = run('default')
        page = run('layout')
        row = dict(engine=engine, weight=weight, default=differences(*default, places), layout=differences(*page, places))
        if selected and weight == 400:
            features = {}
            for tag in selected:
                before, after = run(tag)
                features[tag] = dict(beforeVsDefault=differences(default[0], before, places)['changedLines'],
                                     afterVsDefault=differences(default[1], after, places)['changedLines'],
                                     beforeVsAfter=differences(before, after, places)['changedLines'])
            row['explicitFeatures'] = features
        return row

    with ThreadPoolExecutor(3) as pool:
        runs = list(pool.map(check, product(('ot', 'coretext'), WEIGHTS)))
    different_geometry = geometry(*paths, retained)
    byte_counts = [path.stat().st_size for path in paths]
    result = dict(family=key, corpus=corpus, beforeSha256=builds[0]['fontSha256'], afterSha256=builds[1]['fontSha256'],
                  beforeBytes=byte_counts[0], afterBytes=byte_counts[1], savedBytes=byte_counts[0]-byte_counts[1],
                  beforeSourceGlyphs=counts[0], afterSourceGlyphs=counts[1], beforeTTFGlyphs=len(names_a),
                  afterTTFGlyphs=len(names_b), retainedSourceGlyphs=len(retained), cmapEqual=cmap_a==cmap_b,
                  changedRetainedOutlinesOrMetrics=different_geometry,
                  removedFeatures=sorted(feature_sets[0]-feature_sets[1]), addedFeatures=sorted(feature_sets[1]-feature_sets[0]),
                  runs=runs)
    save_json(args.output / f'{key}.json', result)
    if (not result['cmapEqual'] or any(different_geometry.values())
            or any(r[m]['changedLines'] for r in runs for m in ('default', 'layout'))):
        raise ValueError(f'{key}: corpus, retained glyphs or character map differ; see {args.output / (key + ".json")}')
    print(f"{key}: saved {result['savedBytes']:,} bytes, {len(places):,} lines matched in all {2*len(runs)} comparisons", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('before', type=Path)
    parser.add_argument('after', type=Path)
    parser.add_argument('--usage', type=Path, default=GLYPH_USAGE_DIR, help='Directory with the native per-line feature plans')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--font', choices=FAMILIES, action='append')
    parser.add_argument('--feature', action='append', default=[], help='Also measure an optional tag at 400 (repeatable)')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    profiles = json.loads((VALIDATION_DIR / 'config/profiles.json').read_text())
    with TemporaryDirectory(dir=args.output) as scratch:
        work = Path(scratch)
        probe = work / 'trace_corpus_glyphs'
        compile_corpus_trace(probe)
        for key in dict.fromkeys(args.font or FAMILIES):
            compare(args, key, probe, work, profiles)


if __name__ == '__main__':
    main()
