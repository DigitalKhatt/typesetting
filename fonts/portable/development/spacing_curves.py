"""Convert measured within-line movements into contextual positioning curves."""
from collections import defaultdict
import json

from fontTools.misc.roundTools import otRound

from ..compact_mark import ALTERNATE, SOURCE


def key_of(row):
    sequence, at, kind = row[:3]
    return tuple(sequence), at, kind


def serialize(rows):
    lines = ['  ' + json.dumps(row, ensure_ascii=False) for row in sorted(rows, key=key_of)]
    return ('[\n' + ',\n'.join(lines) + '\n]\n' if lines else '[]\n').encode()


def curve(row):
    if row is None:
        return {400: (0, 0), 700: (0, 0)}
    nodes = {400: tuple(row[3]), 700: tuple(row[4])}
    if len(row) > 5:
        nodes.update((w, tuple(move)) for w, move in row[5])
    return nodes


def interpolate(nodes, weight):
    if weight in nodes:
        return nodes[weight]
    lo = max(w for w in nodes if w < weight)
    hi = min(w for w in nodes if w > weight)
    t = (weight - lo) / (hi - lo)
    return tuple(a + t * (b - a) for a, b in zip(nodes[lo], nodes[hi]))


def make_rule(key, nodes):
    sequence, at, kind = key
    row = [list(sequence), at, kind, list(nodes[400]), list(nodes[700])]
    middle = [[w, list(nodes[w])] for w in sorted(nodes) if 400 < w < 700]
    return row + ([middle] if middle else [])


def simplify(nodes, anchors=()):
    """Keep the rounded coordinate at every observation, including points removed earlier."""
    weights = sorted(nodes)
    required = sorted({0, len(weights) - 1, *(weights.index(w) for w in anchors)})
    result = {weights[i]: nodes[weights[i]] for i in required}
    pending = list(zip(required, required[1:]))
    while pending:
        first, last = pending.pop()
        endpoints = {w: nodes[w] for w in (weights[first], weights[last])}
        largest, split = 0, None
        for i in range(first + 1, last):
            w = weights[i]
            error = max(abs(otRound(a) - b) for a, b in zip(interpolate(endpoints, w), nodes[w]))
            if error > largest:
                largest, split = error, i
        if split is not None:
            w = weights[split]
            result[w] = nodes[w]
            pending.extend([(first, split), (split, last)])
    return result


def rule_index(rows):
    by_target = defaultdict(list)
    for row in sorted(rows, key=lambda r: (-len(r[0]), *key_of(r))):
        sequence, at, kind = key_of(row)
        by_target[sequence[at], kind].append((sequence, at, row))
    return by_target


def match_rule(index, sequence, at, kind):
    for context, target, row in index.get((sequence[at], kind), []):
        start = at - target
        if start >= 0 and sequence[start:start + len(context)] == context:
            return row
    return None


def logical_glyphs(record, names):
    """Map GUI indices to the glyph sequence that exists during RTL GPOS."""
    sequence, indices = [], {}
    for raw_index, glyph in enumerate(reversed(record.split('|'))):
        gid, _, advance, _ = glyph.split(',', 3)
        name = names[int(gid)]
        # HarfBuzz can output a zero-advance space for a default ignorable after GPOS.
        if name == 'space' and float(advance) == 0:
            continue
        indices[raw_index] = len(sequence)
        sequence.append(SOURCE if name == ALTERNATE else name)
    return tuple(sequence), indices


def context_key(sequence, at, left, right, kind, index, padding):
    parent = match_rule(index, sequence, at, kind)
    if parent:
        start = at - parent[1]
        left, right = min(left, start), max(right, start + len(parent[0]) - 1)
    left = max(0, left - padding)
    return sequence[left:right + padding + 1], at - left, kind


def fit_curves(rows, votes, weights):
    rules = {key_of(row): row for row in rows}
    index = rule_index(rows)
    conflicts = 0
    for key, observations in sorted(votes.items()):
        original = curve(match_rule(index, *key))
        # Some authored kerns only set XAdvance. The probe can also propose YPlacement.
        original = {w: (v[0], v[1] if len(v) == 2 else 0) for w, v in original.items()}
        nodes = original.copy()
        for w in weights:
            choices = observations.get(w, {})
            conflicts += int(len(choices) > 1)
            move = min(choices, key=lambda d: (-choices[d], d[0]**2 + d[1]**2, d)) if choices else (0, 0)
            nodes[w] = tuple(otRound(v) + d for v, d in zip(interpolate(original, w), move))
        nodes = simplify(nodes, original)
        if key[2] == 'kern' and all(v[1] == 0 for v in nodes.values()):
            nodes = {w: (v[0],) for w, v in nodes.items()}
        rules[key] = make_rule(key, nodes)
    return list(rules.values()), conflicts
