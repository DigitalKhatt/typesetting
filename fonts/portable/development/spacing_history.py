"""Replay the last recorded edits to the three portable spacing rule files."""
import hashlib
import json
from zipfile import ZipFile

from fontTools.misc.roundTools import otRound
from .spacing_curves import curve, interpolate, key_of, make_rule, match_rule, rule_index, serialize


# These are authoring checkpoints and accepted edits from the original fitting run.
CHECKPOINTS = {
    'madina': ('madina/unjustified/run5/rules.json',
               'd4e15452ee559ec858cce15c7c4b2f24f1005bc22f98cd3bcc5c0909f72c9e97'),
    'oldmadina': ('directwrite/old-authoring-original.json',
                  '8412db7ae0c8898611d8bd5cc570212855b12bcfde51642416be52e2a2b1fc82'),
    'indopak': ('indopak/unjustified/run2/rules.json',
                '5b19d023776556689f91333b819b170645b937ed0d91d4db7493ed60f634588f'),
}
OLD_MOVES = ('directwrite/old-trial/moves.json',
             'd6c64e0930d7e32740ffd69534b458550e1139bd9dcce89ecfb72d74b76f6d8b')
PUBLISHED = {
    'madina': '610e10939c2b963e9492345c72ea327fe6196cf1431c82b0a9251208bd4f7f41',
    'oldmadina': '707c93acaaca105e5bdd4913ef1587bbbb78b33a2f34adaa268fb84ef17e2625',
    'indopak': '2ff8158a4bca22e5f0965afd6ab303331f86973c87b2a5e7d33f63514b5c8744',
}


def checkpoint(archive, entry):
    name, expected = entry
    if archive.is_dir():
        data = (archive / name).read_bytes()
    else:
        with ZipFile(archive) as bundle:
            data = bundle.read(name)
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError(f'Historical checkpoint differs from the original run: {name} in {archive}')
    return json.loads(data)


def replay(family, archive):
    rules = {key_of(row): row for row in checkpoint(archive, CHECKPOINTS[family])}
    if family == 'madina':
        # CoreText on shadda + damma; DirectWrite on the qaf stop sign at 450/450.5.
        for rule in [
            [['shadda', 'damma', 'meemiqlab'], 2, 'mark', [0, 0], [-2, -2]],
            [['maddahabove', 'waqf.qaf'], 1, 'mark', [0, 0], [0, 8], [[450, [0, 3]]]],
            # Restore clearance under yāʾ after restoring the Bold wāw tail.
            [['waw.fina', 'kasra', 'behshape.init.beforereh', 'twodotsdown'], 3, 'mark', [0, 0], [0, 6]],
        ]:
            rules[key_of(rule)] = rule
    if family == 'oldmadina':
        parents = rule_index(rules.values())
        edits = {}
        for (sequence, at), center, shift, _gap in checkpoint(archive, OLD_MOVES):
            key = tuple(sequence), at, 'mark'
            parent = match_rule(parents, *key)
            original = curve(parent)
            nodes = edits.setdefault(key, original.copy())
            low, high = center - 5, center + 6
            for w in {low, center, center + 1, high} | {w for w in original if low < w < high}:
                strength = min(1, (w - low) / 5, (high - w) / 5)
                nodes[w] = tuple(otRound(v + strength * d) for v, d in zip(interpolate(original, w), shift))
        rules.update((key, make_rule(key, nodes)) for key, nodes in edits.items())
    result = serialize(rules.values())
    if hashlib.sha256(result).hexdigest() != PUBLISHED[family]:
        raise ValueError(f'{family}: replay differs from the published authoring file')
    return result
