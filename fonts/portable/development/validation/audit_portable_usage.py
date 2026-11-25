"""Inventory portable glyph use and join it to an instrumented native export."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import csv
import gzip
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory

from fontTools.ttLib import TTFont

from tools.font_validation.compare_opentype_runs import comparable_names, outlines

from ..build_portable import export_regular, native_exporter
from ..project import FAMILIES, FONTS_DIR, GLYPH_USAGE_DIR, VALIDATION_DIR, WORKSPACE, built_ttf
from ...font_checks import file_hash
from .probes import compile_corpus_trace


# These standard Arabic features are already exercised in the default run.
DEFAULT_TAGS = {'ccmp', 'locl', 'isol', 'init', 'medi', 'fina', 'rlig', 'rclt', 'calt'}
DISPLAY_NAMES = {'madina': 'New Madina', 'oldmadina': 'Old Madina', 'indopak': 'IndoPak'}


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def write_rows(path, rows, columns=None):
    with path.open('w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=columns or list(rows[0]), delimiter='\t')
        writer.writeheader()
        writer.writerows(rows)


def verify_inputs(key, folder, font, build, layout):
    if file_hash(font) != build['fontSha256']:
        raise ValueError(f'{key}: TTF differs from the checked variable build')
    run = Path(build['shaping']['referenceRun'])
    corpus = run / 'corpus.txt'
    if file_hash(corpus) != build['shaping']['corpusSha256']:
        raise ValueError(f'{key}: saved corpus has changed')
    places = json.loads((run / 'locations.json').read_text())
    settings = json.loads((run / 'engine-settings.json').read_text())
    if (settings['script'], settings['direction'], settings['language']) != ('arab', 'rtl', 'ar'):
        raise ValueError('The corpus tracer expects the shared RTL Arabic settings')
    if any(settings[k] != 'preserve' for k in ('coretextCGJ', 'directwriteCGJ')):
        raise ValueError('Use the original preserve-CGJ reference run')
    manifest = json.loads((folder / 'layout.run.json').read_text())
    if not manifest['complete'] or manifest['options']['layout'] != layout or manifest['options']['lastPage'] not in (0, places[-1]['page']):
        raise ValueError(f'{key}: incomplete or wrong native layout')
    plan = gzip.decompress((folder / 'layout-features.tsv.gz').read_bytes())
    requested, native_parameter_lines = Counter(), 0
    for item, original, row in zip(plan.decode().splitlines(), corpus.read_text().splitlines(), places, strict=True):
        encoded, features, native = item.split('\t')
        if bytes.fromhex(encoded).decode('utf-16-be') != original or original != row['text']:
            raise ValueError(f'{key}: feature plan text differs on page {row["page"]}, line {row["line"]}')
        requested.update({f[:4] for f in features.split(',') if f})
        native_parameter_lines += int(native) > 0
    return plan, places, settings['size'], dict(layout=layout, pages=places[-1]['page'], lines=len(places),
        corpusSha256=file_hash(corpus), cgj=corpus.read_text().count('\u034f'), featureLines=dict(requested),
        nativeParameterLines=native_parameter_lines)


def collect(probe, font, plan, size, modes, line_count):
    def one(pair):
        engine, mode = pair
        data = subprocess.check_output([probe, font, engine, mode, str(size)], input=plan)
        lines = data.decode().splitlines()
        if lines.pop() != f'lines\t{line_count}':
            raise ValueError('Incomplete shaping trace')
        values = {}
        for row in lines:
            gid, count, first, nominal, boundary = map(int, row.split('\t'))
            values[gid] = (count, first, nominal, boundary)
        return (engine, mode), values

    with ThreadPoolExecutor(4) as pool:
        return dict(pool.map(one, modes))


def gsub_outputs(sub):
    kind = sub.LookupType
    if kind == 7:
        yield from gsub_outputs(sub.ExtSubTable)
    elif kind == 1:
        yield from (dst for src, dst in sub.mapping.items() if src != dst)
    elif kind in (2, 3):
        groups = sub.mapping if kind == 2 else sub.alternates
        for src, targets in groups.items():
            yield from (dst for dst in targets if src != dst)
    elif kind == 4:
        for rules in sub.ligatures.values():
            for rule in rules:
                yield rule.LigGlyph
    elif kind == 8:
        yield from (dst for src, dst in zip(sub.Coverage.glyphs, sub.Substitute, strict=True) if src != dst)
    elif kind not in (5, 6):
        raise ValueError(f'Unknown GSUB lookup type {kind}')


def layout_links(font, names):
    gsub = font['GSUB'].table
    lookups = gsub.LookupList.Lookup
    if len(lookups) != len(names):
        raise ValueError('Native and serialized GSUB lookup counts differ')
    producers, children = defaultdict(set), defaultdict(set)
    for index, lookup in enumerate(lookups):
        for sub in lookup.SubTable:
            for name in gsub_outputs(sub):
                producers[name].add(index)
            pending = [sub]
            while pending:
                item = pending.pop()
                if hasattr(item, 'SequenceIndex') and hasattr(item, 'LookupListIndex'):
                    children[index].add(item.LookupListIndex)
                else:
                    pending.extend(v.value for v in item.iterSubTables())
    features = defaultdict(set)
    for record in gsub.FeatureList.FeatureRecord:
        visited = set()
        pending = list(record.Feature.LookupListIndex)
        while pending:
            index = pending.pop()
            if index not in visited:
                visited.add(index)
                features[index].add(record.FeatureTag)
                pending.extend(children[index])
    return producers, features


def used_components(glyf, selected, order):
    parents = defaultdict(set)
    def visit(name, root):
        glyph = glyf[name]
        if glyph.isComposite():
            for component in glyph.components:
                child = component.glyphName
                if root not in parents[child]:
                    parents[child].add(root)
                    visit(child, root)
    for gid in selected:
        visit(order[gid], order[gid])
    return parents


def coordinates(values):
    result = list(map(float, values))
    while result and result[-1] == 0:
        result.pop()
    return tuple(result)


def try_unlinked(key, regular, groups, folder, probe, profiles, plan, size, lines):
    """Counterfactual native exports. Compare complete records after GID renumbering."""
    common = json.loads((VALIDATION_DIR / 'config/common.settings.json').read_text())
    names = [r['lookup'] for r in groups if not r['features']]
    trials = [[n] for n in names]
    # The IndoPak source gives these two declarations the same adjustment.
    pair = [n for n in ('forwawfea.l3', 'forwawfea.l4') if n in names]
    if len(pair) == 2 and pair != names:
        trials.append(pair)
    if len(names) > 1:
        trials.append(names)
    baseline, results = {}, []
    with TemporaryDirectory(dir=folder) as scratch:
        work = Path(scratch)
        for disabled in trials:
            config = dict(common, disabledLookups=[*common['disabledLookups'], *disabled])
            candidate = export_regular(native_exporter(profiles), key, config, work)
            with TTFont(regular) as a, TTFont(candidate) as b:
                aid, bid = comparable_names((a, b))
                indices = {name: i for i, name in enumerate(aid)}
                gid_map = [indices.get(name, len(aid)+i) for i, name in enumerate(bid)]
                id_map = work / 'canonical-gids.txt'
                id_map.write_text('\n'.join(map(str, gid_map)))
                before = outlines(a, a.getGlyphOrder())
                after = outlines(b, b.getGlyphOrder())
                altered = [bid[i] for i, old in enumerate(gid_map) if old < len(aid) and
                           (before(old) != after(i) or a['hmtx'][a.getGlyphOrder()[old]] != b['hmtx'][b.getGlyphOrder()[i]])]
                removed = sorted(set(aid)-set(bid))
                added = sorted(set(bid)-set(aid))
            diffs = {}
            for engine in ('ot', 'coretext'):
                for mode in ('default', 'layout'):
                    args = [engine, mode, str(size), '--records']
                    if (engine, mode) not in baseline:
                        baseline[engine, mode] = subprocess.check_output([probe, regular, *args], input=plan).splitlines()
                    current = subprocess.check_output([probe, candidate, *args, id_map], input=plan).splitlines()
                    if len(current) != lines:
                        raise ValueError('Partial counterfactual trace')
                    diffs[f'{engine}/{mode}'] = sum(left != right for left, right in
                        zip(baseline[engine, mode], current, strict=True))
            results.append(dict(disabled=disabled, sourceSha256=file_hash(regular), sourceGlyphs=len(aid), candidateGlyphs=len(bid),
                                netRemoved=len(aid)-len(bid), removedIdentities=removed, addedIdentities=added,
                                changedRetainedOutlinesOrMetrics=altered, changedShapedLines=diffs))
    save_json(folder / 'unlinked-experiments.json', results)
    return results


def audit(key, out, fonts, probe, profiles, verify_unlinked=False):
    folder = out / key
    build_dir = fonts / key
    build = json.loads((build_dir / 'report.json').read_text())
    font_path = built_ttf(build_dir, key)
    plan, places, size, corpus = verify_inputs(key, folder, font_path, build, profiles['fonts'][key]['layout'])
    provenance = json.loads((folder / 'native-trace.json').read_text())
    if not provenance['matchedNativeTables'] or provenance['sourceSha256'] != build['sourceSha256']:
        raise ValueError('Provenance requires a matching native export')
    source = build_dir / 'intermediates/portable-regular.otf'
    if file_hash(source) != build['sourceSha256']:
        raise ValueError('Native Regular differs from the variable build input')
    with TTFont(font_path) as font, TTFont(source) as original:
        order, src = font.getGlyphOrder(), original.getGlyphOrder()
        if order[:len(src)] != src:
            raise ValueError('Source glyph order mismatch')
        names = provenance['lookups']['GSUB']
        producers, features = layout_links(font, names)
        optional = sorted({r.FeatureTag for r in font['GSUB'].table.FeatureList.FeatureRecord} - DEFAULT_TAGS)
        modes = [(e, m) for e in ('ot', 'coretext') for m in ('default', 'layout')]
        traces = collect(probe, font_path, plan, size, modes + [('ot', tag) for tag in optional], len(places))
        zero = (0, 0, 0, 0)
        def output(mode):
            return {gid for engine in ('ot', 'coretext') for gid, v in traces[engine, mode].items() if v[0]}
        normal, layout = output('default'), output('layout')
        observed = normal | layout
        intermediate = {gid for mode in ('default', 'layout') for gid, v in traces['ot', mode].items() if v[3]}
        if any(gid >= len(src) for gid in observed | intermediate):
            raise ValueError('A TTF component helper surfaced in shaping')
        components = used_components(font['glyf'], observed, order)
        cmap = defaultdict(list)
        corpus_cmap = defaultdict(list)
        characters = set(''.join(p['text'] for p in places))
        for cp, name in font.getBestCmap().items():
            cmap[name].append(f'U+{cp:04X}')
            if chr(cp) in characters:
                corpus_cmap[name].append(f'U+{cp:04X}')
        without_role = set(src) - {order[i] for i in observed | intermediate} - components.keys() - corpus_cmap.keys()
        with (folder / 'live-glyphs.tsv').open() as f:
            live = Counter()
            for row in csv.DictReader(f, delimiter='\t'):
                vector = coordinates(x for x in row['coordinates'].split(',') if x)
                live[(row['name'], vector)] += int(row['occurrences'])
        rows, group_counts = [], defaultdict(Counter)
        optional_used = set()
        for gid, name in enumerate(order):
            created = provenance['created'].get(name)
            creator = created['scopes'][-1][1] if created else ''
            base = created['source'] if created else name
            coords = coordinates(created['coordinates']) if created else ()
            kind = ('TTF component' if gid >= len(src) else 'parameterized' if created else
                    'numbered verse ending' if re.fullmatch(r'endofaya\d+', name) else 'other native initial')
            opt = [tag for tag in optional if traces['ot', tag].get(gid, zero)[0]]
            if opt:
                optional_used.add(gid)
            native_count = live[(base, coords)] if gid < len(src) else 0
            g = group_counts[(kind, creator)]
            for field, condition in [('total', True), ('default', gid in normal), ('layout', gid in layout),
                                     ('either', gid in observed), ('boundaryOnly', gid not in observed and gid in intermediate),
                                     ('component', name in components), ('liveDesign', bool(native_count)),
                                     ('optionalOnly', gid not in observed and bool(opt))]:
                if condition:
                    g[field] += 1
            idxs = sorted(producers.get(name, ()))
            row = dict(gid=gid, glyph=name, origin=kind, base=base, parameters=','.join(map(str, coords)),
                       firstCreatingLookup=creator,
                       creatorFeatures=','.join(sorted(set().union(*(features[i] for i, v in enumerate(names) if v == creator)))),
                       defaultFinal=int(gid in normal), pageLayoutFinal=int(gid in layout),
                       gsubBoundary=int(gid in intermediate), componentOf=' '.join(sorted(components.get(name, ()))),
                       liveExactDesignOccurrences=native_count, cmap=' '.join(cmap[name]),
                       nominalCorpusCodepoints=' '.join(corpus_cmap[name]), noObservedPageRole=int(name in without_role),
                       optionalFeatureOutputs=','.join(opt),
                       linkedOutputFeatures=','.join(sorted(set().union(*(features[i] for i in idxs)))),
                       directGsubOutputLookups=' '.join(f'{i}:{names[i]}' for i in idxs))
            for mode in ('default', 'layout'):
                for engine in ('ot', 'coretext'):
                    count, first, _, _ = traces[engine, mode].get(gid, zero)
                    row[f'{mode}_{engine}_count'] = count
                    place = places[first-1] if first else None
                    row[f'{mode}_{engine}_first_page_line'] = f'{place["page"]}/{place["line"]}' if place else ''
            rows.append(row)
        source_rows = rows[:len(src)]
        write_rows(folder / 'glyphs.tsv', rows)
        columns = list(rows[0])
        write_rows(folder / 'unused-in-page-layout.tsv', [r for r in source_rows if not r['pageLayoutFinal']], columns)
        write_rows(folder / 'never-in-final-output.tsv', [r for r in source_rows if not r['pageLayoutFinal'] and not r['defaultFinal']], columns)
        write_rows(folder / 'no-observed-page-role.tsv', [r for r in source_rows if r['noObservedPageRole']], columns)
        write_rows(folder / 'component-helpers.tsv', rows[len(src):], columns)
        lookup_rows = []
        feature_source = (WORKSPACE / (key + 'font') / 'features.fea').read_text().splitlines()
        for (kind, creator), counts in sorted(group_counts.items(), key=lambda pair: -pair[1]['total']):
            if not creator:
                continue
            indices = [i for i, name in enumerate(names) if name == creator]
            source_line = next((i for i, line in enumerate(feature_source, 1)
                                if re.match(r'\s*lookup\s+' + re.escape(creator) + r'\s*\{', line)), None)
            lookup_rows.append(dict(lookup=creator, gsubIndices=','.join(map(str, indices)),
                features=','.join(sorted(set().union(*(features[i] for i in indices)))), created=counts['total'],
                defaultFinal=counts['default'], pageLayoutFinal=counts['layout'],
                unusedInPageLayout=counts['total'] - counts['layout'], unusedInEither=counts['total']-counts['either'],
                optionalOnly=counts['optionalOnly'], liveExactDesign=counts['liveDesign'], featureFileLine=source_line or ''))
        write_rows(folder / 'lookup-generators.tsv', lookup_rows)
        origins = defaultdict(Counter)
        for (kind, _), counts in group_counts.items():
            origins[kind].update(counts)
        final_sets = {e: {m: {gid for gid, v in traces[e, m].items() if v[0]} for m in ('default', 'layout')}
                      for e in ('ot', 'coretext')}
        details = {e: {m: len(gids) for m, gids in modes.items()} for e, modes in final_sets.items()}
        engine_differences = {m: len(final_sets['ot'][m] ^ final_sets['coretext'][m]) for m in ('default', 'layout')}
        info = dict(family=key, ttfSha256=file_hash(font_path), sourceSha256=build['sourceSha256'], corpus=corpus,
                    sourceGlyphs=len(src), ttfGlyphs=len(order), defaultFinal=len(normal), pageLayoutFinal=len(layout),
                    eitherFinal=len(observed), onlyWithPageFeatures=len(layout-normal), onlyWithDefault=len(normal-layout),
                    sourceUnusedInPageLayout=len(src)-len(layout), sourceUnusedInEither=len(src)-len(observed),
                    boundaryOnly=len(intermediate-observed), optionalOnly=len(optional_used-observed),
                    noObservedPageRole=len(without_role),
                    sourcesUsedAsComponents=sum(n in components for n in src),
                    sourcesOnlyAsComponents=sum(n in components and i not in observed for i, n in enumerate(src)),
                    usedHelpers=sum(n in components for n in order[len(src):]), nativeLiveTuples=len(live),
                    liveMatchingSourceGlyphs=sum(bool(r['liveExactDesignOccurrences']) for r in source_rows),
                    liveMatchesOutsidePage=sum(bool(r['liveExactDesignOccurrences']) and not r['pageLayoutFinal'] for r in source_rows),
                    perEngine=details, engineGlyphSetDifferences=engine_differences, groups=origins, creators=lookup_rows)
        save_json(folder / 'summary.json', info)
        if verify_unlinked:
            try_unlinked(key, source, lookup_rows, folder, probe, profiles, plan, size, len(places))
        return info


def write_report(out, results, checked_unlinked):
    source_root = os.path.relpath(WORKSPACE, out)
    total = sum(r['sourceGlyphs'] for r in results)
    used = sum(r['pageLayoutFinal'] for r in results)
    generated = sum(r['groups']['parameterized']['total'] for r in results)
    numbers = sum(r['groups']['numbered verse ending']['total'] for r in results)
    helpers = sum(r['ttfGlyphs'] - r['sourceGlyphs'] for r in results)
    lines = [
        '# Glyph use in the three portable Mushafs', '',
        f'The native portable exports contain **{total:,} glyphs** across the three families. '
        f'Their complete page layouts emit **{used:,}** of those glyphs and leave **{total-used:,}** out of the final strings. '
        f'The native exporter allocates {generated:,} parameterized alternates ahead of shaping. '
        f'Initialization supplies {numbers:,} numbered verse endings and {total-generated-numbers:,} other glyphs. '
        f'The variable TTFs append {helpers:,} contour-sharing helpers.', '',
        '| Family | Layout | Pages | Lines | Native glyphs | Final with defaults | Final with page features | Absent from page output |',
        '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |']
    for r in results:
        key, c = r['family'], r['corpus']
        lines.append(f"| {DISPLAY_NAMES[key]} | `{c['layout']}` | {c['pages']} | {c['lines']:,} | {r['sourceGlyphs']:,} "
                     f"| {r['defaultFinal']} | {r['pageLayoutFinal']} | [{r['sourceUnusedInPageLayout']:,}]({key}/unused-in-page-layout.tsv) |")
    lines.extend(['', 'Counts are distinct glyph IDs within each font. The links in the last column give every absent glyph by name and ID. '
                  'Each row also gives its base, design coordinates, first creating lookup, direct GSUB output lookups, optional features and component uses.', '',
                  '| Family | Other native initial glyphs | Numbered endings in page output / built | Parameterized alternates in page output / built | Parameterized alternates absent |',
                  '| --- | ---: | ---: | ---: | ---: |'])
    for r in results:
        groups = r['groups']
        p, n = groups['parameterized'], groups['numbered verse ending']
        lines.append(f"| {DISPLAY_NAMES[r['family']]} | {groups['other native initial']['total']} | {n['layout']} / {n['total']} "
                     f"| {p['layout']} / {p['total']:,} | {p['total']-p['layout']:,} |")
    lines.extend(['', 'The numbered ornaments are generated during native initialization. The `ayanumbers` GSUB lookup selects them when shaping the text.', '',
                  '## Which lookups allocate glyphs', '',
                  'A parameterized rule can apply to the base glyph and earlier variants of that base. During export, a previously unseen parameter combination receives its own glyph ID. '
                  'The tables credit that allocation to the first lookup that requests it. Several lookups can subsequently select or request the same design. '
                  'The four largest generators in each family appear below. The optional column counts additional outputs seen in single-feature tests. '
                  f'[Native parameter generation]({source_root}/visualmetafont/lib/digitalkhatt/src/otlayout/Layout/Subtable.cpp#L230) '
                  f'and [Old Madina parameter generation]({source_root}/oldmadinafont/oldmadina.cpp#L154).', '',
                  '| Family | First creating lookup | Reachable from GSUB feature | Created | In page output | Absent | Additional optional outputs |',
                  '| --- | --- | --- | ---: | ---: | ---: | ---: |'])
    for r in results:
        for c in r['creators'][:4]:
            name = c['lookup']
            if c['featureFileLine']:
                name = f"[{name}]({source_root}/{r['family']}font/features.fea#L{c['featureFileLine']})"
            lines.append(f"| {DISPLAY_NAMES[r['family']]} | {name} | {c['features'] or 'None'} | {c['created']} "
                         f"| {c['pageLayoutFinal']} | {c['unusedInPageLayout']} | {c['optionalOnly']} |")
    lines.extend(['', '## Other uses of glyphs absent from final strings', '',
                  'The following counts can overlap. GSUB intermediates were seen at a HarfBuzz lookup or table boundary. Component counts include recursive outline dependencies of final glyphs. '
                  'An optional test enables one additional GSUB feature at value 1 across the whole corpus. The native renderer also emits designs with private justification parameters. '
                  'The final column counts portable glyphs whose base and full coordinates match a native design even though the portable page output never emits their ID.', '',
                  '| Family | Non-final GSUB intermediates | Non-final native glyphs used as components | Helpers used / total | Additional optional-feature outputs | Native design tuples | Non-final IDs matching native designs |',
                  '| --- | ---: | ---: | ---: | ---: | ---: | ---: |'])
    for r in results:
        lines.append(f"| {DISPLAY_NAMES[r['family']]} | {r['boundaryOnly']} | {r['sourcesOnlyAsComponents']} "
                     f"| {r['usedHelpers']} / {r['ttfGlyphs']-r['sourceGlyphs']} | {r['optionalOnly']} | {r['nativeLiveTuples']:,} | {r['liveMatchesOutsidePage']} |")
    if checked_unlinked and any(not c['features'] for r in results for c in r['creators']):
        lines.extend(['', '## Rebuilding without lookup generators unreachable from exported features', '',
                      'Each trial disables the named lookups in a temporary native portable Regular export. Every retained outline and horizontal metric is compared. '
                      'After mapping renumbered IDs, both engines compare glyphs, clusters, advances and offsets on every line under defaults and under the saved page features.', '',
                      '| Family | Lookups disabled together | Native glyphs saved | Retained outlines or metrics changed | Most changed lines in one run |',
                      '| --- | --- | ---: | ---: | ---: |'])
        combined_savings = 0
        for r in results:
            trials = json.loads((out / r['family'] / 'unlinked-experiments.json').read_text())
            unlinked = {c['lookup'] for c in r['creators'] if not c['features']}
            for t in trials:
                if t['sourceSha256'] != r['sourceSha256']:
                    raise ValueError('Counterfactual export and inventory have different source fonts')
                if set(t['disabled']) == unlinked:
                    combined_savings += t['netRemoved']
                disabled = ', '.join(f'`{n}`' for n in t['disabled'])
                lines.append(f"| [{DISPLAY_NAMES[r['family']]}]({r['family']}/unlinked-experiments.json) | {disabled} | {t['netRemoved']} "
                             f"| {len(t['changedRetainedOutlinesOrMetrics'])} | {max(t['changedShapedLines'].values())} |")
        lines.extend(['', f'Disabling all of these generators together within each family saved **{combined_savings:,} source glyphs** across the three test exports. '
                      'Each linked experiment records the changed-line count for all four comparisons and the exact identities added or removed.'])
    elif checked_unlinked:
        lines.extend(['', '## Reachability of alternate generators', '',
                      'Every lookup that first allocated a parameterized alternate in these exports is reachable from an exported GSUB feature.'])
    lines.extend(['', '## Coverage and evidence', '',
                  'The audit captures the final per-line feature values and UTF-16 ranges from the configured native justifier. It checks every line against the original corpus and page/line map with CGJs retained. '
                  'The same line is shaped with the portable TTF in HarfBuzz OpenType and CoreText at weights 400, 550 and 700. Each engine must produce the same glyph sequence at all three weights. '
                  'Features available in the portable font take effect. The native renderer can additionally apply private parameters after GSUB. Its per-design counts use exact base names and full coordinates. '
                  'A native design can match more than one portable ID. The evidence covers these texts, layouts and feature settings.', '',
                  '| Family | All glyphs and roles | Generating lookups | Native designs used | Counts and input hashes |',
                  '| --- | --- | --- | --- | --- |'])
    for r in results:
        k = r['family']
        lines.append(f"| {DISPLAY_NAMES[k]} | [Glyph inventory]({k}/glyphs.tsv) | [All {len(r['creators'])} generators]({k}/lookup-generators.tsv) "
                     f"| [Exact tuples]({k}/live-glyphs.tsv) | [Summary]({k}/summary.json) |")
    if all(not any(r['engineGlyphSetDifferences'].values()) for r in results):
        lines.extend(['', 'HarfBuzz OpenType and CoreText emitted the same sets of final glyph IDs for every family under both default and per-line features.'])
    (out / 'REPORT.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', choices=FAMILIES, action='append')
    parser.add_argument('--fonts', type=Path, default=FONTS_DIR, help='Checked fonts used by the native capture')
    parser.add_argument('--audit-dir', type=Path, default=GLYPH_USAGE_DIR, help='Native capture and audit directory')
    parser.add_argument('--check-unlinked', action='store_true',
                        help='Disable feature-unreachable generators in temporary exports and compare the whole corpus')
    args = parser.parse_args()
    profiles = json.loads((VALIDATION_DIR / 'config/profiles.json').read_text())
    with TemporaryDirectory(dir=args.audit_dir) as work:
        probe = Path(work) / 'trace_corpus_glyphs'
        compile_corpus_trace(probe)
        results = []
        for key in dict.fromkeys(args.font or FAMILIES):
            result = audit(key, args.audit_dir, args.fonts, probe, profiles, args.check_unlinked)
            results.append(result)
            print(key, result['sourceGlyphs'], result['pageLayoutFinal'], result['sourceUnusedInPageLayout'], flush=True)
        if len(results) == len(FAMILIES):
            write_report(args.audit_dir, results, args.check_unlinked)


if __name__ == '__main__':
    main()
