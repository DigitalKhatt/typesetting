"""Record the inputs and settings needed to rebuild an OpenType release."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile

from .build_portable import FONT_STEMS
from .font_checks import file_hash
from .mark_spacing import RULES


PROJECT = Path(__file__).resolve().parents[2]
INPUT_FOLDER = 'portable-regular-inputs'
WEIGHT_DEFAULTS = ('x', 'y', 'markScale', 'counterScale', 'quadraticErrorPer1000Em')
EXPORTER_FIELDS = ('repository', 'revision', 'harfBuzzRevision', 'arguments')


def file_record(path):
    return dict(bytes=path.stat().st_size, sha256=file_hash(path))


def member_record(archive, name):
    data = archive.read(name)
    return dict(bytes=len(data), sha256=sha256(data).hexdigest())


def read_inputs(path):
    if path.is_dir():
        provenance = json.loads((path / 'inputs.json').read_text(encoding='utf-8'))
        regular = {key: file_record(path / f'{key}.otf') for key in FONT_STEMS}
    else:
        with ZipFile(path) as archive:
            provenance = json.loads(archive.read(f'{INPUT_FOLDER}/inputs.json'))
            regular = {key: member_record(archive, f'{INPUT_FOLDER}/{key}.otf') for key in FONT_STEMS}
    return provenance, regular


def build_settings(report, pinned):
    weight = report['weight']
    defaults = {}
    for name in WEIGHT_DEFAULTS:
        value = weight[name]
        defaults[name] = int(value) if isinstance(value, float) and value.is_integer() else value
    settings = dict(sourceDateEpoch=report['sourceDateEpoch'], fonttools=report['fontTools'],
                    uharfbuzz=report.get('uharfbuzz', pinned.get('uharfbuzz')),
                    brotli=report.get('brotli', pinned.get('brotli')),
                    defaults=defaults)
    if weight['scalesInPriorityOrder'] != [['endofaya*', 0]]:
        settings['scalesInPriorityOrder'] = weight['scalesInPriorityOrder']
    for name in ('fixedAdvances', 'overrides'):
        if weight.get(name):
            settings[name] = weight[name]
    return settings


def family_manifest(key, stem, dist, source, regular, report, rules):
    input_path = f'{INPUT_FOLDER}/{key}.otf'
    regular = dict(path=input_path, **regular)
    if source.get('regularInput', regular) != regular or report['sourceSha256'] != regular['sha256']:
        raise ValueError(f'{key}: Regular OTF differs from its provenance or build report')
    if report['family'] != key:
        raise ValueError(f'{key}: build report belongs to {report["family"]}')

    spacing_rules = None
    if expected := report['spacing']['rulesSha256']:
        rule_file = rules / f'{key}.json'
        if file_hash(rule_file) != expected:
            raise ValueError(f'{rule_file} differs from the build report')
        rule_path = rule_file.resolve()
        if rule_path.is_relative_to(PROJECT):
            rule_path = rule_path.relative_to(PROJECT)
        spacing_rules = dict(path=rule_path.as_posix(), sha256=expected)

    for extension in ('ttf', 'woff2'):
        filename = f'{stem}.{extension}'
        if report['artifacts'].get(filename) != file_record(dist / filename):
            raise ValueError(f'{dist / filename} differs from the build report')
    return dict(sourceRepository=source['sourceRepository'], sourceRevision=source['sourceRevision'],
                regularInput=dict(path=input_path, sha256=regular['sha256']), spacingRules=spacing_rules)


def make_manifest(dist, inputs, rules, tag=None):
    provenance, regular = read_inputs(inputs)
    # Older standalone reports recorded only FontTools. Their release checkout pins the other tools.
    pinned = {}
    for line in (PROJECT / 'requirements.txt').read_text(encoding='utf-8').splitlines():
        name, separator, value = line.partition('==')
        name = name.strip().lower()
        if separator and name in ('uharfbuzz', 'brotli'):
            pinned[name] = value.strip()
    families = {}
    settings = {}
    for key, stem in FONT_STEMS.items():
        report = json.loads((dist / f'{key}-build.json').read_text(encoding='utf-8'))
        families[key] = family_manifest(key, stem, dist, provenance['families'][key], regular[key], report, rules)
        settings[key] = build_settings(report, pinned)

    build = next(iter(settings.values()))
    if any(item != build for item in settings.values()):
        build = dict(families=settings)
    exporter = provenance['exporter']
    native_exporter = {key: exporter[key] for key in EXPORTER_FIELDS if key in exporter}
    manifest = dict(tag=tag or provenance['tag'], build=build, nativeExporter=native_exporter)
    if inputs.is_file():
        manifest['regularInputArchive'] = inputs.name
    manifest['families'] = families
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dist', type=Path, default=Path('dist'), help='Directory with fonts and *-build.json reports')
    parser.add_argument('--inputs', type=Path, help='Regular input ZIP or its extracted directory')
    parser.add_argument('--spacing-rules', type=Path, default=RULES, help='Directory used for the build’s spacing rules')
    parser.add_argument('--tag', help='Release tag (default: tag in inputs.json)')
    parser.add_argument('--output', type=Path, help='Manifest path (default: dist/<tag>.json)')
    args = parser.parse_args(argv)
    inputs = args.inputs
    if inputs is None:
        archive = args.dist / f'{INPUT_FOLDER}.zip'
        inputs = archive if archive.is_file() else Path(INPUT_FOLDER)
    try:
        manifest = make_manifest(args.dist, inputs, args.spacing_rules, args.tag)
        tag = manifest['tag']
        if args.output is None and (not tag or '/' in tag or '\\' in tag):
            raise ValueError('Supply --output for a tag that is not a single file name')
        output = args.output or args.dist / f'{tag}.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=output.parent) as directory:
            temporary = Path(directory) / output.name
            temporary.write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
            temporary.replace(output)
    except (OSError, ValueError, KeyError, BadZipFile) as error:
        parser.exit(1, f'Unable to generate release manifest: {error}\n')
    print(output)


if __name__ == '__main__':
    main()
