#!/usr/bin/env python3
"""Capture a reading-proof kit on a real target OS, or archive explicitly reviewed images."""
import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
from tempfile import TemporaryDirectory
import time
from uuid import uuid4
from zipfile import ZipFile

from tools.compare_baselines.compare_baselines import run_lock
from tools.font_validation.validate import save

from ..project import FAMILIES
from ...font_checks import file_hash


HERE = Path(__file__).resolve().parent
PLATFORMS = ('windows', 'apple', 'android')


def desktop_platform():
    return {'Darwin': 'apple', 'Windows': 'windows'}.get(platform.system())


def renderer_source(target):
    names = ('ReadingActivity.java', 'ReadingProof.java') if target == 'android' else ('CMakeLists.txt', 'desktop.cpp')
    digest = sha256()
    for name in names:
        digest.update(name.encode() + b'\0' + (HERE/'native_proof'/name).read_text(encoding='utf-8').encode())
    return digest.hexdigest()


def command(*args, timeout=300):
    subprocess.run(list(map(str, args)), stdin=subprocess.DEVNULL, check=True, timeout=timeout)


def capture_desktop(kit, out, build, target):
    if desktop_platform() != target:
        raise RuntimeError(f'Capture {target} on a native {target} host')
    command('cmake', '-S', HERE/'native_proof', '-B', build, '-DCMAKE_BUILD_TYPE=Release')
    command('cmake', '--build', build, '--config', 'Release')
    name = 'portable-reading-proof' + ('.exe' if target == 'windows' else '')
    exe = next((d/name for d in (build, build/'Release') if (d/name).is_file()), None)
    if exe is None:
        raise FileNotFoundError(name)
    command(exe, kit, out)


def capture_android(kit, out, build, serial):
    sdk = Path(os.environ.get('ANDROID_HOME') or os.environ.get('ANDROID_SDK_ROOT') or Path.home()/'Library/Android/sdk')
    android = max((p for p in (sdk/'platforms').glob('android-*/android.jar')
                   if p.parent.name[8:].isdigit() and int(p.parent.name[8:]) >= 31),
                  key=lambda p: int(p.parent.name[8:]), default=None)
    tools = sorted((p for p in (sdk/'build-tools').glob('*') if p.name.replace('.', '').isdigit()),
                   key=lambda p: tuple(map(int, p.name.split('.'))))
    if android is None or not tools:
        raise RuntimeError('Install an Android SDK platform and build tools for API 31 or newer')
    build.mkdir(parents=True, exist_ok=True)
    classes = build/'classes'
    classes.mkdir(exist_ok=True)
    command('javac', '--release', '11', '-cp', android, '-d', classes,
            HERE/'native_proof/ReadingProof.java', HERE/'native_proof/ReadingActivity.java')
    jar = build/'reading-proof.jar'
    command(tools[-1]/('d8.bat' if os.name=='nt' else 'd8'), '--min-api', '31', '--lib', android,
            '--output', jar, *classes.rglob('*.class'))
    adb = [str(sdk/'platform-tools'/('adb.exe' if os.name=='nt' else 'adb'))]
    if serial:
        adb.extend(['-s', serial])
    # A unique package cannot replace an existing user application.
    package = 'org.digitalkhatt.proof.p' + uuid4().hex
    manifest = build/'AndroidManifest.xml'
    manifest.write_text(f'<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="{package}">'
        '<uses-sdk android:minSdkVersion="31" android:targetSdkVersion="35"/>'
        '<application android:label="DigitalKhatt reading proof" android:debuggable="true">'
        '<activity android:name="org.digitalkhatt.proof.ReadingActivity" android:exported="true"/>'
        '</application></manifest>')
    unsigned, signed = build/'unsigned.apk', build/'reading-proof.apk'
    command(tools[-1]/('aapt2.exe' if os.name=='nt' else 'aapt2'), 'link', '-I', android,
            '--manifest', manifest, '-o', unsigned)
    with ZipFile(unsigned,'a') as apk, ZipFile(jar) as dex:
        apk.writestr('classes.dex',dex.read('classes.dex'))
        for name in ('font.ttf','cases.json'):
            apk.write(kit/name, 'assets/'+name)
    keystore = build/'proof.p12'
    if not keystore.exists():
        command('keytool','-genkeypair','-keystore',keystore,'-storepass','android','-keypass','android',
                '-dname','CN=Local reading proof','-alias','proof','-keyalg','RSA','-validity','365')
    command(tools[-1]/('apksigner.bat' if os.name=='nt' else 'apksigner'), 'sign', '--ks', keystore,
            '--ks-pass','pass:android','--out',signed,unsigned)
    shell = [*adb, 'shell', '-T', '-n']
    command(*adb, 'install','--no-streaming',signed,timeout=60)
    try:
        command(*shell,'am','start','-W','-n',package+'/org.digitalkhatt.proof.ReadingActivity',timeout=40)
        read = [*adb,'exec-out','run-as',package,'cat']
        deadline = time.monotonic()+100
        while time.monotonic() < deadline:
            result = subprocess.run([*read,'files/capture.json'],capture_output=True,timeout=8)
            if result.stdout.startswith(b'{'):
                (out/'capture.json').write_bytes(result.stdout)
                break
            error = subprocess.run([*shell,'run-as',package,'cat','files/error.txt'],capture_output=True,timeout=8)
            if error.stdout.startswith(b'ReadingProofError\n'):
                raise RuntimeError(error.stdout.decode(errors='replace'))
            time.sleep(.5)
        else:
            raise TimeoutError('The Android reading app did not finish its capture')
        for row in json.loads((kit/'cases.json').read_text())['cases']:
            name = row['id']+'.png'
            data = subprocess.check_output([*read,'files/'+name],timeout=15)
            if not data.startswith(b'\x89PNG\r\n\x1a\n'):
                raise ValueError(f'Android did not return the expected PNG for {name}')
            (out/name).write_bytes(data)
    finally:
        command(*adb,'uninstall',package,timeout=35)


def capture(kit, root, target, build, serial=None):
    spec = json.loads((kit/'cases.json').read_text())
    family = spec['family']
    if target not in PLATFORMS or family not in FAMILIES or file_hash(kit/'font.ttf') != spec['fontSha256']:
        raise ValueError('Invalid font identity in the proof kit')
    ids = [c['id'] for c in spec['cases']]
    if not ids or len(set(ids)) != len(ids) or any(not name or not all(
            ch.isascii() and (ch.isalnum() or ch in '-_') for ch in name) for name in ids):
        raise ValueError('A proof kit must have unique, portable sample names')
    dest = root/target/family
    dest.mkdir(parents=True, exist_ok=True)
    native_build = build/('android' if target == 'android' else 'desktop')
    native_build.mkdir(parents=True, exist_ok=True)
    with run_lock(dest/'.capture.lock'), TemporaryDirectory(dir=dest) as tmp:
        stage = Path(tmp)
        with run_lock(native_build/'.build.lock'):
            if target == 'android':
                capture_android(kit, stage, native_build, serial)
            else:
                capture_desktop(kit, stage, native_build, target)
        report = json.loads((stage/'capture.json').read_text())
        if (report['platform'] != target or report['kitSha256'] != file_hash(kit/'cases.json')
                or report['fontSha256'] != spec['fontSha256'] or report['samplesSha256'] != spec['samplesSha256']
                or [c['id'] for c in report['cases']] != ids):
            raise ValueError('Native capture differs from the requested proof kit')
        for row in report['cases']:
            row['pngSha256'] = file_hash(stage/(row['id']+'.png'))
        report['family'] = family
        report['rendererSourceSha256'] = renderer_source(target)
        # Publish immutable revisions. A concurrent verifier keeps its exact images.
        revision = dest/'runs'/uuid4().hex
        revision.parent.mkdir(exist_ok=True)
        save(stage/'capture.json', report)
        stage.rename(revision)
        save(dest/'latest.json', {'run': revision.relative_to(dest).as_posix()})
    return revision


def approve(captured, goldens):
    manifest = captured/'capture.json'
    digest = file_hash(manifest)
    report = json.loads(manifest.read_text())
    if report['platform'] not in PLATFORMS or report['family'] not in FAMILIES:
        raise ValueError('Unknown capture platform or family')
    def verify(directory):
        if file_hash(directory/'capture.json') != digest or not report['cases']:
            raise ValueError('Reviewed capture manifest is empty or has changed')
        for row in report['cases']:
            name = row['id'] + '.png'
            if Path(name).name != name or file_hash(directory/name) != row['pngSha256']:
                raise ValueError('Review images have changed since capture')
    verify(captured)
    folder = goldens/report['platform']/report['family']
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder/digest
    with run_lock(folder/'.approval.lock'):
        if not dest.exists():
            with TemporaryDirectory(dir=folder) as tmp:
                copy = Path(tmp)/'images'
                shutil.copytree(captured, copy)
                verify(copy)
                copy.rename(dest)
        else:
            verify(dest)
        save(folder/'approved.json', dict(run=digest, manifestSha256=digest))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest='action', required=True)
    record = actions.add_parser('capture')
    record.add_argument('--kit', type=Path, required=True)
    record.add_argument('--platform', choices=PLATFORMS, required=True)
    record.add_argument('--output', type=Path, required=True)
    record.add_argument('--android-serial')
    accept = actions.add_parser('approve')
    accept.add_argument('--capture', type=Path, required=True, help='Reviewed revision containing capture.json')
    accept.add_argument('--goldens', type=Path, required=True)
    args = parser.parse_args()
    if args.action == 'capture':
        print(capture(args.kit.resolve(), args.output.resolve(), args.platform,
                      args.output.resolve()/'tools', args.android_serial))
    else:
        approve(args.capture.resolve(), args.goldens.resolve())
