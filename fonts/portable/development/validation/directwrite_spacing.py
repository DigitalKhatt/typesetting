"""Run the shared Windows shaper on an explicit instance of the installed TTF."""
import json
import os
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from ..project import WORKSPACE
from ...font_checks import file_hash


class DirectWriteProbe:
    def __init__(self, root, version, folder):
        self.root = root.resolve()
        self.version = version
        installation = self.root if version == '2.1.0' else self.root / 'versions' / version
        setup_file = installation / 'setup.json'
        if not setup_file.is_file():
            raise FileNotFoundError(f'Run tools/font_validation/validate.py setup-directwrite --dwrite-version {version}')
        setup = json.loads(setup_file.read_text())
        dll = installation / 'bin/DWriteCore.dll'
        package = f'microsoft.windowsappsdk.dwrite.{version}.nupkg'
        if (setup.get('directWriteVersion', version) != version or
                package not in {p['name'] for p in setup['packages']} or
                file_hash(dll) != setup['microsoftDWriteCoreSha256']):
            raise ValueError(f'DWriteCore {version} differs from the shared setup manifest')
        compiler_package = next(p['directory'] for p in setup['packages'] if p['name'].startswith('llvm-mingw-'))
        compiler = self.root / 'runtime' / compiler_package / 'bin/x86_64-w64-mingw32-clang++'
        source = WORKSPACE / 'tools/directwrite/shape.cpp'
        folder.mkdir()
        self.probe = (folder / 'shape.exe').resolve()
        # Keep the PE timestamp deterministic so identical probes can reuse measured weights.
        subprocess.run([compiler, '-std=c++17', '-O2', '-static', '-Wl,--no-insert-timestamp',
                        source, '-o', self.probe, '-luuid'], check=True)
        shutil.copyfile(dll, self.probe.with_name(dll.name))
        self.wine = self.root / 'runtime/Wine Stable.app/Contents/Resources/wine/bin/wine'
        self.env = dict(os.environ, WINEPREFIX=str(self.root/'wineprefix'), WINEDEBUG='-all',
                        MVK_CONFIG_LOG_LEVEL='0', WINEDLLOVERRIDES='mscoree,mshtml=')
        self.provenance = dict(version=version, dllSha256=file_hash(dll), probeSha256=file_hash(self.probe),
                               sourceSha256=file_hash(source), wine=subprocess.check_output(
                                   [self.wine, '--version'], env=self.env, text=True).strip(),
                               geometry='GetGlyphRunOutline on the selected IDWriteFontFace5')

    def shape(self, font, corpus, tajweed, weight, records, outlines, log):
        # DirectWrite's Wine font loader needs a short C: path. Match the
        # shared shaper's staging and preserve the exact published font bytes.
        with TemporaryDirectory(prefix='portable-', dir=self.root/'wineprefix/drive_c') as tmp:
            stage = Path(tmp)
            windows = 'C:\\' + stage.name + '\\'
            shutil.copyfile(font, stage/'font.ttf')
            shutil.copyfile(corpus, stage/'corpus.txt')
            if file_hash(stage/'font.ttf') != file_hash(font) or file_hash(stage/'corpus.txt') != file_hash(corpus):
                raise ValueError('DirectWrite staging changed the font or the corpus')
            command = [self.wine, self.probe, windows+'font.ttf', windows+'corpus.txt', windows+'positions.txt',
                       f'--weight={weight}', f'--features=tjwd={int(tajweed)}', '--contact-outlines='+windows+'ink.txt']
            with log.open('wb') as output:
                proc = subprocess.run(command, cwd=self.probe.parent, env=self.env,
                                      stdout=output, stderr=subprocess.STDOUT)
            if proc.returncode:
                raise RuntimeError(f'DWriteCore {self.version} failed at {weight}: {log.read_text(errors="replace")[-1600:]}')
            shutil.copyfile(stage/'positions.txt', records)
            shutil.copyfile(stage/'ink.txt', outlines)
