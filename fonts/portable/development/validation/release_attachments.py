"""Check the authored attachment graph, join ink and contextual word advances."""
from collections import Counter, defaultdict
from functools import lru_cache
from hashlib import sha256
import json
import subprocess

from fontTools.misc.roundTools import otRound
from fontTools.ttLib import TTFont
from fontTools.varLib.varStore import VarStoreInstancer

from tools.font_validation.validate import save

from ... import compact_mark
from ...mark_spacing import RULES
from ..spacing_curves import curve, interpolate, match_rule, rule_index
from .check_mark_spacing import prepare
from ...font_checks import file_hash
from .portable_checks import cursive_subtables
from .probes import compile_release_trace
from .release_weights import coverage_plan


def trace_file(probe, font, weight, tajweed, corpus, target):
    with corpus.open('rb') as source, target.open('wb') as out:
        subprocess.run([probe, font, str(weight), str(int(tajweed))], stdin=source, stdout=out, check=True)


def decode(line):
    return [tuple(map(int, cell.split(','))) for cell in line.rstrip().split('|') if cell]


class Ink:
    def __init__(self, font, weight):
        import pathops
        self.ops = pathops
        self.glyphs = font.getGlyphSet(location={'wght': weight} if 'fvar' in font else None)
        self.names = font.getGlyphOrder()
        self.paths = {}
        self.joins = {}

    def path(self, gid):
        if gid not in self.paths:
            p = self.ops.Path()
            self.glyphs[self.names[gid]].draw(p.getPen(self.glyphs))
            self.paths[gid] = p
        return self.paths[gid]

    def meets(self, a, b, dx, dy):
        key = a, b, dx, dy
        if key in self.joins:
            return self.joins[key]
        op = self.ops
        left, right = self.path(a), self.path(b).transform(translateX=dx, translateY=dy)
        # Expand one outline by half a font unit. This accepts touching caps
        # after TrueType grid rounding, including a shared edge with zero area.
        hit = bool(left.area and right.area and op.op(left, right, op.PathOp.INTERSECTION).area)
        if not hit and left.area and right.area:
            stroke = op.Path(left)
            stroke.stroke(1, op.LineCap.ROUND_CAP, op.LineJoin.ROUND_JOIN, 4)
            stroke.convertConicsToQuads(.02)
            hit = bool(op.op(stroke, right, op.PathOp.INTERSECTION).area)
        self.joins[key] = hit
        return hit


def cursive_records(font, weight):
    store = getattr(font['GDEF'].table, 'VarStore', None)
    inst = (VarStoreInstancer(store, font['fvar'].axes, font.normalizeLocation({'wght': weight}))
            if store is not None else None)
    def point(anchor):
        if anchor is None:
            return None
        values = [anchor.XCoordinate, anchor.YCoordinate]
        for i, name in enumerate(('XDeviceTable', 'YDeviceTable')):
            device = getattr(anchor, name, None)
            if device is not None:
                if device.DeltaFormat != 0x8000 or inst is None:
                    raise ValueError('Unsupported device-specific cursive anchor')
                values[i] += inst[(device.StartSize << 16) + device.EndSize]
        return tuple(values)
    tables = defaultdict(list)
    ids = font.getReverseGlyphMap()
    for index, sub in cursive_subtables(font):
        tables[index].append({ids[n]: (point(r.EntryAnchor), point(r.ExitAnchor))
                              for n, r in zip(sub.Coverage.glyphs, sub.EntryExitRecord, strict=True)})
    return tables


def without_context_kern(cubic, native, output):
    native_count = len(native['GPOS'].table.LookupList.Lookup)
    with TTFont(cubic) as reference:
        for row in reference['GPOS'].table.FeatureList.FeatureRecord:
            if row.FeatureTag == 'kern':
                f = row.Feature
                f.LookupListIndex = [i for i in f.LookupListIndex if i < native_count]
                f.LookupCount = len(f.LookupListIndex)
        reference.save(output)


def check_weight(native, font, source_trace, target_trace, unkerned, places, w, rules, original_ink):
    names, source_names = font.getGlyphOrder(), native.getGlyphOrder()
    ink, anchors = Ink(font, w), cursive_records(font, w)
    counts, errors, examples = Counter(), Counter(), []
    mark_class = native['GDEF'].table.GlyphClassDef.classDefs
    outgoing = {i for i, name in enumerate(source_names) if {'init','medi'}.intersection(name.split('.'))}
    incoming = {i for i, name in enumerate(source_names) if {'medi','fina'}.intersection(name.split('.'))}
    def finding(kind, loc, index, **details):
        errors[kind] += 1
        if errors[kind] <= 8:
            examples.append(dict(kind=kind, page=loc['page'], line=loc['line'], index=index, **details))

    @lru_cache(maxsize=None)
    def join_pair(lookup, a, b):
        for table in anchors[lookup]:
            exit_anchor = table.get(a, (None, None))[1]
            entry_anchor = table.get(b, (None, None))[0]
            if exit_anchor is not None and entry_anchor is not None:
                return exit_anchor, entry_anchor
        return None

    kern = rule_index([r for r in rules if r[2] == 'kern'])
    moves = {id(r): otRound(interpolate(curve(r), w)[0]) for r in rules if r[2] == 'kern'}
    switches = compact_mark.ALTERNATE in names
    active = otRound(font.normalizeLocation({'wght': w})['wght'] * 16384) >= 1
    def stroke_was_joined(before, i, j):
        a, b = before[i], before[j]
        return original_ink.meets(a[0], b[0], b[3]-a[3], b[4]-a[4])
    with source_trace.open() as src, target_trace.open() as dst, unkerned.open() as base:
        for loc, raw, shaped, plain in zip(places, src, dst, base, strict=True):
            before, after, baseline = decode(raw), decode(shaped), decode(plain)
            counts['lines'] += 1
            if len(before) != len(after) or len(baseline) != len(after):
                finding('glyph-count', loc, 0)
                continue
            form = [names[r[0]] for r in after]
            canonical = [compact_mark.SOURCE if n == compact_mark.ALTERNATE else n for n in form]
            different = next((i for i, (s,t,u) in enumerate(zip(before, after, baseline))
                              if source_names[s[0]] != canonical[i] or t[1] != s[1] or u[:2] != t[:2]), None)
            if different is not None:
                finding('glyph-or-cluster', loc, different, glyph=form[different])
                continue
            sequence = tuple(n for n, r in zip(canonical, after) if not (n == 'space' and r[2] == 0))
            at, actual_width, expected_width, word_start = 0, 0, 0, 0
            previous_base = None
            siblings = defaultdict(list)
            for i, (s, t, u) in enumerate(zip(before, after, baseline)):
                gid, cluster, adv, x, y, parent, target, lookup = t
                if switches and canonical[i] == compact_mark.SOURCE:
                    context = at >= 2 and sequence[at-2:at] == ('hah.fina', 'kasra')
                    counts['switchContext' if context else 'switchOutsideContext'] += 1
                    if (form[i] == compact_mark.ALTERNATE) != (active and context):
                        finding('wrong-contextual-form', loc, i, glyph=form[i])
                if canonical[i] != 'space' or adv != 0:
                    rule = match_rule(kern, sequence, at, 'kern') if at < len(sequence) else None
                    at += 1
                else:
                    rule = None
                expected = u[2] + (moves[id(rule)] if rule else 0)
                if rule:
                    counts['contextualKernMatches'] += 1
                    if adv != expected:
                        finding('contextual-kern', loc, i, glyph=form[i], expected=expected, actual=adv)
                separator = ('space' in canonical[i] or 'linefeed' in canonical[i]) and adv != 0
                if not separator:
                    actual_width += adv
                    expected_width += expected
                elif adv != expected:
                    finding('space-advance', loc, i, expected=expected, actual=adv)
                if (separator and i > word_start) or (i+1 == len(after) and not separator):
                    counts['words'] += 1
                    if actual_width != expected_width:
                        finding('word-advance', loc, word_start, expected=expected_width, actual=actual_width)
                if separator:
                    word_start = i+1
                    actual_width = expected_width = 0
                if parent != s[5]:
                    finding('mark-parent', loc, i, glyph=form[i], expected=s[5], actual=parent)
                if s[5] >= 0:
                    counts['markAttachments'] += 1
                    root, seen = i, set()
                    while before[root][5] >= 0 and root not in seen:
                        seen.add(root)
                        root = before[root][5]
                    if root in seen or not 0 <= root < len(before):
                        raise ValueError('Invalid attachment graph in the authored source')
                    siblings[root].append(i)
                elif mark_class.get(source_names[s[0]]) == 3 and original_ink.path(s[0]).area:
                    counts['visibleMarksWithoutAuthoredParent'] += 1
                    if counts['visibleMarksWithoutAuthoredParent'] <= 3:
                        examples.append(dict(kind='authored mark without a GPOS parent', glyph=form[i],
                                             page=loc['page'], line=loc['line'], index=i))
                if target != s[6]:
                    finding('cursive-partner', loc, i, glyph=form[i], expected=s[6], actual=target)
                if mark_class.get(source_names[s[0]]) != 3 and not (canonical[i] == 'space' and adv == 0):
                    if previous_base is not None and before[previous_base][6] != i:
                        a = previous_base
                        if before[a][0] in outgoing and s[0] in incoming and stroke_was_joined(before, a, i):
                            counts['implicitStrokeChecks'] += 1
                            if not ink.meets(after[a][0], gid, x-after[a][3], y-after[a][4]):
                                finding('disconnected-implicit-stroke', loc, a, glyph=form[a], partner=form[i])
                    previous_base = i
                if target < 0:
                    continue
                counts['cursiveConnections'] += 1
                other = after[target]
                pair = join_pair(lookup, gid, other[0])
                if not pair:
                    finding('missing-anchor', loc, i, lookup=lookup, glyph=form[i], partner=form[target])
                    continue
                exit_xy, entry_xy = pair
                dx, dy = other[3]-x, other[4]-y
                mismatch = (exit_xy[0]-entry_xy[0]-dx, exit_xy[1]-entry_xy[1]-dy)
                if max(map(abs, mismatch)) > .5:
                    finding('anchor-alignment', loc, i, glyph=form[i], partner=form[target], delta=mismatch)
                if not ink.meets(gid, other[0], dx, dy):
                    joined = stroke_was_joined(before, i, target)
                    counts['newlyDisconnectedStrokes' if joined else 'alsoDisconnectedInRegular'] += 1
                    finding('disconnected-stroke', loc, i, glyph=form[i], partner=form[target],
                            joinedInSource=joined)

            # Group both direct marks and nested marks by authored base. Only
            # vertically separated, horizontally overlapping marks define an order.
            for group in siblings.values():
                for a_pos, a in enumerate(group):
                    for b in group[a_pos+1:]:
                        sa, sb = before[a], before[b]
                        if mark_class.get(source_names[sa[0]]) != 3 or mark_class.get(source_names[sb[0]]) != 3:
                            continue
                        aa, bb = original_ink.path(sa[0]).bounds, original_ink.path(sb[0]).bounds
                        if min(aa[2]+sa[3], bb[2]+sb[3]) <= max(aa[0]+sa[3], bb[0]+sb[3]):
                            continue
                        order = 1 if aa[3]+sa[4] < bb[1]+sb[4] else -1 if bb[3]+sb[4] < aa[1]+sa[4] else 0
                        if order:
                            counts['stackOrderPairs'] += 1
                            ta, tb = after[a], after[b]
                            ba, bc = ink.path(ta[0]).bounds, ink.path(tb[0]).bounds
                            if order * ((bc[1]+bc[3])/2+tb[4] - (ba[1]+ba[3])/2-ta[4]) <= 0:
                                finding('stack-order', loc, a, glyph=form[a], other=form[b])
    if not counts['markAttachments'] or not counts['cursiveConnections'] or (kern and not counts['contextualKernMatches']):
        raise ValueError('Missing mark, cursive or contextual-kern evidence in the full-corpus trace')
    return dict(weight=w, counts=dict(counts), findings=dict(errors), examples=examples)


def attachment_weights(font, rules):
    weights = {400, 550, 700}
    for rule in rules:
        if rule[2] == 'kern':
            weights.update(curve(rule))
    for weight, reasons in coverage_plan(font).items():
        if any('feature switch' in reason for reason in reasons):
            weights.add(weight)
    return sorted(weights)


def check_attachments(key, font, folder, work, profiles, common, args):
    places, _, corpus = prepare(key, work, font, profiles, common, args.corpus_root)
    source = font.parent/'intermediates/portable-regular.otf'
    cubic = font.parent/'intermediates/weight-reference.otf'
    report = json.loads((font.parent/'report.json').read_text())
    if file_hash(source) != report['sourceSha256'] or ('cubicSha256' in report and file_hash(cubic) != report['cubicSha256']):
        raise ValueError('Intermediate fonts differ from the build report')
    saved = font.parent/'intermediates/spacing-rules.json'
    if args.spacing_rules:
        saved = args.spacing_rules/(key+'.json')
    if not saved.is_file() and not args.spacing_rules:
        local = args.fonts.parent/'rules'/(key+'.json')
        saved = local if local.is_file() else RULES/(key+'.json')
    rules = json.loads(saved.read_text())
    snapshot_digest = sha256((json.dumps(rules, ensure_ascii=False) + '\n').encode()).hexdigest()
    if 'rulesSha256' in report['spacing'] and snapshot_digest != report['spacing']['rulesSha256']:
        raise ValueError('Intermediate spacing rules differ from the build report')
    if any(sum(r[2] == kind for r in rules) != report['spacing'][kind+'Rules'] for kind in ('mark','kern')):
        raise ValueError('Supply the rule JSON used by this build with --spacing-rules')
    probe = compile_release_trace(work, profiles)
    original = work/'original.trace'
    trace_file(probe, source, 400, corpus['tajweed'], work/'corpus.txt', original)
    baseline = work/'without-generated-kern.otf'
    checks = []
    with TTFont(source) as native, TTFont(font) as current:
        without_context_kern(cubic, native, baseline)
        weights = attachment_weights(current, rules)
        original_ink = Ink(native, 400)
        for weight in weights:
            candidate, plain = work/'candidate.trace', work/'baseline.trace'
            for path, trace in ((font, candidate), (baseline, plain)):
                trace_file(probe, path, weight, corpus['tajweed'], work/'corpus.txt', trace)
            row = check_weight(native, current, original, candidate, plain, places, weight, rules, original_ink)
            checks.append(row)
            save(folder/f'{weight}.json', row)
    status = ('failed' if any(r['findings'] for r in checks) else 'needs-review' if
              any(r['counts'].get('visibleMarksWithoutAuthoredParent') for r in checks) else 'passed')
    return dict(status=status, corpus=corpus,
                rulesSha256=file_hash(saved), weights=weights, checks=checks, traceEngine='repository HarfBuzz OpenType')
