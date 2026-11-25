"""Export the workspace's Portable fonts and compile them with the release builder."""
import argparse
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory

from fontTools.ttLib import TTFont

from tools.compare_baselines.compare_baselines import PROFILES, run_lock

from ..build_portable import add_build_options, compile_family, validate_build_options
from ..font_checks import file_hash
from .project import FAMILIES, FONTS_DIR, VALIDATION_DIR, WORKSPACE
from .validation.portable_checks import corpus_run, shape_corpus


TABLES = ('GSUB', 'GPOS', 'GDEF', 'CFF2', 'glyf', 'gvar', 'loca', 'hmtx', 'HVAR')


def native_exporter(profiles):
    build_dir = WORKSPACE / profiles['buildDir']
    name = 'digitalkhatt_generate_opentype' + ('.exe' if os.name == 'nt' else '')
    for subdir in ('visualmetafont/lib/digitalkhatt', 'lib/digitalkhatt'):
        for config in (profiles['configuration'], ''):
            candidate = build_dir / subdir / config / name
            if candidate.is_file():
                return candidate
    raise FileNotFoundError(f'Build {name} under {build_dir} or specify --exporter')


def export_regular(exporter, key, common, work):
    _, directory, project_name, *_ = PROFILES[key]
    project = WORKSPACE / directory / (project_name + '.mp')
    source = work / 'portable-regular.otf'
    cmd = [exporter, '--profile', 'portable', '--no-variable', '--resources', WORKSPACE / common['resources'],
           '--features', common['features']]
    for lookup in common['disabledLookups']:
        cmd.extend(['--disable-lookup', lookup])
    with (work / 'export.log').open('w') as log:
        result = subprocess.run([*cmd, '-o', source, project], cwd=WORKSPACE, stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'{key} portable export failed\n' + (work / 'export.log').read_text(errors='replace')[-3500:])
    return source


def sizes(path):
    with TTFont(path) as font:
        return dict(bytes=path.stat().st_size, tables={tag: len(font.reader[tag]) for tag in TABLES if tag in font})


def build_family(key, args, exporter, profiles, common):
    target = args.output / key
    target.mkdir(parents=True, exist_ok=True)
    with run_lock(target / '.build.lock'), TemporaryDirectory(dir=target, prefix='.build-') as temporary:
        work = Path(temporary)
        print(f'{key}: exporting portable Regular and generating Bold', flush=True)
        source = export_regular(exporter, key, common, work)
        compiled = compile_family(key, source, args, work, woff2=args.woff2)
        stages = {stage: sizes(path) for stage, path in
                  [('sourceRegular', source), ('flatTTF', compiled.flat),
                   ('sharedTTF', compiled.shared), ('packedTTF', compiled.ttf)]}
        report = dict(compiled.report, profile='portable', axis={'wght': [400, 400, 700]}, stages=stages,
                      fontSha256=compiled.report['artifacts'][compiled.ttf.name]['sha256'])
        report['weight']['overrides'] = str(args.overrides) if args.overrides else None
        report['spacing'].pop('rulesSha256')
        cubic, flat, final = compiled.cubic, compiled.flat, compiled.ttf
        spacing = compiled.rules
        if args.keep_intermediates:
            rules_copy = work / 'spacing-rules.json'
            rules_copy.write_text(json.dumps(spacing, ensure_ascii=False) + '\n', encoding='utf-8')
            report['spacing']['rulesSha256'] = file_hash(rules_copy)
            report['cubicSha256'] = file_hash(cubic)
        if args.check_shaping:
            run = args.reference_run or corpus_run(key, profiles['directWriteVersion'], args.corpus_root)
            print(f'{key}: shaping the original corpus against the cubic and flat references', flush=True)
            regular = cubic if any(any(rule[3]) for rule in spacing) else source
            shaping = shape_corpus(run, dict(source=regular, cubic=cubic, flat=flat, final=final), profiles['hbShape'])
            shaping['regularReference'] = 'positioned cubic at default weight' if regular == cubic else 'native portable Regular'
            report['shaping'] = shaping
            if any(row['differentFromFlat'] or row['differentFromCubic'] for row in shaping['comparisons']):
                failure = work / 'failed-shaping.json'
                failure.write_text(json.dumps(shaping, indent=2) + '\n')
                failure.replace(target / failure.name)
                raise ValueError(f'{key} shaping differs; see {target / failure.name}')
        output_report = work / 'report.json'
        output_report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        if args.keep_intermediates:
            dest = target / 'intermediates'
            dest.mkdir(exist_ok=True)
            for path in (source, *compiled.intermediates, rules_copy, work / 'export.log'):
                path.replace(dest / path.name)
        for artifact in compiled.fonts:
            artifact.replace(target / artifact.name)
        output_report.replace(target / output_report.name)
        print(f'{key}: {stages["flatTTF"]["bytes"]:,} -> {stages["sharedTTF"]["bytes"]:,} -> '
              f'{stages["packedTTF"]["bytes"]:,} bytes; {target / final.name}', flush=True)


def main():
    from .validation import release_checks
    parser = argparse.ArgumentParser(description=__doc__)
    add_build_options(parser)
    parser.add_argument('--output', type=Path, default=FONTS_DIR, help='Family build root (default: output/fonts)')
    parser.add_argument('--exporter', type=Path, help='Built digitalkhatt_generate_opentype executable')
    parser.add_argument('--woff2', action='store_true', help='Also encode and verify WOFF2 files')
    parser.add_argument('--check-shaping', action='store_true', help='Compare full traces on a saved shared Mushaf corpus')
    corpus = parser.add_mutually_exclusive_group()
    corpus.add_argument('--reference-run', type=Path, help='Shared corpus run for a single family with --check-shaping')
    corpus.add_argument('--corpus-root', type=Path, help='Prepared original corpus directory')
    parser.add_argument('--release', action='store_true', help='Build with shaping references and run all enabled release checks')
    parser.add_argument('--release-output', type=Path, help='Release evidence directory (default: release/ beside fonts/)')
    release_checks.add_release_options(parser.add_argument_group('release validation'))
    args = parser.parse_args()
    if args.release:
        release_checks.validate_options(parser, args)
        args.keep_intermediates = args.check_shaping = args.woff2 = True
    elif args.release_output or any(getattr(args, 'no_'+s.replace('-', '_')) for s in release_checks.STAGES):
        parser.error('Use --release to select release checks')
    args.output = args.output.resolve()
    families = validate_build_options(parser, args)
    if len(families) > 1 and args.reference_run:
        parser.error('--reference-run requires a single --font')
    if (args.reference_run or args.corpus_root) and not args.check_shaping:
        parser.error('--reference-run and --corpus-root require --check-shaping')
    profiles = json.loads((VALIDATION_DIR / 'config/profiles.json').read_text())
    common = json.loads((VALIDATION_DIR / 'config/common.settings.json').read_text())
    exporter = args.exporter.resolve() if args.exporter else native_exporter(profiles)
    for key in families:
        build_family(key, args, exporter, profiles, common)
    if args.release:
        args.fonts = args.output
        args.output = (args.release_output or args.fonts.parent/'release').resolve()
        args.spacing_rules = None  # Read the exact rule snapshot written by this build.
        return release_checks.run(args)


if __name__ == '__main__':
    raise SystemExit(main())
