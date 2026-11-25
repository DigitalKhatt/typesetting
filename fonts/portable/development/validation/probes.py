"""Compile the native probes for corpus, collision, and GPOS traces."""
from pathlib import Path
import shlex
import subprocess

from ..project import WORKSPACE


NATIVE = Path(__file__).with_name('native')


def pkg_flags(*packages):
    return shlex.split(subprocess.check_output(['pkg-config', '--cflags', '--libs', *packages], text=True))


def compile_corpus_trace(target):
    subprocess.run(['c++', '-std=c++20', '-O2', NATIVE / 'trace_corpus_glyphs.cpp',
                    *pkg_flags('harfbuzz'), '-o', target], check=True)


def compile_probes(folder, profiles):
    native = WORKSPACE / 'visualmetafont/lib/digitalkhatt'
    build = WORKSPACE / profiles['buildDir'] / 'visualmetafont/lib/digitalkhatt'
    libs = next((d for d in (build / profiles['configuration'], build)
                 if all((d / f'libdigitalkhatt_{name}.a').is_file() for name in ('geometry', 'layout'))), None)
    if libs is None:
        raise FileNotFoundError(f'Build the digitalkhatt_geometry and digitalkhatt_layout targets under {build}')
    compile_corpus_trace(folder / 'trace')
    subprocess.run(['c++', '-std=c++20', '-O2', '-I' + str(native / 'include'), NATIVE / 'mark_contacts.cpp',
                    libs / 'libdigitalkhatt_layout.a', libs / 'libdigitalkhatt_geometry.a',
                    *pkg_flags('harfbuzz', 'clipper2'), '-o', folder / 'mark_contacts'], check=True)


def compile_release_trace(work, profiles):
    harfbuzz = WORKSPACE / 'visualmetafont/lib/harfbuzz'
    base = WORKSPACE / profiles['buildDir'] / 'visualmetafont/lib/harfbuzz'
    library = next((p for p in (base/profiles['configuration']/'libharfbuzz.a', base/'libharfbuzz.a')
                    if p.is_file()), None)
    if library is None:
        raise FileNotFoundError('Build the native harfbuzz target for detailed GPOS traces')
    target = work / 'release-trace'
    subprocess.run(['c++', '-std=c++20', '-O2', '-I' + str(harfbuzz / 'harfbuzz/src'),
                    NATIVE / 'release_trace.cpp', library, '-o', target], check=True)
    return target
