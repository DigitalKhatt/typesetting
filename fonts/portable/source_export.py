"""Export Regular OTFs from a release's pinned glyph projects."""

import os
from pathlib import Path
from shutil import which
import subprocess
import sys


HARFBUZZ_REPOSITORY = 'https://github.com/DigitalKhatt/harfbuzz.git'
CMAKE_PROJECT = Path(__file__).with_name('native_export')


def cmake_environment():
    env = os.environ.copy()
    brew = which('brew') if sys.platform == 'darwin' else None
    if brew:
        prefix = Path(subprocess.check_output([brew, '--prefix'], text=True).strip()) / 'opt'
        llvm = prefix / 'llvm/bin'
        if (llvm / 'clang++').is_file():
            env.setdefault('CC', str(llvm / 'clang'))
            env.setdefault('CXX', str(llvm / 'clang++'))
        tool_dirs = [prefix / package / 'bin' for package in ('bison', 'flex')]
        env['PATH'] = os.pathsep.join([*(str(path) for path in tool_dirs if path.is_dir()),
                                      env.get('PATH', os.defpath)])
    return env


def checkout(repository, revision, path):
    path.mkdir(parents=True, exist_ok=True)
    git = ['git', '-C', path]
    if not (path / '.git').is_dir():
        subprocess.run([*git, 'init', '--quiet'], check=True)
    head = subprocess.run([*git, 'rev-parse', '--verify', 'HEAD'], capture_output=True, text=True)
    if head.returncode:
        print(f'Fetching {repository} at {revision}', flush=True)
        subprocess.run([*git, 'fetch', '--quiet', '--depth=1', '--', repository, revision], check=True)
        subprocess.run([*git, 'checkout', '--quiet', '--detach', 'FETCH_HEAD'], check=True)
        head = subprocess.run([*git, 'rev-parse', 'HEAD'], check=True, capture_output=True, text=True)
    if head.stdout.strip() != revision:
        raise ValueError(f'{path} is at {head.stdout.strip()}, expected {revision}')
    changes = subprocess.check_output([*git, 'status', '--porcelain', '--untracked-files=no'], text=True)
    if changes:
        raise ValueError(f'{path} has local changes')


def export_regular(manifest, sources, output):
    native = manifest['nativeExporter']
    visualmetafont = sources / 'visualmetafont'
    checkout(native['repository'], native['revision'], visualmetafont)
    checkout(HARFBUZZ_REPOSITORY, native['harfBuzzRevision'], visualmetafont / 'lib/harfbuzz/harfbuzz')
    projects = {key: sources / (key + 'font') for key in manifest['families']}
    for key, path in projects.items():
        family = manifest['families'][key]
        checkout(family['sourceRepository'], family['sourceRevision'], path)

    build = sources / 'build'
    env = cmake_environment()
    subprocess.run(['cmake', '-S', CMAKE_PROJECT, '-B', build, f'-DDIGITALKHATT_SOURCES={sources}',
                    '-DCMAKE_BUILD_TYPE=Release', '-DFETCHCONTENT_UPDATES_DISCONNECTED=ON'], check=True, env=env)
    subprocess.run(['cmake', '--build', build, '--config', 'Release', '--target',
                    'digitalkhatt_generate_opentype', *projects], check=True, env=env)

    name = 'digitalkhatt_generate_opentype' + ('.exe' if os.name == 'nt' else '')
    exporter = build / 'bin' / name
    resources = visualmetafont / 'src/Resources/metafont'
    for key, path in projects.items():
        log_path = build / f'{key}-export.log'
        print(f'{key}: exporting Regular from {path}', flush=True)
        with log_path.open('w', encoding='utf-8') as log:
            result = subprocess.run([exporter, *native['arguments'], '--resources', resources,
                                     '-o', output / f'{key}.otf', path / f'{key}.mp'],
                                    stdout=log, stderr=subprocess.STDOUT)
        if result.returncode:
            raise ValueError(f'{key} Regular export failed. See {log_path}')
