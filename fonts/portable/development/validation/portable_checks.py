"""Compare shaping on the original Mushaf corpus."""
from contextlib import ExitStack
import json
from pathlib import Path
import subprocess

from fontTools.ttLib.tables import otTables

from ...font_checks import WEIGHTS, file_hash
from ..project import CORPORA_DIR, VALIDATION_DIR


def cursive_subtables(font):
    if 'GPOS' not in font or font['GPOS'].table.LookupList is None:
        return
    for index, lookup in enumerate(font['GPOS'].table.LookupList.Lookup):
        for subtable in lookup.SubTable:
            subtable = getattr(subtable, 'ExtSubTable', subtable)
            if isinstance(subtable, otTables.CursivePos):
                yield index, subtable


def corpus_run(key, version, corpus_root=None):
    family = (corpus_root or CORPORA_DIR).resolve() / key
    latest = family / 'latest.json'
    if latest.is_file():
        run = family / json.loads(latest.read_text())['run']
        if all((run / name).is_file() for name in
               ('corpus.txt', 'locations.json', 'corpus-request.settings.json', 'engine-settings.json')):
            return run
    if corpus_root is not None:
        raise FileNotFoundError(f'No complete {key} corpus in {corpus_root}. '
                                'Run fonts.portable.development.prepare_corpora with this output directory.')
    root = VALIDATION_DIR / 'output' / 'opentype'
    for mode in ('portable', 'variable-portable'):
        saved = root / f'dwrite-{version}-{mode}' / 'shapers' / key
        latest = saved / 'latest.json'
        if latest.is_file():
            summary = Path(json.loads(latest.read_text())['summary'])
            return saved / 'runs' / summary.parent.name
    raise FileNotFoundError('Create the corpus with python3 -m fonts.portable.development.prepare_corpora '
                            f'--font {key} or pass a saved corpus directory.')


def shape_corpus(reference_run, sources, hb_shape):
    settings = json.loads((reference_run / 'engine-settings.json').read_text())
    if settings.get('coretextCGJ') != 'preserve' or settings.get('directwriteCGJ') != 'preserve':
        raise ValueError('The reference run must use the shared preserve-CGJ settings')
    corpus = reference_run / 'corpus.txt'
    lines = corpus.read_text(encoding='utf-8').splitlines()
    available = subprocess.check_output([hb_shape, '--list-shapers'], text=True).split()
    engines = ['ot'] + (['coretext'] if 'coretext' in available else [])
    results = []
    for engine in engines:
        for weight in WEIGHTS:
            for level in settings['clusterLevels']:
                args = [f'--text-file={corpus}', f'--shapers={engine}', f'--cluster-level={level}',
                        '--output-format=json', '--no-glyph-names', f'--font-size={settings["size"]}',
                        f'--direction={settings["direction"]}', f'--script={settings["script"]}',
                        f'--language={settings["language"]}']
                paths = (sources['source'] if weight == 400 else sources['cubic'],
                         sources['flat'], sources['final'])
                different = [0, 0]
                examples = [[], []]
                count = 0
                with ExitStack() as stack:
                    processes = []
                    for path in paths:
                        variations = [] if weight == 400 and path == sources['source'] else [f'--variations=wght={weight}']
                        proc = stack.enter_context(subprocess.Popen([hb_shape, path, *args, *variations],
                                                                   stdout=subprocess.PIPE))
                        processes.append(proc)
                    for count, records in enumerate(zip(*(p.stdout for p in processes), strict=True), 1):
                        for i in (0, 1):
                            if records[i] != records[2]:
                                different[i] += 1
                                if len(examples[i]) < 5:
                                    examples[i].append(count)
                    if any(p.wait() for p in processes) or count != len(lines):
                        raise RuntimeError(f'Incomplete {engine} shaping trace')
                results.append(dict(engine=engine, weight=weight, clusterLevel=level,
                                    differentFromCubic=different[0], differentFromFlat=different[1],
                                    firstCubicDifferences=examples[0], firstFlatDifferences=examples[1]))
    return dict(referenceRun=str(reference_run), corpusSha256=file_hash(corpus), lines=len(lines),
                cgjCount=sum(t.count('\u034f') for t in lines), comparisons=results)
