"""Rebuild a release from its manifest in a standalone Typesetting checkout."""

import argparse
from hashlib import sha256
import json
from pathlib import Path
from shutil import copyfile, copyfileobj
from subprocess import CalledProcessError
from tempfile import TemporaryDirectory
from urllib.parse import quote
from urllib.request import urlopen
from zipfile import BadZipFile, ZipFile

from .build_portable import FAMILIES, build_family, build_lock, installed_version
from .font_checks import file_hash
from .source_export import export_regular


PROJECT = Path(__file__).resolve().parents[2]
RELEASE_DOWNLOADS = 'https://github.com/DigitalKhatt/typesetting/releases/download'


def filename(value):
    if not isinstance(value, str) or not value or value in ('.', '..') or '/' in value or '\\' in value:
        raise ValueError(f'Expected a single filename, got {value!r}')
    return value


def download_regular(manifest, tag, output, work):
    archive = output / filename(manifest.get('regularInputArchive', 'portable-regular-inputs.zip'))
    cached = archive.is_file()
    incoming = archive if cached else work / archive.name
    if not cached:
        url = f'{RELEASE_DOWNLOADS}/{quote(tag)}/{quote(archive.name)}'
        print(f'Downloading {url}', flush=True)
        with urlopen(url, timeout=60) as response, incoming.open('wb') as stream:
            copyfileobj(response, stream)

    with ZipFile(incoming) as zipfile:
        for key in FAMILIES:
            record = manifest['families'][key]['regularInput']
            data = zipfile.read(record['path'])
            if sha256(data).hexdigest() != record['sha256']:
                raise ValueError(f'{key}: Regular OTF in {archive.name} differs from the release manifest')
            (work / f'{key}.otf').write_bytes(data)
    if not cached:
        incoming.replace(archive)


def family_options(manifest, key, output, work):
    settings = manifest['build']
    if 'families' in settings:
        settings = settings['families'][key]
    versions = {'fonttools': installed_version('fonttools'), 'uharfbuzz': installed_version('uharfbuzz'),
                'brotli': installed_version('brotlicffi') or installed_version('Brotli')}
    for package, actual in versions.items():
        expected = settings[package]
        if actual != expected:
            raise ValueError(f'{key}: {package} {expected} is required, found {actual or "none"}. '
                             'Install the dependencies from the release checkout’s requirements.txt.')

    spacing = manifest['families'][key]['spacingRules']
    rules_directory = work / 'spacing'
    if spacing:
        rules = PROJECT / spacing['path']
        rules_directory.mkdir(exist_ok=True)
        snapshot = rules_directory / f'{key}.json'
        copyfile(rules, snapshot)
        if file_hash(snapshot) != spacing['sha256']:
            raise ValueError(f'{rules} differs from the release manifest')

    weight = settings['defaults']
    scales = settings.get('scalesInPriorityOrder', [['endofaya*', 0]])
    return argparse.Namespace(
        output=output, keep_intermediates=False, epoch=settings['sourceDateEpoch'],
        x=weight['x'], y=weight['y'], mark_scale=weight['markScale'], counter_scale=weight['counterScale'],
        quadratic_error=weight['quadraticErrorPer1000Em'],
        # The compiler reverses user scales and appends its built-in end-of-ayah rule.
        scale=list(reversed(scales[:-1])), fixed_advances=settings.get('fixedAdvances', False),
        overrides=PROJECT / settings['overrides'] if settings.get('overrides') else None,
        no_spacing_adjustments=spacing is None, spacing_rules=rules_directory,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path, help='Release JSON from the matching Typesetting checkout')
    parser.add_argument('--from-source', action='store_true', help='Fetch and build the pinned native glyph sources')
    parser.add_argument('--output', type=Path, help='Output directory (default: dist or dist/from-source)')
    args = parser.parse_args(argv)
    output = (args.output or Path('dist/from-source' if args.from_source else 'dist')).resolve()
    try:
        manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
        tag = filename(manifest['tag'])
        output.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=output, prefix='.regular-') as directory:
            work = Path(directory)
            options = {key: family_options(manifest, key, output, work) for key in FAMILIES}
            if args.from_source:
                sources = PROJECT / 'output' / 'rebuild' / tag
                sources.mkdir(parents=True, exist_ok=True)
                with build_lock(sources / '.build.lock'):
                    export_regular(manifest, sources, work)
            else:
                download_regular(manifest, tag, output, work)
            for key, settings in options.items():
                build_family(key, work / f'{key}.otf', settings)
    except (OSError, ValueError, KeyError, BadZipFile, CalledProcessError, RuntimeError) as error:
        parser.exit(1, f'Unable to rebuild the release: {error}\n')


if __name__ == '__main__':
    main()
