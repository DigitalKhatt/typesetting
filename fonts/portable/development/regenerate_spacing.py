#!/usr/bin/env python3
"""Reproduce the published positioning rules or fit candidates from the Mushaf corpus."""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory, mkdtemp

from tools.compare_baselines.compare_baselines import run_lock

from .project import DATA_DIR, FAMILIES, TYPESETTING_DIR, VALIDATION_DIR, WORKSPACE
from .validation.check_mark_spacing import check, prepare, weight_grid
from .validation.directwrite_spacing import DirectWriteProbe
from ..font_checks import file_hash
from .validation.probes import compile_probes
from ..mark_spacing import read_rules
from .spacing_curves import context_key, fit_curves, logical_glyphs, rule_index, serialize
from .spacing_history import replay

OUTPUT = VALIDATION_DIR / 'output'


def publish(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=path.parent) as tmp:
        temp = Path(tmp) / path.name
        temp.write_bytes(data)
        temp.replace(path)


def save_json(path, data):
    publish(path, (json.dumps(data, indent=2) + '\n').encode())


def rule_counts(rows):
    return dict(rules=len(rows), distinctIntermediateWeights=len(
        {w for row in rows if len(row) > 5 for w, _ in row[5]}))


def local_moves(probes, font, weight, gap, inputs, records, contacts, outlines, names, index, padding):
    """Measure proposals on the original shaped lines that the shared checker flagged."""
    affected = {int(line.split('\t', 1)[0]) for line in contacts.read_text().splitlines()}
    with records.open() as f:
        lines = [line for n, line in enumerate(f, 1) if n in affected]
    if len(lines) != len(affected):
        raise ValueError('A contact refers to a missing shaped line')
    cmd = [probes / 'mark_contacts', font, str(weight), str(len(lines)), inputs / 'glyphs.txt', str(gap)]
    ink = ['--outlines', outlines] if outlines else []
    records_text = ''.join(lines)
    suggested = subprocess.check_output([*cmd, str(gap + 1), *ink], input=records_text, text=True)
    if not suggested:
        for target in (gap + 1, gap):
            suggested = subprocess.check_output([*cmd, str(target), *ink, '--paired-marks'],
                                                input=records_text, text=True)
            if suggested:
                break
    votes = defaultdict(Counter)
    last = None
    for row in suggested.splitlines():
        n, target, first, final, dx, dy, kern = map(int, row.split())
        if n != last:
            sequence, positions = logical_glyphs(lines[n - 1], names)
            last = n
        eligible = [positions[i] for i in range(first, final + 1) if i in positions]
        key = context_key(sequence, positions[target], eligible[0], eligible[-1],
                          'kern' if kern else 'mark', index, padding)
        votes[key][dx, dy] += 1
    return votes


def measure(stage, probes, font, family, rows, args, profiles, common, dw, padding, suggest):
    inputs = stage / 'inputs'
    inputs.mkdir()
    places, em, corpus = prepare(family, inputs, font, profiles, common, args.corpus_root)
    names = [line.split()[0] for line in (inputs / 'glyphs.txt').read_text().splitlines()]
    gap = common['xpbd']['collisionReportMinGap']
    index = rule_index(rows)
    dest = stage / 'checks'
    dest.mkdir()

    def sample(job):
        engine, weight = job
        tag = f'{engine}-{weight}'
        votes = {}
        with TemporaryDirectory(dir=dest) as tmp:
            work = Path(tmp)
            result = check(probes, inputs, font, engine, weight, em, gap, places, work, suggest,
                           directwrite=dw, tajweed=corpus['tajweed'])
            if suggest and result['contacts']:
                outlines = work / (tag + '.outlines') if engine == 'directwrite' else None
                votes = local_moves(probes, font, weight, gap, inputs, work / (tag + '.records'),
                                    work / (tag + '.contacts'), outlines, names, index, padding)
            for suffix in ('.json', '.tsv.gz', '.log'):
                source = work / (tag + suffix)
                if source.is_file():
                    source.replace(dest / source.name)
        return result, votes

    votes = defaultdict(lambda: defaultdict(Counter))
    checks = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for result, proposals in pool.map(sample, [(e, w) for e in args.engine for w in args.weights]):
            checks.append(result)
            for key, choices in proposals.items():
                votes[key][result['weight']].update(choices)
    summary = dict(corpus=corpus, minimumGap=gap, checks=checks)
    if dw:
        summary['directWrite'] = dw.provenance
    save_json(dest / 'summary.json', summary)
    if suggest:
        # One vote per occurrence and engine. Retain the evidence for each fitted coordinate.
        evidence = [[list(seq), at, kind, w, [[dx, dy, count] for (dx, dy), count in sorted(counts.items())]]
                    for (seq, at, kind), observed in sorted(votes.items()) for w, counts in sorted(observed.items())]
        save_json(stage / 'proposals.json', evidence)
    intersections = sum(v for result in checks for k, v in result['kinds'].items() if k.endswith('.intersection'))
    total = sum(result['contacts'] for result in checks)
    return (intersections, total), votes, corpus


def fit_family(family, args, probes, profiles, common, dw):
    runs = args.output / 'runs'
    runs.mkdir(exist_ok=True)
    run = Path(mkdtemp(prefix=family + '-', dir=runs))
    rows = read_rules(family, args.seed_rules) if args.seed_rules else []
    history = []
    original_corpus = None
    best_score = None
    for number in range(args.rounds + 1):
        stage = run / f'round-{number}'
        rules = stage / 'rules' / (family + '.json')
        publish(rules, serialize(rows))
        fonts = stage / 'fonts'
        subprocess.run([sys.executable, '-m', f'{__package__}.build_portable', '--font', family,
                        '--spacing-rules', rules.parent, '--output', fonts], cwd=TYPESETTING_DIR, check=True)
        font = fonts / family / (FAMILIES[family] + '-Portable-VF.ttf')
        suggest = number < args.rounds
        score, votes, corpus = measure(stage, probes, font, family, rows, args, profiles, common, dw, number, suggest)
        if original_corpus is not None and corpus != original_corpus:
            raise ValueError(f'{family}: the validation corpus changed during the fit')
        original_corpus = corpus
        history.append(dict(round=number, intersections=score[0], contacts=score[1],
                            rulesSha256=file_hash(rules), fontSha256=file_hash(font), **rule_counts(rows)))
        if best_score is not None and score >= best_score:
            break
        best_score = score
        best_stage = stage
        if not votes:
            break
        updated, conflicts = fit_curves(rows, votes, args.weights)
        history[-1]['conflictingContextWeights'] = conflicts
        if serialize(updated) == serialize(rows):
            break
        rows = updated

    rule_dest = args.output / 'rules' / (family + '.json')
    font_dest = args.output / 'fonts' / family
    publish(rule_dest, (best_stage / 'rules' / rule_dest.name).read_bytes())
    for path in (best_stage / 'fonts' / family).iterdir():
        if path.suffix == '.ttf' or path.name == 'report.json':
            publish(font_dest / path.name, path.read_bytes())
    result = dict(family=family, corpus=corpus, engines=args.engine, weights=args.weights, rounds=history,
                  maximumRevisions=args.rounds, minimumGap=common['xpbd']['collisionReportMinGap'],
                  seedRules=str(args.seed_rules.resolve()) if args.seed_rules else None,
                  selected=str(best_stage.relative_to(args.output)), remainingContacts=best_score[1])
    save_json(run / 'fit.json', result)
    save_json(args.output / (family + '-fit.json'), result)
    print(f'{family}: {rule_dest}, {best_score[1]} remaining contacts over the selected samples', flush=True)
    return best_score[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='action', required=True)
    exact = subs.add_parser('replay', help='Recreate the published JSON from the recorded calibration')
    fit = subs.add_parser('fit', help='Build and measure new candidate rules on the original corpus')
    for cmd, directory in [(exact, 'portable-spacing-replay'), (fit, 'portable-spacing-fit')]:
        cmd.add_argument('--font', '--family', choices=FAMILIES, action='append', help='Repeat for multiple families')
        cmd.add_argument('--output', type=Path, default=OUTPUT / directory)
    exact.add_argument('--archive', type=Path, default=DATA_DIR / 'spacing-checkpoints.zip',
                       help='Historical checkpoints ZIP or extracted directory')
    fit.add_argument('--seed-rules', type=Path, metavar='DIR', help='Start from these FAMILY.json files (default: empty)')
    fit.add_argument('--corpus-root', type=Path, help='Prepared original corpus directory')
    fit.add_argument('--rounds', type=int, default=5, help='Maximum number of measured revisions (default: 5)')
    fit.add_argument('--weight-step', type=int, help='Sample every N weights from 400 to 700 (default grid: 400, 550, 700)')
    fit.add_argument('--weight', type=float, action='append', help='Sample an additional weight. Endpoints are always included.')
    fit.add_argument('--engine', choices=['ot', 'coretext', 'directwrite'], action='append',
                     help='Measurement engine, repeatable (default: HarfBuzz ot)')
    fit.add_argument('--dwrite-version', choices=['2.1.0', '2.6.4'])
    fit.add_argument('--jobs', type=int, default=3)
    args = parser.parse_args()
    families = list(dict.fromkeys(args.font or FAMILIES))
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.action == 'replay':
        with run_lock(args.output / '.spacing.lock'):
            files = {key: replay(key, args.archive) for key in families}
            for key, data in files.items():
                path = args.output / 'rules' / (key + '.json')
                publish(path, data)
                counts = rule_counts(json.loads(data))
                print(f'{key}: verified {path} ({counts["rules"]} rules, '
                      f'distinct intermediate weights = {counts["distinctIntermediateWeights"]})')
        return 0
    if args.jobs < 1 or args.rounds < 0 or (args.weight_step is not None and args.weight_step < 1):
        parser.error('Use positive jobs and weight step, and zero or more fitting rounds')
    args.engine = list(dict.fromkeys(args.engine or ['ot']))
    if args.dwrite_version and 'directwrite' not in args.engine:
        parser.error('--dwrite-version requires --engine directwrite')
    try:
        extra = list(args.weight or [])
        if args.weight_step is None:
            extra.append(550)
        args.weights = sorted({400, 700, *weight_grid(extra, args.weight_step, False)})
    except ValueError as error:
        parser.error(str(error))
    profiles = json.loads((VALIDATION_DIR / 'config/profiles.json').read_text())
    common = json.loads((VALIDATION_DIR / 'config/common.settings.json').read_text())
    with run_lock(args.output / '.spacing.lock'), TemporaryDirectory(dir=args.output) as tmp:
        probes = Path(tmp)
        compile_probes(probes, profiles)
        dw = (DirectWriteProbe(WORKSPACE / profiles['directWriteRoot'], args.dwrite_version or profiles['directWriteVersion'],
                               probes / 'windows') if 'directwrite' in args.engine else None)
        remaining = sum(fit_family(key, args, probes, profiles, common, dw) for key in families)
    return int(remaining != 0)


if __name__ == '__main__':
    raise SystemExit(main())
