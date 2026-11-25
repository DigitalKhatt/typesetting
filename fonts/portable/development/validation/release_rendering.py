"""Sanitize the TTF and compare native reading-size word and line captures."""
from hashlib import sha256
from html import escape
import json
from pathlib import Path
import os
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory

from fontTools.ttLib import TTFont

from tools.font_validation.validate import load, save

from ..project import VALIDATION_DIR
from .check_mark_spacing import prepare
from .native_capture import PLATFORMS, capture, desktop_platform, renderer_source
from ...font_checks import file_hash


def prepare_kit(key, font, folder, profiles, corpus_root, proof_profile=None):
    folder.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=folder) as tmp:
        places, _, corpus = prepare(key, Path(tmp), font, profiles,
                                    load(VALIDATION_DIR/'config/common.settings.json'), corpus_root)
    lines = {(r['page'], r['line']): r['text'] for r in places}
    settings = load(proof_profile or Path(__file__).parent/'native_proof/samples.json')
    if (not settings['pixels'] or not all(isinstance(p, int) and p > 0 for p in settings['pixels'])
            or not settings['weights'] or not all(400 <= w <= 700 and isinstance(w, int) for w in settings['weights'])
            or not settings['samples'] or min(settings[k] for k in ('lineWidthEm','wordWidthEm','lineHeightEm',
                                                                    'baselineEm','guardPixels')) <= 0):
        raise ValueError('Invalid reading-proof sizes, weights or samples')
    cases = []
    for sample_loc in settings['samples']:
        page, line, word = sample_loc['page'], sample_loc['line'], sample_loc.get('word')
        original = lines[page, line]
        if word is not None and not 1 <= word <= len(original.split()):
            raise ValueError(f'{key} page {page} line {line} has no word {word}')
        text = original if word is None else original.split()[word-1]
        base = f'p{page}-l{line}' + (f'-word{word}' if word else '')
        for size in settings['pixels']:
            for weight in settings['weights']:
                sample = f'{base}-{size}px-w{weight}'
                for scale in (1, 4):
                    cases.append(dict(id=sample if scale == 1 else sample+'-4x', sample=sample,
                                      scale=scale, page=page, line=line, word=word, text=text,
                                      pixels=size*scale, weight=weight, guard=settings['guardPixels']*scale,
                                      inset=settings['insetPixels']*scale,
                                      width=round(settings['lineWidthEm' if word is None else 'wordWidthEm']*size)*scale,
                                      height=round(size*settings['lineHeightEm'])*scale,
                                      baseline=round(size*settings['baselineEm'])*scale))
    if len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Duplicate cases in the reading-proof profile')
    spec = dict(schema=1, family=key, tajweed=corpus['tajweed'], cases=cases)
    spec['samplesSha256'] = sha256(json.dumps(spec, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    spec.update(fontSha256=file_hash(font), corpusSha256=corpus['corpusSha256'])
    with TTFont(font, lazy=True) as opened:
        spec['glyfSha256'] = sha256(opened.reader['glyf']).hexdigest()
    with TemporaryDirectory(dir=folder) as tmp:
        tmpfont = Path(tmp)/'font.ttf'
        shutil.copyfile(font, tmpfont)
        tmpfont.replace(folder/'font.ttf')
    save(folder/'cases.json', spec)
    return spec


def sanitize(font, folder, override):
    exe = override or shutil.which('ots-sanitize')
    if not exe:
        try:
            import ots
        except ImportError:
            return dict(status='unavailable', message='Install opentype-sanitizer or pass --ots PATH')
        exe = next((p for p in Path(ots.__file__).parent.glob('ots-sanitize*') if p.is_file()), None)
    if not exe:
        return dict(status='unavailable', message='Cannot find the OpenType Sanitizer executable')
    with TemporaryDirectory(dir=folder) as tmp:
        cleaned = Path(tmp)/'sanitized.ttf'
        with (folder/'ots.log').open('wb') as log:
            process = subprocess.run([exe, font, cleaned], stdout=log, stderr=subprocess.STDOUT)
        missing, dropped = [], []
        if process.returncode == 0:
            with TTFont(cleaned) as checked, TTFont(font) as original:
                required = {'cmap','GSUB','GPOS','GDEF','glyf','gvar','fvar','HVAR','hmtx'}
                missing = sorted(required - set(original.keys()))
                dropped = sorted((required & set(original.keys())) - set(checked.keys()))
        return dict(status='failed' if process.returncode or missing or dropped else 'passed', exitCode=process.returncode,
                    missingFromInput=missing, droppedTables=dropped, executable=str(exe), log=str(folder/'ots.log'))


def counters(gray):
    """Count enclosed white components with four-connected scanline runs at 50% ink."""
    width, height = gray.size
    binary = gray.point(lambda x: 255 if x >= 128 else 0).tobytes()
    parents = [0]
    def root(i):
        while parents[i] != i:
            parents[i] = parents[parents[i]]
            i = parents[i]
        return i
    prev = []
    for y in range(height):
        current = []
        j = 0
        for match in re.finditer(b'\xff+', binary[y*width:(y+1)*width]):
            left, right = match.span()
            label = len(parents)
            parents.append(label)
            if y in (0, height-1) or left == 0 or right == width:
                parents[label] = 0
            while j < len(prev) and prev[j][1] <= left:
                j += 1
            k = j
            while k < len(prev) and prev[k][0] < right:
                a, b = root(label), root(prev[k][2])
                parents[max(a,b)] = min(a,b)
                k += 1
            current.append((left, right, label))
        prev = current
    return len({root(i) for i in range(len(parents))} - {0})


def image_at(path, row, case):
    from PIL import Image
    image = path/(case['id']+'.png')
    if file_hash(image) != row['pngSha256']:
        raise ValueError(f'Image digest differs from capture manifest: {image}')
    with Image.open(image) as opened:
        if opened.format != 'PNG' or opened.size != (case['width']+2*case['guard'], case['height']+2*case['guard']):
            raise ValueError(f'Unexpected reading-proof image format or size: {image}')
        return opened.convert('RGB')


def assess(target, kit, candidates, goldens, folder):
    from PIL import ImageChops
    spec = load(kit/'cases.json')
    family = spec['family']
    directory = candidates/target/family
    if not (directory/'latest.json').is_file():
        return dict(status='needs-review', reason='missing native capture', kit=str(kit))
    path = directory/load(directory/'latest.json')['run']
    manifest = load(path/'capture.json')
    if (manifest['platform'] != target or manifest['family'] != family or manifest['fontSha256'] != spec['fontSha256']
            or manifest['kitSha256'] != file_hash(kit/'cases.json') or manifest['samplesSha256'] != spec['samplesSha256']):
        return dict(status='needs-review', reason='capture belongs to another font or sample kit', kit=str(kit))
    source = renderer_source(target)
    if manifest.get('rendererSourceSha256') != source:
        return dict(status='needs-review', reason='capture uses a different native proof implementation', kit=str(kit))
    approved = None
    prior = {}
    reference_issue = 'awaiting approval'
    pointer = goldens/target/family/'approved.json' if goldens else None
    if pointer and pointer.is_file():
        approval = load(pointer)
        reference = pointer.parent/approval['run']
        if file_hash(reference/'capture.json') != approval['manifestSha256']:
            raise ValueError('Approved capture manifest has changed')
        golden = load(reference/'capture.json')
        if (golden['platform'], golden['family'], golden['samplesSha256'], golden['renderer'], golden['os'],
                golden.get('rendererSourceSha256')) == (
                target, family, spec['samplesSha256'], manifest['renderer'], manifest['os'], source):
            approved = reference
            prior = {r['id']: r for r in golden['cases']}
        else:
            reference_issue = 'approved samples or rendering environment differ'
    cases = spec['cases']
    if [c['id'] for c in cases] != [r['id'] for r in manifest['cases']]:
        raise ValueError('Incomplete or reordered native proof')
    results = []
    counter_counts = {}
    for case, row in zip(cases, manifest['cases'], strict=True):
        image = image_at(path, row, case)
        gray = image.convert('L')
        bounds = ImageChops.invert(gray).getbbox()
        g = case['guard']
        clipped = bool(bounds and (bounds[0]<g or bounds[1]<g or bounds[2]>g+case['width'] or bounds[3]>g+case['height']))
        holes = counters(gray)
        counter_counts[case['sample'], case['scale']] = holes
        issues = []
        for condition, issue in ((not bounds or not row['glyphs'], 'blank'), (clipped, 'clipped ink'),
                                 (row['fallbackGlyphs'], 'fallback'), (row['missingGlyphs'], 'missing glyph')):
            if condition:
                issues.append(issue)
        result = dict(id=case['id'], counters=holes, issues=issues)
        if approved is not None:
            old = image_at(approved, prior[case['id']], case)
            diff = ImageChops.difference(image, old)
            if diff.getbbox():
                issues.append('pixels changed')
                folder.mkdir(parents=True, exist_ok=True)
                diff.save(folder/(case['id']+'-diff.png'))
            if holes < counters(old.convert('L')):
                issues.append('fewer open counters than approved')
        results.append(result)
    for c, result in zip(cases, results):
        if c['scale'] == 1:
            result['fewerCountersThan4x'] = max(0, counter_counts[c['sample'],4] - result['counters'])
    status = 'failed' if any(r['issues'] for r in results) else 'passed' if approved else 'needs-review'
    return dict(status=status, capture=str(path), approved=str(approved) if approved else None,
                review=None if approved else reference_issue, os=manifest['os'], renderer=manifest['renderer'], cases=results)


def gallery_link(path, folder):
    try:
        relative = Path(os.path.relpath(path, folder)).as_posix()
    except ValueError:  # Windows paths on separate volumes
        relative = path.resolve().as_uri()
    return escape(relative, quote=True)


def check_rendering(key, font, folder, profiles, args):
    kit = folder/'kit'
    prepare_kit(key, font, kit, profiles, args.corpus_root, args.proof_profile)
    from .release_checks import stage_result
    ots = stage_result(lambda: sanitize(font, folder, args.ots))
    candidates = (args.captures or args.output/'captures').resolve()
    platforms = {}
    for target in PLATFORMS:
        def evaluate():
            pointer = candidates/target/key/'latest.json'
            current = False
            if pointer.is_file():
                prior = pointer.parent/load(pointer)['run']/'capture.json'
                old = load(prior) if prior.is_file() else {}
                current = (old.get('kitSha256') == file_hash(kit/'cases.json')
                           and old.get('rendererSourceSha256') == renderer_source(target))
            local_desktop = target == desktop_platform() and (not args.captures or not current)
            if local_desktop or (target == 'android' and args.capture_android):
                capture(kit, candidates, target, args.output/'native-tools', args.android_serial)
            return assess(target, kit, candidates, args.goldens, folder/'diffs'/target)
        platforms[target] = stage_result(evaluate)
        save(folder/(target+'.json'), platforms[target])
    statuses = {r['status'] for r in platforms.values()} | {ots['status']}
    status = 'passed' if statuses == {'passed'} else 'needs-review' if statuses <= {'passed','needs-review'} else 'failed'
    gallery = folder/'review.html'
    sections = []
    for target, result in platforms.items():
        info = [result['status'], result.get('renderer'), result.get('reason') or result.get('review') or result.get('message')]
        sections.append('<h2>'+escape(target.title())+'</h2><p>'+escape(' · '.join(filter(None, info)))+'</p>')
        if 'capture' not in result:
            continue
        results = {r['id']: r for r in result['cases']}
        for case in load(kit/'cases.json')['cases']:
            if case['scale'] != 1:
                continue
            name = case['id']
            run = Path(result['capture'])
            link = gallery_link(run/(name+'.png'), folder)
            high = gallery_link(run/(name+'-4x.png'), folder)
            item = results[name]
            label = f'Page {case["page"]}, line {case["line"]}' + (f', word {case["word"]}' if case['word'] else '')
            title = f'{label} · {case["pixels"]} px · weight {case["weight"]}'
            details = [*item['issues'], f'{item["counters"]} open counters']
            if item['fewerCountersThan4x']:
                details.append(f'{item["fewerCountersThan4x"]} fewer than at 4×')
            control_issues = results[name+'-4x']['issues']
            if control_issues:
                details.append('4× control shows ' + ', '.join(control_issues))
            links = [f'<a href="{high}">4× control</a>']
            if result.get('approved'):
                old = gallery_link(Path(result['approved'])/(name+'.png'), folder)
                links.append(f'<a href="{old}">Approved image</a>')
                if 'pixels changed' in item['issues']:
                    delta = gallery_link(folder/'diffs'/target/(name+'-diff.png'), folder)
                    links.append(f'<a href="{delta}">Pixel difference</a>')
            guard = case['guard']
            top = 100*guard/(case['height']+2*guard)
            side = 100*guard/(case['width']+2*guard)
            sections.append(f'<figure><figcaption>{escape(title)}</figcaption><a class="frame" href="{link}" '
                            f'style="--inset:{top:.5f}% {side:.5f}%"><img src="{link}" alt="{escape(title)}"></a>'
                            f'<p>{escape(" · ".join(details))} · {" · ".join(links)}</p></figure>')
    gallery.write_text('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
                       '<style>body{font:16px system-ui;margin:2rem;max-width:64rem}figure{margin:1rem 0;'
                       'border-top:1px solid #ccc;padding:1rem 0}figcaption{font-weight:600}.frame{display:inline-block;'
                       'max-width:100%;position:relative}.frame:after{content:"";position:absolute;inset:var(--inset);'
                       'outline:1px dotted #888;pointer-events:none}img{display:block;max-width:100%}</style>'
                       f'<title>{escape(key)} reading proofs</title><h1>{escape(key)} reading proofs</h1>'
                       '<p>The dotted rectangle marks the reading viewport. Open an image to see the original pixels.</p>'
                       + ''.join(sections), encoding='utf-8')
    return dict(status=status, sanitizer=ots, kit=str(kit), review=str(gallery), platforms=platforms)
