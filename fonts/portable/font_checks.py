"""Check serialized OpenType tables, quadratic outlines and metrics."""
from hashlib import sha256

from fontTools.misc.roundTools import otRound
from fontTools.pens.recordingPen import RecordingPen
from fontTools.ttLib import TTFont, newTable
from fontTools.ttLib.tables import otTables
from fontTools.ttLib.tables._g_l_y_f import flagOnCurve
from fontTools.varLib.instancer import instantiateVariableFont

from . import compact_mark


WEIGHTS = (400, 550, 700)


def file_hash(path):
    return sha256(path.read_bytes()).hexdigest()


def check_attachment_points(font):
    # Converting and reversing cubic contours changes the outline point IDs.
    # The portable profiles use coordinate anchors. A point-index attachment
    # in a future profile would need an explicit remap before exporting.
    pending = [font['GPOS'].table, font['GDEF'].table]
    visited = set()
    while pending:
        table = pending.pop()
        if id(table) in visited:
            continue
        visited.add(id(table))
        if (isinstance(table, (otTables.Anchor, otTables.CaretValue)) and table.Format == 2 or
                isinstance(table, otTables.AttachPoint) and table.PointIndex):
            raise ValueError('Portable layout references outline point IDs that require quadratic remapping')
        pending.extend(entry.value for entry in table.iterSubTables())


def check_font(source, final, masters, advances, layout_xml, work, *, positioned=None, compact=False):
    with TTFont(source) as original, TTFont(positioned or source) as layout, TTFont(final) as font:
        native = original.getGlyphOrder()
        order = native + ([compact_mark.ALTERNATE] if compact else [])
        check_attachment_points(original)
        if font.sfntVersion != '\x00\x01\x00\x00' or 'CFF2' in font:
            raise ValueError('Expected a quadratic TrueType font')
        axes = [(a.axisTag, a.minValue, a.defaultValue, a.maxValue) for a in font['fvar'].axes]
        if axes != [('wght', 400, 400, 700)] or font.getGlyphOrder()[:len(order)] != order:
            raise ValueError('Weight axis or source glyph order changed')
        expected_gsub = original.reader['GSUB']
        if compact:
            expected = newTable('GSUB')
            expected.decompile(expected_gsub, original)
            compact_mark.add_substitution(layout, expected.table)
            expected_gsub = expected.compile(layout)
        if font.reader['cmap'] != original.reader['cmap'] or expected_gsub != layout.reader['GSUB']:
            raise ValueError('Native character mapping or substitutions changed')
        if font.reader['GDEF'] != layout.reader['GDEF']:
            raise ValueError('GDEF differs from the positioned reference')
        if compact:
            glyf = font['glyf']
            source_mark, alternate = compact_mark.SOURCE, compact_mark.ALTERNATE
            if (glyf[source_mark].getCoordinates(glyf) != glyf[alternate].getCoordinates(glyf)
                    or font['hmtx'][source_mark] != font['hmtx'][alternate]):
                raise ValueError('Contextual mīm must have the same Regular outline and metrics')
        # Dump the decoded binary again. This compares every feature, lookup,
        # anchor and positioning value, independent of the packed offsets.
        check_xml = work / 'check-layout.ttx'
        font.saveXML(check_xml, tables=['GSUB', 'GPOS'])
        if file_hash(check_xml) != file_hash(layout_xml):
            raise ValueError('Decoded GSUB/GPOS differ after TTX packing')
        check_xml.unlink()
        features = {tag: [f.FeatureTag for f in font[tag].table.FeatureList.FeatureRecord]
                    for tag in ('GSUB', 'GPOS')}
        instance_widths = {w: font.getGlyphSet(location={'wght': w}) for w in WEIGHTS}
        for weight in WEIGHTS:
            fraction = (weight - 400) / 300
            with TTFont(final) as instance:
                instantiateVariableFont(instance, {'wght': weight}, inplace=True)
                glyf = instance['glyf']
                for name in order:
                    a, b = masters[name]
                    actual, ends, flags = glyf[name].getCoordinates(glyf)
                    expected = a.coordinates + (b.coordinates - a.coordinates) * fraction
                    if (actual != expected or list(ends) != a.endPtsOfContours or
                            bytes(v & flagOnCurve for v in flags) != bytes(v & flagOnCurve for v in a.flags)):
                        raise ValueError(f'{name} at {weight}: expanded quadratic outline changed')
                    w0, w1 = advances[name]
                    width = w0 + (w1 - w0) * fraction
                    x_min = expected.calcIntBounds()[0]
                    if (instance_widths[weight][name].width != width or
                            instance['hmtx'][name] != (otRound(width), x_min)):
                        raise ValueError(f'{name} at {weight}: advance or left sidebearing changed')
    return dict(glyphsChecked=len(order), weights=list(WEIGHTS), layoutXmlSha256=file_hash(layout_xml),
                features=features)


def check_woff2(ttf, woff2):
    with TTFont(ttf) as desktop, TTFont(woff2) as web:
        names = desktop.getGlyphOrder()
        if web.flavor != 'woff2' or web.sfntVersion != desktop.sfntVersion or web.getGlyphOrder() != names:
            raise ValueError('WOFF2 changed the font format or glyph order')
        tables = set(desktop.reader.keys())
        if tables != set(web.reader.keys()):
            raise ValueError('WOFF2 changed the table inventory')
        # WOFF2 reconstructs glyf and loca and updates head for the decoded envelope.
        for tag in sorted(tables - {'head', 'glyf', 'loca'}):
            if web.reader[tag] != desktop.reader[tag]:
                raise ValueError(f'WOFF2 changed the {tag} table')
        head = desktop['head']
        web_head = web['head']
        for key, value in head.__dict__.items():
            if key not in {'checkSumAdjustment', 'flags', 'indexToLocFormat'} and getattr(web_head, key) != value:
                raise ValueError(f'WOFF2 changed head.{key}')
        if (head.flags ^ web_head.flags) & ~(1 << 11):
            raise ValueError('WOFF2 changed layout-relevant flags')
        for weight in WEIGHTS:
            first = desktop.getGlyphSet(location={'wght': weight})
            second = web.getGlyphSet(location={'wght': weight})
            for name in names:
                left, right = RecordingPen(), RecordingPen()
                first[name].draw(left)
                second[name].draw(right)
                if left.value != right.value or first[name].width != second[name].width:
                    raise ValueError(f'WOFF2 changed {name} at weight {weight}')
        return dict(glyphsChecked=len(names), weights=list(WEIGHTS), tableCount=len(tables))
