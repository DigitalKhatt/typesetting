"""Trace first allocations and final line features using the shared macOS Release build.

Run from the workspace root after building the native Release targets. Only the
instrumented translation units are recompiled. No production library is replaced.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
from pathlib import Path
import json
import gzip
import os
import shlex
import shutil
import subprocess
from tempfile import TemporaryDirectory
from fontTools.ttLib import TTFont

from tools.font_validation.validate import live_settings

from ..project import FAMILIES, FONTS_DIR, GLYPH_USAGE_DIR, VALIDATION_DIR, WORKSPACE

CONFIG = VALIDATION_DIR / 'config'
PROFILES = json.loads((CONFIG / 'profiles.json').read_text())
COMMON = json.loads((CONFIG / 'common.settings.json').read_text())
BUILD = WORKSPACE / PROFILES['buildDir']
HEADER = r'''#include <iostream>
#include <string>
#include <iomanip>
#include <fstream>
#include <cstdlib>
namespace {
struct AuditScope {
    AuditScope(const char* stage, const std::string& name) {
        std::cerr << "AUDIT\tbegin\t" << stage << '\t' << name << '\n';
    }
    ~AuditScope() { std::cerr << "AUDIT\tend\n"; }
};
}
'''


def instrument(text, marker, addition):
    if text.count(marker) != 1:
        raise ValueError('Review instrumentation for ' + marker)
    return text.replace(marker, marker + '\n' + addition)


def trace_source(name, source):
    if name == 'OtLayout.cpp':
        source = instrument(source, 'bool OtLayout::parseCppLookup(const std::string& lookupName) {',
                            'AuditScope trace("factory", lookupName);')
        source = instrument(source, 'void OtLayout::generateSubstEquivGlyphsLegacy() {\n  for (auto* lookup : lookups) {',
                            'AuditScope trace("equivalents", lookup->name);')
        source = instrument(source, 'glyphNamePerCode[newGlyphCode] = name;',
                            r'''std::cerr << "AUDIT\tcreate\t" << name << '\t' << sourceGlyph->name << '\t';
for (size_t i=0;i<parameters.size();++i) std::cerr << (i ? "," : "") << std::setprecision(17) << parameters.value(i);
std::cerr << '\n';''')
    elif name == 'oldmadina.cpp':
        source = instrument(source, 'auto generateLookup = [&](Lookup* lookup) {',
                            'AuditScope trace("old-equivalents", lookup->name);')
    elif name == 'feaast.cpp':
        source = instrument(source, 'lookup->name = lookupDefinition.getName();',
                            'AuditScope trace("feature-load", lookup->name);')
    elif name == 'JustificationShaping.cpp':
        source = instrument(source, 'auto buffer = shape(lineTextInfo.lineText, font, features, &layout, parameters, true, justResult.operation);',
                            r'''static std::ofstream usage(std::getenv("DK_USAGE_FEATURES"));
for (auto c : lineTextInfo.lineText) usage << std::setfill('0') << std::setw(4) << std::hex << unsigned(c);
usage << std::dec << '\t';
for (size_t i=0;i<features.size();++i) {char s[128]; hb_feature_to_string(&features[i],s,sizeof(s)); usage << (i ? "," : "") << s;}
usage << '\t' << parameters.size() << '\n';''')
        source = instrument(source, 'glyphLayout.parameters = layout.glyphParameters(glyph_info[i], tatweels.left, tatweels.right);',
                            r'''static std::ofstream used(std::getenv("DK_USAGE_NATIVE"));
used << layout.glyphName(font,glyph_info[i].codepoint) << '\t';
for (size_t axis=0;axis<glyphLayout.parameters.size();++axis) used << (axis ? "," : "") << std::setprecision(17) << glyphLayout.parameters.value(axis);
used << '\n';''')
    elif name == 'generate_mushaf.cpp':
        source = instrument(source, '#include "MPFont.h"', '#include "digitalkhatt/project/FontProfile.h"')
        source = instrument(source, 'MPFont font; initializeFont(font,project,options.resources);',
                            'digitalkhatt::project::ScopedFontProfile profile(font, digitalkhatt::project::FontProfile::Justification);')
    else:
        source = '#include "Layout/Lookup.h"\n' + source
        source = instrument(source, 'OtLayout layout(&font, extended, extended ? true : variable);',
                            r'''for (auto& [id,name] : layout.glyphNamePerCode) std::cerr << "AUDIT\tinitial\t" << name << '\n';''')
        source = instrument(source, r'''std::cout << "Generated " << outputFile << '\n';''',
                            r'''for (auto& [id,name] : layout.glyphNamePerCode) std::cerr << "AUDIT\tgid\t" << id << '\t' << name << '\n';
for (size_t i=0;i<layout.gsublookups.size();++i) std::cerr << "AUDIT\tGSUB\t" << i << '\t' << layout.gsublookups[i]->name << '\n';
for (size_t i=0;i<layout.gposlookups.size();++i) std::cerr << "AUDIT\tGPOS\t" << i << '\t' << layout.gposlookups[i]->name << '\n';''')
    return HEADER + source


def capture(key, exe, work, fonts, output):
    directory = output / key
    directory.mkdir(exist_ok=True)
    project = WORKSPACE / (key + 'font')
    shadow = work / project.name
    shadow.mkdir()
    for item in project.iterdir():
        if item.name.startswith('.') or item.name == 'output':
            continue
        dest = shadow / item.name
        if item.name == key + '.mp':
            shutil.copyfile(item, dest)
        elif item.name == 'liboldmadina.dylib' and key == 'oldmadina':
            dest.symlink_to(work / item.name)
        else:
            dest.symlink_to(item)
    font_path = shadow / 'trace.otf'
    args = [exe, '--resources', WORKSPACE / COMMON['resources'], '--profile', 'portable', '--features', COMMON['features']]
    for value in COMMON['disabledLookups']:
        args += ['--disable-lookup', value]
    process = subprocess.run([*args, '-o', font_path, shadow / (key + '.mp')], capture_output=True, text=True,
                             env={**os.environ, 'DYLD_LIBRARY_PATH': str(work)})
    if process.returncode:
        raise RuntimeError(process.stderr[-2000:])
    (directory / 'native-events.tsv.gz').write_bytes(gzip.compress(process.stderr.encode(), mtime=0))
    regular = fonts / key / 'intermediates/portable-regular.otf'
    with TTFont(regular) as original, TTFont(font_path) as traced:
        if original.keys() != traced.keys() or any(original.reader[t] != traced.reader[t] for t in original.reader.keys() if t != 'head'):
            raise ValueError(f'{key}: traced native export differs from build input')
        def stable_head(f):
            head = f.reader['head']
            return head[:8] + head[12:20] + head[36:]
        if stable_head(original) != stable_head(traced):
            raise ValueError(f'{key}: traced head differs beyond timestamps and checksum')
        order = original.getGlyphOrder()
        lookups = {tag: [None] * len(original[tag].table.LookupList.Lookup) for tag in ('GSUB', 'GPOS')}
    created, names, stack, initial = {}, {}, [], set()
    for line in process.stderr.splitlines():
        if not line.startswith('AUDIT\t'):
            continue
        _, op, *values = line.split('\t')
        if op == 'begin':
            stack.append(values)
        elif op == 'end':
            stack.pop()
        elif op == 'create':
            name, base, coordinates = values
            if name in created:
                raise ValueError(name)
            created[name] = dict(source=base, coordinates=[float(x) for x in coordinates.split(',') if x], scopes=stack.copy())
        elif op == 'initial':
            initial.add(values[0])
        elif op == 'gid':
            names[int(values[0])] = values[1]
        elif op in lookups:
            lookups[op][int(values[0])] = values[1]
    if stack or sorted(names) != list(range(len(order))) or any(None in row for row in lookups.values()):
        raise ValueError(f'{key}: stack={stack} names={len(names)}/{len(order)}, '
                         f'missing IDs={sorted(set(range(len(order)))-set(names))[:12]}, '
                         f'lookups missing={[(tag, row.count(None)) for tag, row in lookups.items()]}')
    if set(names.values()) != initial | set(created) or any(not row['scopes'] for row in created.values()):
        raise ValueError(f'{key}: unattributed native allocations')
    glyphs = {order[gid]: created[name] for gid, name in sorted(names.items()) if name in created}
    report = dict(lookups=lookups, created=glyphs, matchedNativeTables=True,
                  sourceSha256=sha256(regular.read_bytes()).hexdigest())
    (directory / 'native-trace.json').write_text(json.dumps(report, indent=2) + '\n')
    print(key, len(glyphs), 'traced new instances', flush=True)


def capture_layout(key, exe, work, output):
    directory = output / key
    directory.mkdir(exist_ok=True)
    settings = live_settings(PROFILES['fonts'], key, 'justified')
    project = WORKSPACE / (key + 'font')
    settings.update(font=str(project / (key + '.mp')), firstPage=1, lastPage=0)
    for field in ('features', 'database', 'resources', 'pdfResources'):
        settings[field] = str((project if field == 'features' else WORKSPACE) / settings[field])
    config = directory / 'layout-settings.json'
    config.write_text(json.dumps(settings, indent=2) + '\n')
    feats, glyphs = work / (key + '.features'), work / (key + '.used')
    env = {**os.environ, 'DYLD_LIBRARY_PATH': str(work), 'DK_USAGE_FEATURES': str(feats), 'DK_USAGE_NATIVE': str(glyphs)}
    out = work / (key + '.pdf')
    with (work / (key + '.log')).open('w') as log:
        result = subprocess.run([exe, '--config', config, '--no-report', '--no-pdf', '--no-notice', '-o', out], cwd=WORKSPACE,
                                stdout=log, stderr=subprocess.STDOUT, env=env)
    if result.returncode:
        raise RuntimeError((work / (key + '.log')).read_text()[-2500:])
    manifest = json.loads(out.with_suffix('.run.json').read_text())
    if not manifest['complete']:
        raise ValueError('Partial native layout')
    (directory / 'layout.run.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (directory / 'layout-features.tsv.gz').write_bytes(gzip.compress(feats.read_bytes(), mtime=0))
    with glyphs.open() as stream:
        counts = Counter(stream)
    (directory / 'live-glyphs.tsv').write_text('name\tcoordinates\toccurrences\n' +
        ''.join(f'{line.rstrip(chr(10))}\t{n}\n' for line, n in sorted(counts.items())))
    print(key, len(counts), 'distinct live name/parameter tuples', flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--font', choices=FAMILIES, action='append', help='Capture this family (repeatable, defaults to all three)')
    parser.add_argument('--fonts', type=Path, default=FONTS_DIR, help='Checked builds containing native Regular intermediates')
    parser.add_argument('--output', type=Path, default=GLYPH_USAGE_DIR, help='Native capture directory')
    parser.add_argument('--layout-only', action='store_true',
                        help='Capture the current justified text and feature ranges without a Portable reference font')
    args = parser.parse_args()
    families = list(dict.fromkeys(args.font or FAMILIES))
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    items = json.loads((BUILD / 'compile_commands.json').read_text())
    targets = {'JustificationShaping.cpp': 'digitalkhatt_justify'}
    if args.layout_only:
        targets['generate_mushaf.cpp'] = 'digitalkhatt_generate_mushaf'
        binaries = ['digitalkhatt_otlayout', 'digitalkhatt_generate_mushaf']
    else:
        targets.update({'OtLayout.cpp': 'digitalkhatt_otlayout', 'feaast.cpp': 'digitalkhatt_otlayout',
                        'generate_opentype.cpp': 'digitalkhatt_generate_opentype', 'oldmadina.cpp': 'oldmadina_font'})
        binaries = ['digitalkhatt_otlayout', 'oldmadina_font', 'digitalkhatt_generate_opentype', 'digitalkhatt_generate_mushaf']
    with TemporaryDirectory(dir=args.output) as temp:
        work = Path(temp)
        replacements = {}

        def compile_one(item):
            original = Path(item['file'])
            name = original.name
            args = shlex.split(item['command'])
            copy = work / name
            copy.write_text(trace_source(name, original.read_text()))
            old_obj = args[args.index('-o') + 1]
            obj = work / (name + '.o')
            args[args.index('-o') + 1] = str(obj)
            args[args.index('-c') + 1] = str(copy)
            subprocess.run([*args, '-iquote', str(original.parent), '-w'], cwd=BUILD, check=True)
            return old_obj, str(obj)

        selected = [i for i in items if Path(i['file']).name in targets
                    and targets[Path(i['file']).name] + '.dir/Release/' in i['command']]
        if len(selected) != len(targets):
            raise ValueError('Need the native Release compile commands')
        with ThreadPoolExecutor(4) as pool:
            replacements.update(pool.map(compile_one, selected))
        original_archive = 'visualmetafont/lib/digitalkhatt/Release/libdigitalkhatt_justify.a'
        archive = work / Path(original_archive).name
        shutil.copyfile(BUILD / original_archive, archive)
        subprocess.run(['ar', 'r', archive, work / 'JustificationShaping.cpp.o'], check=True)
        subprocess.run(['ranlib', archive], check=True)
        replacements[original_archive] = str(archive)
        for target in binaries:
            lines = subprocess.check_output(['ninja', '-C', str(BUILD), '-f', 'build-Release.ninja',
                                             '-t', 'commands', target], text=True).splitlines()
            link_command = shlex.split(lines[-1].removeprefix(': && ').removesuffix(' && :'))
            link_command = [replacements.get(arg, arg) for arg in link_command]
            out = link_command.index('-o') + 1
            original_out = link_command[out]
            binary = work / Path(original_out).name
            link_command[out] = str(binary)
            subprocess.run(link_command, cwd=BUILD, check=True)
            replacements[original_out] = str(binary)
        if not args.layout_only:
            for key in families:
                capture(key, work / 'digitalkhatt_generate_opentype', work, args.fonts, args.output)
        with ThreadPoolExecutor(3) as pool:
            list(pool.map(lambda key: capture_layout(key, work / 'digitalkhatt_generate_mushaf', work, args.output), families))


if __name__ == '__main__':
    main()
