"""Compile measured local clearance adjustments into standard variable GPOS."""
from collections import defaultdict
from copy import copy
from itertools import product
import json
from pathlib import Path

from fontTools.otlLib.builder import buildCoverage, buildLookup, buildSinglePosSubtable, buildValue
from fontTools.ttLib.tables import otTables as ot
from fontTools.varLib.builder import buildVarDevTable
from fontTools.varLib.varStore import OnlineVarStoreBuilder

RULES = Path(__file__).with_name('data') / 'mark_spacing'
DEVICE_FIELDS = {'XPlacement': 'XPlaDevice', 'YPlacement': 'YPlaDevice', 'XAdvance': 'XAdvDevice'}


def read_rules(family, directory=RULES):
    return json.loads((directory / (family + '.json')).read_text())


def coordinates(rule):
    regular, bold, *intermediate = rule[3:]
    middle = intermediate[0] if intermediate else []
    return tuple(regular), tuple(bold), tuple((weight, tuple(move)) for weight, move in middle)


def positioning_value(store, mode, regular, bold, middle):
    fields = ('XAdvance', 'YPlacement') if mode == 'kern' else ('XPlacement', 'YPlacement')
    if mode == 'kern' and len(regular) == 1:
        fields = ('XAdvance',)
    knots = [(400, regular), *middle, (700, bold)]
    weights = [w for w, _ in knots]
    if weights != sorted(set(weights)) or any(len(move) != len(fields) for _, move in knots):
        raise ValueError('Invalid weight locations or coordinate count in spacing rule')
    locations = [(weight - 400) / 300 for weight in weights]
    # One triangular support per nondefault knot gives piecewise-linear
    # positioning. Each value below is relative to the Regular position.
    supports = [{'wght': (locations[i-1], locations[i], locations[min(i+1, len(knots)-1)])}
                for i in range(1, len(knots))]
    store.setSupports(supports)
    result = {}
    for index, field in enumerate(fields):
        if regular[index]:
            result[field] = regular[index]
        deltas = [move[index] - regular[index] for _, move in knots[1:]]
        if any(deltas):
            result[DEVICE_FIELDS[field]] = buildVarDevTable(store.storeDeltas(deltas))
    return buildValue(result)


def repeat_attachment(lookups, order, parent, child):
    """Reuse the native anchors after moving the parent and before moving the child."""
    for lookup in lookups:
        for wrapper in lookup.SubTable:
            native = getattr(wrapper, 'ExtSubTable', wrapper)
            if (not isinstance(native, ot.MarkMarkPos) or child not in native.Mark1Coverage.glyphs
                    or parent not in native.Mark2Coverage.glyphs):
                continue
            sub = copy(native)
            child_index = native.Mark1Coverage.glyphs.index(child)
            parent_index = native.Mark2Coverage.glyphs.index(parent)
            sub.Mark1Coverage = buildCoverage([child], order)
            sub.Mark1Array = copy(native.Mark1Array)
            sub.Mark1Array.MarkRecord = [native.Mark1Array.MarkRecord[child_index]]
            sub.Mark1Array.MarkCount = 1
            sub.Mark2Coverage = buildCoverage([parent], order)
            sub.Mark2Array = copy(native.Mark2Array)
            sub.Mark2Array.Mark2Record = [native.Mark2Array.Mark2Record[parent_index]]
            sub.Mark2Array.Mark2Count = 1
            return buildLookup([sub], flags=lookup.LookupFlag, table='GPOS', extension=True,
                               markFilterSet=getattr(lookup, 'MarkFilteringSet', None))
    raise ValueError(f'Missing native mark-to-mark attachment for {parent} and {child}')


def position_record(lookup_index):
    record = ot.PosLookupRecord()
    record.SequenceIndex = 0
    record.LookupListIndex = lookup_index
    return record


def glyph_contexts(contexts, order):
    by_target = defaultdict(list)
    for sequence, target, action in contexts:
        rule = ot.ChainPosRule()
        rule.Backtrack = list(reversed(sequence[:target]))
        rule.Input = []
        rule.InputGlyphCount = 1
        rule.LookAhead = list(sequence[target+1:])
        rule.PosLookupRecord = [position_record(action)]
        by_target[sequence[target]].append(rule)
    sub = ot.ChainContextPos()
    sub.Format = 1
    sub.Coverage = buildCoverage(by_target, order)
    sub.ChainPosRuleSet = []
    for glyph in sub.Coverage.glyphs:
        rule_set = ot.ChainPosRuleSet()
        rule_set.ChainPosRule = by_target[glyph]
        sub.ChainPosRuleSet.append(rule_set)
    return sub


def context_subtables(contexts, order, alternates):
    batch = []
    size = 10  # Format 1 header and Coverage header.
    for sequence, target, action in contexts:
        options = [(g, alternates[g]) if g in alternates else (g,) for g in sequence]
        alternate_positions = sum(len(g) > 1 for g in options)
        # Keep the internal offsets of each Format 1 subtable within 16 bits.
        # Charge every rule for a Coverage glyph and a rule-set header.
        cost = (18 + 2 * len(sequence)) * (1 + alternate_positions)
        if batch and (alternate_positions > 1 or size + cost > 0xFFFF):
            yield glyph_contexts(batch, order)
            batch = []
            size = 10
        if alternate_positions > 1:
            # Coverage lists retain OR matching without expanding every
            # combination when a custom context has multiple alternates.
            sub = ot.ChainContextPos()
            sub.Format = 3
            sub.BacktrackCoverage = [buildCoverage(g, order) for g in reversed(options[:target])]
            sub.InputCoverage = [buildCoverage(options[target], order)]
            sub.LookAheadCoverage = [buildCoverage(g, order) for g in options[target+1:]]
            sub.PosLookupRecord = [position_record(action)]
            yield sub
        else:
            batch.extend((variant, target, action) for variant in product(*options))
            size += cost
    if batch:
        yield glyph_contexts(batch, order)


def apply_spacing(font, rules, *, parent_attachment=None, glyph_alternates=None):
    if not rules:
        return
    gdef = font['GDEF'].table
    if getattr(gdef, 'VarStore', None) is not None:
        raise ValueError('The input already has GPOS variation data')
    order = font.getReverseGlyphMap()
    gpos = font['GPOS'].table
    store = OnlineVarStoreBuilder(['wght'])
    lookups = gpos.LookupList.Lookup
    features = {row.FeatureTag: row.Feature for row in gpos.FeatureList.FeatureRecord}
    alternates = glyph_alternates or {}
    sources = {variant: source for source, variant in alternates.items()}

    def is_parent(rule):
        sequence, target, kind = rule[:3]
        return (parent_attachment and kind == 'mark' and sequence[target] == parent_attachment[0]
                and parent_attachment[1] in sequence[target+1:])

    by_glyph = defaultdict(set)
    value_keys = set()
    seen = set()
    for rule in rules:
        sequence, target, mode = rule[:3]
        key = (mode, tuple(sequence), target)
        if key in seen or mode not in ('mark', 'kern') or not 0 <= target < len(sequence):
            raise ValueError(f'Invalid or duplicate clearance rule {key}')
        missing = set(sequence).difference(order)
        if missing:
            raise ValueError(f'Spacing rule refers to missing glyphs {sorted(missing)}')
        seen.add(key)
        glyph = sequence[target]
        value_key = (mode, *coordinates(rule))
        value_keys.add(value_key)
        by_glyph[sources.get(glyph, glyph)].add(value_key)

    values = {key: positioning_value(store, *key) for key in sorted(value_keys)}
    actions = []
    action_indexes = {}
    # One SinglePos lookup can carry a distinct value for each covered glyph.
    # Give each glyph's different curves successive slots in those lookups.
    for glyph, keys in sorted(by_glyph.items()):
        for slot, key in enumerate(sorted(keys)):
            if slot == len(actions):
                actions.append({})
            actions[slot][glyph] = values[key]
            if glyph in alternates:
                actions[slot][alternates[glyph]] = values[key]
            action_indexes[glyph, key] = len(lookups) + slot
    for mapping in actions:
        # Limit each subtable's value and device data to 16-bit offsets.
        entries = list(mapping.items())
        subtables = [buildSinglePosSubtable(dict(entries[i:i+2048]), order)
                     for i in range(0, len(entries), 2048)]
        lookups.append(buildLookup(subtables, table='GPOS', extension=len(subtables) > 1))

    for mode, feature in (('kern', 'kern'), ('parent', 'mkmk'), ('mark', 'mkmk')):
        contexts = []
        for rule in sorted(rules, key=lambda r: (-len(r[0]), r[0], r[1])):
            sequence, target, kind = rule[:3]
            if ('parent' if is_parent(rule) else kind) != mode:
                continue
            glyph = sequence[target]
            key = (kind, *coordinates(rule))
            action = action_indexes[sources.get(glyph, glyph), key]
            contexts.append((sequence, target, action))
        if contexts:
            # Index the rules by their single input glyph. Each glyph gets
            # its longest match, and scanning can continue at the next mark.
            lookup = buildLookup(list(context_subtables(contexts, order, alternates)),
                                 table='GPOS', extension=True)
            feat = features[feature]
            feat.LookupListIndex.append(len(lookups))
            lookups.append(lookup)
            if mode == 'parent':
                feat.LookupListIndex.append(len(lookups))
                lookups.append(repeat_attachment(lookups, order, *parent_attachment))
            feat.LookupCount = len(feat.LookupListIndex)
    gpos.LookupList.LookupCount = len(lookups)
    gdef.VarStore = store.finish()
    gdef.Version = 0x00010003
