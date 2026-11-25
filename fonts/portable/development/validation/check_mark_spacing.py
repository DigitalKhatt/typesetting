"""Apply the GUI's within-line Save Collision check to a shaped portable variable TTF."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import gzip
import json
from math import isfinite
from pathlib import Path
import shutil
import sqlite3
import subprocess
from tempfile import TemporaryDirectory
from time import sleep

from fontTools.ttLib import TTFont

from tools.compare_baselines.compare_baselines import run_lock
from tools.font_validation.validate import live_settings

from ..project import FAMILIES, FONTS_DIR, VALIDATION_DIR, WORKSPACE
from .directwrite_spacing import DirectWriteProbe
from ...font_checks import file_hash
from .portable_checks import corpus_run, cursive_subtables
from .probes import compile_probes


OUTPUT = VALIDATION_DIR / 'output' / 'portable-mark-spacing'


def write_glyph_metadata(font, extra_marks, path):
    classes = font['GDEF'].table.GlyphClassDef.classDefs
    entries, exits = set(), set()
    for _, subtable in cursive_subtables(font):
        for name, record in zip(subtable.Coverage.glyphs, subtable.EntryExitRecord, strict=True):
            if record.EntryAnchor is not None:
                entries.add(name)
            if record.ExitAnchor is not None:
                exits.add(name)
    path.write_text(''.join(
        f'{g} {int(classes.get(g) == 3 or g in extra_marks)} {int(g in entries or g in exits)} {int(g in entries)}\n'
        for g in font.getGlyphOrder()))


def prepare(key, folder, source, profiles, common, corpus_root=None):
    build = json.loads((source.parent / 'report.json').read_text())
    if build['fontSha256'] != file_hash(source):
        raise ValueError(f'{key}: TTF differs from its build report')
    family = profiles['fonts'][key]
    reference = build.get('shaping', {})
    run = Path(reference['referenceRun']) if reference else None
    if corpus_root is not None or run is None or not run.is_dir():
        run = corpus_run(key, profiles['directWriteVersion'], corpus_root)
    settings = json.loads((run / 'engine-settings.json').read_text())
    if (settings['direction'], settings['script'], settings['language']) != ('rtl','arab','ar'):
        raise ValueError('Expected the shared RTL Arabic shaping settings')
    if any(settings[k] != 'preserve' for k in ('coretextCGJ','directwriteCGJ')):
        raise ValueError('Use the original preserve-CGJ corpus')
    corpus_file = run / 'corpus.txt'
    original = corpus_file.read_text().splitlines()
    places = json.loads((run / 'locations.json').read_text())
    if not original or original != [row['text'] for row in places]:
        raise ValueError('Page and line map differs from the corpus')
    digest = file_hash(corpus_file)
    if reference and digest != reference['corpusSha256']:
        raise ValueError('Corpus differs from the build reference')
    shutil.copyfile(corpus_file, folder / 'corpus.txt')
    mode = live_settings(profiles['fonts'], key, 'unjustified')
    layout = family['layout']
    database = (WORKSPACE / common['database']).resolve()
    with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
        table = '"' + layout.replace('"', '""') + '"'
        coordinates = list(db.execute(f'SELECT page, line FROM {table} ORDER BY page, line'))
    if coordinates != [(r['page'], r['line']) for r in places]:
        raise ValueError(f'Corpus does not cover every line of {layout}')
    requested = json.loads((run / 'corpus-request.settings.json').read_text())
    if requested['layout'] != layout or requested['firstPage'] != 1 or requested['lastPage'] not in (0, places[-1]['page']):
        raise ValueError(f'Expected the full {layout} corpus')
    # The GUI's No justification branch sets Tajweed and an empty range for shr1.
    # Use the font's own advances, offsets and ordinary word spaces at every weight.
    feature = 'tjwd=' + str(int(mode['tajweed']))
    (folder / 'features.tsv').write_text(''.join(t.encode('utf-16-be').hex()+'\t'+feature+'\t0\n' for t in original))
    with TTFont(source) as font:
        em = font['head'].unitsPerEm
        if em != settings['size']:
            raise ValueError('Shaping and geometry must both use font units')
        write_glyph_metadata(font, common['xpbd']['additionalCollisionMarks'], folder / 'glyphs.txt')
    corpus = dict(layout=layout, justifier='none', style='none', tajweed=mode['tajweed'],
                  pages=places[-1]['page'], lines=len(places), corpusSha256=digest,
                  cgj=sum(line.count('\u034f') for line in original))
    return places, em, corpus


def check(probes, folder, font, engine, weight, em, gap, places, dest, keep_records=True, *,
          directwrite=None, tajweed=False, review_margin=0):
    dest.mkdir(parents=True, exist_ok=True)
    tag = f'{engine}-{weight}'
    records = dest / f'{tag}.records'
    raw = dest / f'{tag}.contacts'
    shape = [probes / 'trace', font, engine, 'layout', str(em), f'--records={weight}']
    measure = [probes / 'mark_contacts', font, str(weight), str(len(places)), folder / 'glyphs.txt', str(gap + review_margin)]
    if engine == 'directwrite':
        with TemporaryDirectory(dir=folder) as tmp:
            files = dest if keep_records else Path(tmp)
            positions, outlines = files/f'{tag}.records', files/f'{tag}.outlines'
            directwrite.shape(font, folder/'corpus.txt', tajweed, weight, positions, outlines, dest/f'{tag}.log')
            with positions.open('rb') as shaped, raw.open('wb') as out:
                subprocess.run([*measure, '--outlines', outlines], stdin=shaped, stdout=out, check=True)
    else:
        hb_records(shape, measure, folder/'features.tsv', records, raw, keep_records, engine, weight)
    return report_contacts(font, engine, weight, places, dest, tag, raw, keep_records, gap, review_margin)


def hb_records(shape, measure, features, records, raw, keep_records, engine, weight):
    with features.open('rb') as source, raw.open('wb') as out:
        if keep_records:
            with records.open('wb') as shaped:
                subprocess.run(shape, stdin=source, stdout=shaped, check=True)
            with records.open('rb') as shaped:
                subprocess.run(measure, stdin=shaped, stdout=out, check=True)
        else:
            with subprocess.Popen(shape, stdin=source, stdout=subprocess.PIPE) as shaper:
                try:
                    subprocess.run(measure, stdin=shaper.stdout, stdout=out, check=True)
                finally:
                    shaper.stdout.close()
                if shaper.wait():
                    raise RuntimeError(f'Incomplete {engine} shaping run at {weight}')


def report_contacts(font, engine, weight, places, dest, tag, raw, keep_records, gap, review_margin):
    kinds, pairs, lines = Counter(), Counter(), set()
    reviewed = 0
    header = 'page\tline\tfirstIndex\tsecondIndex\tfirstCluster\tsecondCluster\tfirstGlyph\tsecondGlyph\tfirstIsMark\tsecondIsMark\tsignedClearance\tnormalX\tnormalY\n'
    with ExitStack() as files:
        data = files.enter_context(raw.open())
        out = files.enter_context(gzip.open(dest / f'{tag}.tsv.gz', 'wt', encoding='utf-8'))
        out.write(header)
        nearby = None
        if review_margin:
            nearby = files.enter_context(gzip.open(dest / f'{tag}-nearby.tsv.gz', 'wt', encoding='utf-8'))
            nearby.write(header)
        for row in data:
            num, *fields = row.rstrip().split('\t')
            loc = places[int(num)-1]
            output_row = f'{loc["page"]}\t{loc["line"]}\t'+'\t'.join(fields)+'\n'
            clearance = float(fields[8])
            if nearby is not None:
                nearby.write(output_row)
                reviewed += 1
                if clearance >= gap:
                    continue
            first, second, ma, mb = fields[4:8]
            cat = 'mark' if '1' in (ma, mb) else 'baseOnly'
            kind = 'intersection' if clearance < 0 else 'belowGap'
            kinds[cat+'.'+kind] += 1
            pairs[first,second] += 1
            lines.add(num)
            out.write(output_row)
    result = dict(engine=engine, weight=weight, fontSha256=file_hash(font), contacts=sum(kinds.values()),
                  affectedLines=len(lines), kinds=dict(kinds), topPairs=[[a,b,n] for (a,b),n in pairs.most_common(12)])
    if review_margin:
        result.update(reviewPairs=reviewed, reviewGap=gap+review_margin)
        if engine == 'directwrite' and keep_records:
            result.update(recordsSha256=file_hash(dest / f'{tag}.records'),
                          outlinesSha256=file_hash(dest / f'{tag}.outlines'))
    (dest / f'{tag}.json').write_text(json.dumps(result, indent=2)+'\n')
    if not keep_records:
        raw.unlink()
    print(font.parent.name, tag, result['contacts'], dict(kinds), flush=True)
    return result


def weight_grid(weights, step, midpoints):
    values = set(weights or [])
    if step:
        values.update(range(400, 701, step))
        values.add(700)
    ordered = sorted(values or {400, 550, 700})
    if not all(400 <= w <= 700 for w in ordered):
        raise ValueError('Weight is outside the portable axis')
    if midpoints:
        if len(ordered) < 2:
            raise ValueError('Interval midpoints require at least two distinct weights')
        ordered = [(a + b) / 2 for a, b in zip(ordered, ordered[1:])]
    return [int(w) if int(w) == w else w for w in ordered]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--family', '--font', choices=FAMILIES, action='append')
    p.add_argument('--fonts', type=Path, default=FONTS_DIR, help='Verified input family build directories')
    p.add_argument('--output', type=Path, default=OUTPUT)
    p.add_argument('--corpus-root', type=Path, help='Prepared original corpus directory')
    p.add_argument('--weight', type=float, action='append', help='Test this additional axis value, repeatable')
    p.add_argument('--weight-step', type=int, help='Test the full 400–700 axis at this integer interval')
    p.add_argument('--interval-midpoints', action='store_true', help='Test halfway between each pair of successive selected weights')
    p.add_argument('--review-margin', type=float, default=0, help='Also save pairs within this many units above the GUI threshold for curve review')
    p.add_argument('--jobs', type=int, default=3)
    p.add_argument('--suggest', action='store_true', help='Write local trial moves for reported contacts')
    p.add_argument('--keep-records', action='store_true', help='Keep raw shaping and contact records for tuning')
    p.add_argument('--engine', choices=['ot','coretext','directwrite'], action='append')
    p.add_argument('--dwrite-version', choices=['2.1.0','2.6.4'], help='Microsoft DWriteCore package from the shared setup')
    p.add_argument('--stage')
    args = p.parse_args()
    profiles = json.loads((VALIDATION_DIR/'config/profiles.json').read_text())
    common = json.loads((VALIDATION_DIR/'config/common.settings.json').read_text())
    engines = list(dict.fromkeys(args.engine or ['ot']))
    if args.dwrite_version and 'directwrite' not in engines:
        p.error('--dwrite-version requires --engine directwrite')
    version = args.dwrite_version or profiles['directWriteVersion']
    stage = args.stage or (f'directwrite-{version}' if 'directwrite' in engines else 'checked')
    if not args.stage:
        if args.interval_midpoints:
            stage += '-midpoints'
        if args.review_margin:
            stage += f'-review-{args.review_margin:g}'
    if args.jobs < 1 or (args.weight_step is not None and args.weight_step <= 0):
        p.error('Jobs and weight step must be positive')
    if not isfinite(args.review_margin) or args.review_margin < 0:
        p.error('Review margin must be finite and nonnegative')
    try:
        tested = weight_grid(args.weight, args.weight_step, args.interval_midpoints)
    except ValueError as error:
        p.error(str(error))
    total = 0
    args.output.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output) as tmp:
        probes = Path(tmp)
        compile_probes(probes, profiles)
        directwrite = (DirectWriteProbe(WORKSPACE/profiles['directWriteRoot'], version, probes/'windows')
                       if 'directwrite' in engines else None)
        for key in args.family or FAMILIES:
            folder = args.output/key/'unjustified'
            folder.mkdir(parents=True, exist_ok=True)
            source_font = args.fonts/key/(FAMILIES[key]+'-Portable-VF.ttf')
            inputs = probes / key
            inputs.mkdir()
            font = inputs / source_font.name
            # Both build outputs are published with atomic renames. Snapshot
            # the matching pair so multiple validation engines can read at once.
            for attempt in range(3):
                shutil.copyfile(source_font.parent / 'report.json', inputs / 'report.json')
                shutil.copyfile(source_font, font)
                if json.loads((inputs / 'report.json').read_text())['fontSha256'] == file_hash(font):
                    break
                if attempt < 2:
                    sleep(.1)
            places, em, corpus = prepare(key, inputs, font, profiles, common, args.corpus_root)
            jobs = [(engine, weight) for engine in engines for weight in tested]
            dest = folder / stage
            dest.mkdir(exist_ok=True)
            gap = common['xpbd']['collisionReportMinGap']

            def measure(job):
                engine, weight = job
                result = check(probes, inputs, font, engine, weight, em, gap, places, dest,
                               args.keep_records or args.suggest, directwrite=directwrite, tajweed=corpus['tajweed'],
                               review_margin=args.review_margin)
                if args.suggest:
                    ink = ['--outlines', dest/f'{engine}-{weight}.outlines'] if engine == 'directwrite' else []
                    with (dest / f'{engine}-{weight}.records').open('rb') as shaped:
                        moves = subprocess.check_output([probes / 'mark_contacts', font, str(weight),
                            str(len(places)), inputs/'glyphs.txt', str(gap), str(gap+1), *ink], stdin=shaped)
                    with (dest / f'{engine}-{weight}-suggestions.tsv').open('w') as out:
                        out.write('page\tline\ttargetIndex\tfirstIndex\tlastIndex\tdxOrAdvance\tdy\tisKern\n')
                        for row in moves.decode().splitlines():
                            line, rest = row.split('\t', 1)
                            place = places[int(line)-1]
                            out.write(f'{place["page"]}\t{place["line"]}\t{rest}\n')
                return result

            with run_lock(dest / '.check.lock'), ThreadPoolExecutor(max_workers=args.jobs) as pool:
                results = list(pool.map(measure, jobs))
                summary = dict(corpus=corpus, minimumGap=gap, checks=results)
                if directwrite:
                    summary['directWrite'] = directwrite.provenance
                (dest/'summary.json').write_text(json.dumps(summary, indent=2)+'\n')
            total += sum(r['contacts'] for r in results)
    return int(total != 0)


if __name__ == '__main__':
    raise SystemExit(main())
