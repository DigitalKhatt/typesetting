"""Export selected cubic Bold glyphs with their Regular outlines in a reference layer."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from fontTools.pens.pointPen import SegmentToPointPen
from fontTools.ttLib import TTFont
from fontTools.ufoLib import UFOWriter


def export_ufo(font, path, names):
    with UFOWriter(path, formatVersion=3) as writer:
        writer.writeInfo(SimpleNamespace(familyName=font['name'].getDebugName(1),
                                         styleName='Bold', unitsPerEm=font['head'].unitsPerEm))
        for weight in (700, 400):
            layer = writer.getGlyphSet() if weight == 700 else writer.getGlyphSet('Regular', defaultLayer=False)
            glyphs = font.getGlyphSet(location={'wght': weight})
            for name in names:
                glyph = glyphs[name]
                layer.writeGlyph(name, SimpleNamespace(width=glyph.width),
                                 lambda pen: glyph.draw(SegmentToPointPen(pen)))
            layer.writeContents()
        writer.writeLayerContents()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('reference', type=Path, help='weight-reference.otf from a build with --keep-intermediates')
    parser.add_argument('output', type=Path, help='New UFO directory')
    parser.add_argument('--glyph', required=True, action='append', help='Glyph to export (repeatable)')
    args = parser.parse_args()
    if args.output.exists():
        parser.error('Choose a new UFO directory to preserve existing edits')
    with TTFont(args.reference) as font:
        if 'CFF2' not in font or 'fvar' not in font:
            parser.error('Expected a cubic variable reference from the portable build')
        names = list(dict.fromkeys(args.glyph))
        unknown = sorted(set(names) - set(font.getGlyphOrder()))
        if unknown:
            parser.error(f'Unknown glyphs: {unknown}')
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=args.output.parent) as stage:
            master = Path(stage) / 'Bold.ufo'
            export_ufo(font, master, names)
            master.rename(args.output)


if __name__ == '__main__':
    main()
