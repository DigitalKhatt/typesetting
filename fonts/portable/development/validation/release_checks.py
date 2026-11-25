#!/usr/bin/env python3
"""Run the three ordered release checks on published portable variable TTFs."""
import argparse
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory

from fontTools.ttLib import TTFont


from tools.compare_baselines.compare_baselines import run_lock
from tools.font_validation.validate import load, save

from ..project import FAMILIES, FONTS_DIR, VALIDATION_DIR, WORKSPACE
from .check_mark_spacing import prepare
from .directwrite_spacing import DirectWriteProbe
from ...font_checks import file_hash
from .probes import compile_probes
from .release_weights import coverage_plan, screen_engine


STAGES = ('weight-coverage', 'attachments', 'app-rendering')


def add_release_switches(parser):
    for name in STAGES:
        parser.add_argument('--no-' + name, action='store_true', help='Disable the ' + name + ' release check')


def stage_result(check):
    try:
        return check()
    except Exception as exc:
        # Preserve independent stages and report the exceptional check as incomplete.
        return dict(status='error', message=f'{type(exc).__name__}: {exc}')


def weight_check(key, font, folder, work, profiles, common, args):
    inputs = work / 'inputs'
    inputs.mkdir()
    places, em, corpus = prepare(key, inputs, font, profiles, common, args.corpus_root)
    with TTFont(font) as opened:
        plan = coverage_plan(opened)
    save(folder/'plan.json', dict(weights=plan, corpus=corpus, refinementDepth=args.refine_depth))
    compile_probes(work, profiles)
    engines = {}
    for engine in args.engine:
        def measure():
            dw = (DirectWriteProbe(WORKSPACE/profiles['directWriteRoot'], args.dwrite_version, work/'windows')
                  if engine == 'directwrite' else None)
            return screen_engine(engine, plan, folder, work, inputs, font, places, em, corpus,
                                 common['xpbd']['collisionReportMinGap'], args, dw)
        engines[engine] = stage_result(measure)
    return dict(status='passed' if all(r['status'] == 'passed' for r in engines.values()) else 'failed',
                requiredWeights=len(plan), engines=engines)


def run(args):
    profiles = load(VALIDATION_DIR/'config/profiles.json')
    common = load(VALIDATION_DIR/'config/common.settings.json')
    args.engine = list(dict.fromkeys(args.engine or ['ot', 'coretext', 'directwrite']))
    args.dwrite_version = args.dwrite_version or profiles['directWriteVersion']
    args.output.mkdir(parents=True, exist_ok=True)
    disabled = [s for s in STAGES if getattr(args, 'no_' + s.replace('-', '_'))]
    families = list(dict.fromkeys(args.font or FAMILIES))
    report = dict(status='running', disabled=disabled, order=STAGES, engines=args.engine,
                  dwriteVersion=args.dwrite_version, reviewMargin=args.review_margin, refineDepth=args.refine_depth,
                  families={key: {} for key in families})
    summary = args.output / 'release.json'
    with run_lock(args.output/'.release.lock'), TemporaryDirectory(dir=args.output) as temporary:
        save(summary, report)
        # Finish each stage for every family before starting the next stage.
        for stage in STAGES:
            for key in families:
                font = args.fonts / key / (FAMILIES[key]+'-Portable-VF.ttf')
                folder = args.output/key/stage
                def evaluate():
                    build = load(font.parent/'report.json')
                    if file_hash(font) != build['fontSha256'] or build['family'] != key:
                        raise ValueError(f'{font} does not match its build report')
                    work = Path(temporary)/key/stage
                    work.mkdir(parents=True)
                    folder.mkdir(parents=True, exist_ok=True)
                    if stage == 'weight-coverage':
                        result = weight_check(key, font, folder, work, profiles, common, args)
                    elif stage == 'attachments':
                        from .release_attachments import check_attachments
                        result = check_attachments(key, font, folder, work, profiles, common, args)
                    else:
                        from .release_rendering import check_rendering
                        result = check_rendering(key, font, folder, profiles, args)
                    if file_hash(font) != build['fontSha256']:
                        raise ValueError(f'{font} changed during release validation')
                    return dict(fontSha256=build['fontSha256'], **result)
                result = dict(status='disabled') if stage in disabled else stage_result(evaluate)
                report['families'][key][stage] = result
                if stage not in disabled:
                    save(folder/'summary.json', result)
                save(summary, report)
                print(f'{key} {stage}: {result["status"]} {result.get("message", "")}', flush=True)
        entries = [r for stages in report['families'].values() for r in stages.values()]
        if all(r['status'] == 'disabled' for r in entries):
            report['status'] = 'disabled'
        elif any(r['status'] not in ('passed', 'disabled') for r in entries):
            report['status'] = 'incomplete' if all(r['status'] in ('passed','disabled','needs-review')
                                                    for r in entries) else 'failed'
        else:
            partial = (disabled or set(families) != set(FAMILIES)
                       or set(args.engine) != {'ot','coretext','directwrite'} or not args.refine_depth)
            report['status'] = 'passed-selected' if partial else 'passed'
        save(summary, report)
    print(f'{report["status"]}: {summary}', flush=True)
    return int(report['status'] in ('failed', 'incomplete'))


def add_release_options(p):
    add_release_switches(p)
    p.add_argument('--engine', action='append', choices=['ot','coretext','directwrite'],
                   help='Weight screen engine. Repeat to select several (release default: all three)')
    p.add_argument('--dwrite-version', choices=['2.1.0', '2.6.4'])
    p.add_argument('--jobs', type=int, default=3)
    p.add_argument('--review-margin', type=float, default=1, help='Refine when a measured gap is below minimum + margin')
    p.add_argument('--refine-depth', type=int, default=1, help='Maximum bisections per near-threshold interval')
    p.add_argument('--ots', type=Path, help='OpenType Sanitizer executable')
    p.add_argument('--proof-profile', type=Path, help='Native reading-size sample and viewport JSON')
    p.add_argument('--captures', type=Path, help='Native capture directories imported from target hosts')
    p.add_argument('--capture-android', action='store_true', help='Capture on an attached Android device using adb')
    p.add_argument('--android-serial', help='ADB device for --capture-android')
    p.add_argument('--goldens', type=Path, help='Directory of explicitly approved native captures')


def validate_options(p, args):
    if args.jobs < 1 or not 0 <= args.refine_depth <= 12 or not isfinite(args.review_margin) or args.review_margin <= 0:
        p.error('Use positive jobs and review margin, and a refinement depth between 0 and 12')
    if args.android_serial and not args.capture_android:
        p.error('--android-serial requires --capture-android')


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fonts', type=Path, default=FONTS_DIR)
    p.add_argument('--font', action='append', choices=FAMILIES)
    p.add_argument('--output', type=Path, help='Default: release/ beside fonts/')
    p.add_argument('--corpus-root', type=Path)
    p.add_argument('--spacing-rules', type=Path, help='JSON used for this build, for older intermediates')
    add_release_options(p)
    args = p.parse_args(argv)
    validate_options(p, args)
    args.fonts = args.fonts.resolve()
    args.output = (args.output or args.fonts.parent/'release').resolve()
    return run(args)


if __name__ == '__main__':
    raise SystemExit(main())
