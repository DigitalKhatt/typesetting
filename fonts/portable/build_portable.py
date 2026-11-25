"""Build DigitalKhatt Portable TTF and WOFF2 fonts from static CFF2 exports."""
import argparse
from contextlib import contextmanager
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path
from shutil import copyfile
import subprocess
import sys
from tempfile import TemporaryDirectory

import fontTools
from fontTools.ttLib import TTFont

from . import compact_mark
from .bezier_weight import build, read_overrides
from .font_checks import check_font, check_woff2, file_hash
from .mark_spacing import RULES, apply_spacing, read_rules
from .truetype_weight import read_masters, share_contours, write_ttf


FAMILIES = {
    'madina': 'DigitalKhattNewMadina',
    'oldmadina': 'DigitalKhattOldMadina',
    'indopak': 'DigitalKhattIndoPak',
}


@dataclass
class CompiledFamily:
    ttf: Path
    woff2: Path | None
    cubic: Path
    flat: Path
    shared: Path
    layout: Path
    rules: list
    report: dict

    @property
    def fonts(self):
        return (self.ttf, self.woff2) if self.woff2 else (self.ttf,)

    @property
    def intermediates(self):
        return self.cubic, self.flat, self.shared, self.layout


@contextmanager
def build_lock(path):
    with path.open('a+b') as stream:
        try:
            if os.name == 'nt':
                import msvcrt

                if not stream.tell():
                    stream.write(b'\0')
                    stream.flush()
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError(f'This font is already building in {path.parent}') from error
        yield


def pack_layout(source, shared, final, work):
    with TTFont(source) as original, TTFont(shared) as before:
        for tag in ('GSUB', 'GPOS', 'GDEF'):
            if original.reader[tag] != before.reader[tag]:
                raise ValueError(f'{tag} changed before packing')
    xml = work / 'layout.ttx'
    ttx = [sys.executable, '-m', 'fontTools.ttx', '-q']
    subprocess.run([*ttx, '-t', 'GSUB', '-t', 'GPOS', '-o', xml, shared], check=True)
    subprocess.run([*ttx, '-b', '--no-recalc-timestamp', '-m', shared, '-o', final, xml], check=True)
    return xml


def compile_family(key, source, args, work, *, woff2):
    """Compile one static Portable export in a staging directory."""
    stem = FAMILIES[key] + '-Portable-VF'
    cubic, flat, shared = (work / name for name in ('weight-reference.otf', 'flat.ttf', 'shared.ttf'))
    final = work / (stem + '.ttf')
    spacing = []
    rules_digest = None
    if not args.no_spacing_adjustments:
        snapshot = work / (key + '.json')
        copyfile(args.spacing_rules / snapshot.name, snapshot)
        spacing = read_rules(key, work)
        rules_digest = file_hash(snapshot)
    compact = key == 'oldmadina' and not args.no_spacing_adjustments
    scales = [*reversed(args.scale), ('endofaya*', 0)]
    with TTFont(source, recalcBBoxes=False, recalcTimestamp=False) as font:
        if 'CFF2' not in font or 'fvar' in font:
            raise ValueError(f'{source}: expected a static Portable CFF2 export')
        if args.epoch is not None:
            epoch = args.epoch + 2082844800
            font['head'].created = font['head'].modified = epoch
        em = font['head'].unitsPerEm / 1000
        edits = read_overrides(args.overrides, font)
        variants = {}
        if compact:
            compact_mark.add_regular_copy(font)
            variants[compact_mark.ALTERNATE] = (compact_mark.SOURCE, compact_mark.compact_bold)
        changed = build(font, args.x * em, args.y * em, args.mark_scale, args.counter_scale,
                        scales, edits, bold_variants=variants, fixed_advances=args.fixed_advances)
        if compact:
            compact_mark.add_substitution(font, font['GSUB'].table)
            compact_mark.add_positioning_membership(font)
        apply_spacing(font, spacing,
                      parent_attachment=('fatha', 'meemiqlab') if key == 'oldmadina' else None,
                      glyph_alternates={compact_mark.SOURCE: compact_mark.ALTERNATE} if compact else None)
        font.save(cubic)
    masters, advances = read_masters(cubic, args.quadratic_error)
    write_ttf(cubic, flat, masters, advances)
    components, sharing = share_contours(masters)
    write_ttf(cubic, shared, components, advances)
    layout = pack_layout(cubic, shared, final, work)
    checks = check_font(source, final, masters, advances, layout, work, positioned=cubic, compact=compact)
    web = None
    if woff2:
        web = final.with_suffix('.woff2')
        with TTFont(final, recalcBBoxes=False, recalcTimestamp=False) as font:
            font.flavor = 'woff2'
            font.save(web, reorderTables=False)
        checks['woff2'] = check_woff2(final, web)
    artifacts = {path.name: {'bytes': path.stat().st_size, 'sha256': file_hash(path)}
                 for path in (final, web) if path is not None}
    report = dict(
        family=key,
        axis={'wght': {'min': 400, 'default': 400, 'max': 700}},
        sourceSha256=file_hash(source),
        sourceDateEpoch=args.epoch,
        fontTools=fontTools.__version__,
        spacing=dict(markRules=sum(r[2] == 'mark' for r in spacing),
                     kernRules=sum(r[2] == 'kern' for r in spacing),
                     rulesSha256=rules_digest),
        weight=dict(x=args.x, y=args.y, markScale=args.mark_scale, counterScale=args.counter_scale,
                    scalesInPriorityOrder=scales, fixedAdvances=args.fixed_advances,
                    quadraticErrorPer1000Em=args.quadratic_error, changedGlyphs=changed),
        sharing=sharing,
        bytesBeforeSharing=flat.stat().st_size,
        bytesBeforePacking=shared.stat().st_size,
        checks=checks,
        artifacts=artifacts,
    )
    if compact:
        report['spacing']['contextualBoldScale'] = compact_mark.BOLD_SCALE
    return CompiledFamily(final, web, cubic, flat, shared, layout, spacing, report)


def build_family(key, source, args):
    target = args.output
    with build_lock(target / f'.{key}.lock'), TemporaryDirectory(dir=target, prefix=f'.{key}-') as temporary:
        work = Path(temporary)
        source_copy = work / 'portable-regular.otf'
        copyfile(source, source_copy)
        compiled = compile_family(key, source_copy, args, work, woff2=True)
        output_report = work / f'{key}-build.json'
        output_report.write_text(json.dumps(compiled.report, indent=2) + '\n', encoding='utf-8')
        if args.keep_intermediates:
            intermediates = target / 'intermediates' / key
            intermediates.mkdir(parents=True, exist_ok=True)
            for artifact in (source_copy, *compiled.intermediates):
                artifact.replace(intermediates / artifact.name)
        for artifact in (*compiled.fonts, output_report):
            artifact.replace(target / artifact.name)
        final, web = compiled.fonts
        artifacts = compiled.report['artifacts']
        print(f'{key}: {artifacts[final.name]["bytes"]:,} bytes TTF, '
              f'{artifacts[web.name]["bytes"]:,} bytes WOFF2', flush=True)


def glyph_scale(value):
    pattern, _, number = value.rpartition('=')
    try:
        factor = float(number)
    except ValueError as error:
        raise argparse.ArgumentTypeError('Expected GLYPH_GLOB=FACTOR') from error
    if not pattern or not math.isfinite(factor) or factor < 0:
        raise argparse.ArgumentTypeError('Expected a glyph glob and a finite nonnegative strength')
    return pattern, factor


def add_build_options(parser):
    spacing = parser.add_mutually_exclusive_group()
    spacing.add_argument('--spacing-rules', type=Path, default=RULES, help='Directory containing FAMILY.json spacing rules')
    spacing.add_argument('--no-spacing-adjustments', action='store_true', help='Build with native positioning only')
    parser.add_argument('--font', action='append', choices=FAMILIES, help='Repeat to select families. Default is all three.')
    parser.add_argument('--x', type=float, default=12, help='Horizontal Bold offset per edge per 1000 em')
    parser.add_argument('--y', type=float, default=10, help='Vertical Bold offset per edge per 1000 em')
    parser.add_argument('--mark-scale', type=float, default=.45)
    parser.add_argument('--counter-scale', type=float, default=.5)
    parser.add_argument('--scale', type=glyph_scale, action='append', default=[], metavar='GLYPH_GLOB=FACTOR')
    parser.add_argument('--fixed-advances', action='store_true')
    parser.add_argument('--overrides', type=Path, help='Sparse Bold UFO for a single --font')
    parser.add_argument('--quadratic-error', type=float, default=.5, help='Cubic conversion tolerance per 1000 em')
    parser.add_argument('--keep-intermediates', action='store_true', help='Keep cubic and intermediate quadratic fonts')


def validate_build_options(parser, args):
    families = list(dict.fromkeys(args.font or FAMILIES))
    if len(families) != 1 and args.overrides:
        parser.error('--overrides requires a single --font')
    if not all(math.isfinite(value) and value > 0 for value in (args.x, args.y, args.quadratic_error)):
        parser.error('Offsets and cubic conversion tolerance must be positive and finite')
    if not all(0 <= value <= 1 for value in (args.mark_scale, args.counter_scale)):
        parser.error('Mark and counter strengths must be in [0, 1]')
    try:
        args.epoch = int(os.environ['SOURCE_DATE_EPOCH']) if 'SOURCE_DATE_EPOCH' in os.environ else None
        if args.epoch is not None and args.epoch < 0:
            raise ValueError
    except ValueError:
        parser.error('SOURCE_DATE_EPOCH must be a nonnegative Unix timestamp')
    return families


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--input-dir', type=Path, help='Directory containing madina.otf, oldmadina.otf and indopak.otf')
    inputs.add_argument('--regular-otf', type=Path, help='Static Portable OTF for a single --font')
    parser.add_argument('--output', type=Path, default=Path('dist'), help='Output directory. Default is dist.')
    add_build_options(parser)
    args = parser.parse_args()
    families = validate_build_options(parser, args)
    if len(families) != 1 and args.regular_otf:
        parser.error('--regular-otf requires a single --font')
    paths = {key: args.regular_otf or args.input_dir / (key + '.otf') for key in families}
    if missing := [str(path) for path in paths.values() if not path.is_file()]:
        parser.error('Missing Regular OTF input ' + ', '.join(missing))
    args.output.mkdir(parents=True, exist_ok=True)
    for key, path in paths.items():
        build_family(key, path, args)


if __name__ == '__main__':
    main()
