"""Generate a local weight proof of the glyphs used by the three portable corpora."""
import argparse
from collections import Counter
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import webbrowser

from fontTools.pens.basePen import BasePen
from fontTools.ttLib import TTFont

from ..project import CORPORA_DIR, FAMILIES, FONTS_DIR, OUTPUT_DIR, VALIDATION_DIR, built_ttf
from ..validation.check_mark_spacing import prepare
from ..validation.release_weights import coverage_plan


SITE = Path(__file__).with_name('site')
LABELS = {'madina': 'New Madina', 'oldmadina': 'Old Madina', 'indopak': 'IndoPak'}
CLASSES = {1: 'Base', 2: 'Ligature', 3: 'Mark', 4: 'Component'}


class OutlinePen(BasePen):
    def __init__(self, glyphs):
        super().__init__(glyphs)
        self.commands = []
        self.points = []

    def append(self, operation, *points):
        self.commands.append(operation)
        self.points.extend(value for point in points for value in point)

    def _moveTo(self, point):
        self.append('M', point)

    def _lineTo(self, point):
        self.append('L', point)

    def _qCurveToOne(self, control, point):
        self.append('Q', control, point)

    def _curveToOne(self, first, second, point):
        self.append('C', first, second, point)

    def _closePath(self):
        self.append('Z')


def outline_location(font):
    axis, = font['fvar'].axes
    minimum = -1 if axis.minValue < axis.defaultValue else 0
    maximum = 1 if axis.maxValue > axis.defaultValue else 0
    knots = {minimum, 0, maximum}
    # A glyph or any of its components may have intermediate gvar regions.
    # Each control point varies linearly between consecutive region boundaries.
    for variations in font['gvar'].variations.values():
        for variation in variations:
            knots.update(v for v in variation.axes.get('wght', ()) if minimum <= v <= maximum)
    axis_map = {-1: -1, 0: 0, 1: 1}
    if 'avar' in font:
        axis_map.update(font['avar'].segments['wght'])
    return (dict(min=axis.minValue, default=axis.defaultValue, max=axis.maxValue,
                 map=sorted(axis_map.items())), sorted(knots))


def glyph_outline(glyph_sets, name):
    drawings = []
    for glyphs in glyph_sets:
        pen = OutlinePen(glyphs)
        glyphs[name].draw(pen)
        drawings.append(pen)
    first = drawings[0]
    if any(pen.commands != first.commands for pen in drawings):
        raise ValueError(f'{name}: incompatible outlines across the weight axis')
    bounds = None
    if first.points:
        bounds = [min(min(p.points[::2]) for p in drawings), min(min(p.points[1::2]) for p in drawings),
                  max(max(p.points[::2]) for p in drawings), max(max(p.points[1::2]) for p in drawings)]
    changed = any(pen.points != first.points for pen in drawings[1:])
    return dict(commands=''.join(first.commands), masters=[p.points for p in drawings] if changed else [first.points],
                bounds=bounds, changed=changed)


def collect_usage(hb_shape, path, text, weights, places, em, tajweed):
    counts = [Counter() for _ in weights]
    samples = [{} for _ in weights]
    command = [hb_shape, str(path), f'--text-file={text}', '--shapers=ot',
               '--script=arab', '--language=ar', '--direction=rtl', '--cluster-level=1',
               '--utf8-clusters', '--no-positions', '--no-glyph-names',
               '--output-format=json', f'--font-size={em}', f'--features=tjwd={int(tajweed)}']
    for i, w in enumerate(weights):
        # Keep every line intact, including its original default ignorables.
        with subprocess.Popen([*command, f'--variations=wght={w}'], stdout=subprocess.PIPE) as probe:
            lines = 0
            for index, (raw, _) in enumerate(zip(probe.stdout, places, strict=True)):
                for row in json.loads(raw):
                    gid = row['g']
                    counts[i][gid] += 1
                    samples[i].setdefault(gid, (index, row['cl']))
                lines += 1
            if probe.wait() or lines != len(places):
                raise ValueError(f'Incomplete HarfBuzz trace at weight {w:g} for {path.name}')
    return counts, samples


def build_family(key, fonts, corpora, work, site, profiles, common):
    folder = fonts / key
    original = built_ttf(folder, key)
    work.mkdir()
    snapshot = work / original.name
    for source, dest in ((folder / 'report.json', work / 'report.json'), (original, snapshot)):
        if not source.is_file():
            raise FileNotFoundError(f'{source}. Build the three portable TTFs for this build selection first.')
        shutil.copyfile(source, dest)
    places, em, corpus = prepare(key, work, snapshot, profiles, common, corpora)
    build = json.loads((work / 'report.json').read_text())
    with TTFont(snapshot) as font:
        plan = coverage_plan(font)
        axis, knots = outline_location(font)
        weights = sorted({axis['min'], axis['default'], axis['max'], (axis['min'] + axis['max']) / 2}
                         | {w for w, reasons in plan.items() if 'GSUB feature switch' in reasons})
        counts, samples = collect_usage(profiles['hbShape'], snapshot, work / 'corpus.txt',
                                        weights, places, em, corpus['tajweed'])
        used = sorted(set().union(*counts))
        order = font.getGlyphOrder()
        classes = font['GDEF'].table.GlyphClassDef.classDefs
        glyph_sets = [font.getGlyphSet(location={'wght': knot}, normalized=True) for knot in knots]
        glyphs = []
        lines = {}
        for gid in used:
            if not 0 <= gid < len(order):
                raise ValueError(f'HarfBuzz returned invalid GID {gid} for {name}')
            glyph_name = order[gid]
            outline = glyph_outline(glyph_sets, glyph_name)
            examples = []
            for i, sample in enumerate(samples):
                if gid in sample:
                    index, cluster = sample[gid]
                    lines[index] = places[index]
                    examples.append([i, index, cluster])
            glyphs.append(dict(id=gid, name=glyph_name, kind=CLASSES.get(classes.get(glyph_name), 'Unclassified'),
                               counts=[count[gid] for count in counts], examples=examples, **outline))
    corpus_file = key + '.json'
    font_file = key + '.ttf'
    shutil.copyfile(snapshot, site / font_file)
    data = dict(id=key, label=LABELS[key], font=font_file, fontSha256=build['fontSha256'], em=em, corpus=corpus,
                axis=axis, knots=knots, weights=weights, glyphs=glyphs, lines=lines)
    (site / corpus_file).write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    print(f'{LABELS[key]}: {len(glyphs):,} used glyphs from {len(places):,} lines', flush=True)
    return dict(id=key, label=LABELS[key], data=corpus_file, used=len(glyphs))


def generate(fonts, corpora, output):
    profiles = json.loads((VALIDATION_DIR / 'config/profiles.json').read_text())
    common = json.loads((VALIDATION_DIR / 'config/common.settings.json').read_text())
    output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=output.parent, prefix='.glyph-review-') as temp:
        work = Path(temp)
        site = work / 'site'
        shutil.copytree(SITE, site)
        families = [build_family(key, fonts, corpora, work / key, site, profiles, common) for key in FAMILIES]
        (site / 'manifest.json').write_text(json.dumps({'families': families}, indent=2) + '\n', encoding='utf-8')
        output.mkdir(exist_ok=True)
        # Publish only once all three corpora and outlines have been verified.
        for path in sorted(site.iterdir(), key=lambda p: p.name == 'manifest.json'):
            path.replace(output / path.name)
    print(f'Glyph review: {output / "index.html"}', flush=True)


class ReviewHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()


def serve(output, port, open_browser):
    handler = partial(ReviewHandler, directory=str(output))
    with ThreadingHTTPServer(('127.0.0.1', port), handler) as server:
        url = f'http://127.0.0.1:{server.server_port}/'
        print(f'Local: {url}', flush=True)
        if open_browser:
            webbrowser.open(url)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fonts', type=Path, default=FONTS_DIR)
    parser.add_argument('--corpus-root', type=Path, default=CORPORA_DIR)
    parser.add_argument('--output', type=Path, default=OUTPUT_DIR / 'glyph-review')
    parser.add_argument('--serve', action='store_true', help='Serve the generated files on loopback')
    parser.add_argument('--port', type=int, default=8765, help='Local port, or 0 for an available port')
    parser.add_argument('--open', action='store_true', help='Open the local server in the default browser')
    args = parser.parse_args()
    if args.open and not args.serve:
        parser.error('--open requires --serve')
    if not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    output = args.output.resolve()
    try:
        generate(args.fonts.resolve(), args.corpus_root.resolve(), output)
        if args.serve:
            serve(output, args.port, args.open)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        parser.exit(1, f'{exc}\n')


if __name__ == '__main__':
    main()
