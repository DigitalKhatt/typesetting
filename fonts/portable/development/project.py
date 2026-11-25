"""Paths shared by the Portable development tools."""
from ..build_portable import FAMILIES, FONT_STEMS
from ..mark_spacing import RULES
from . import TYPESETTING_DIR, WORKSPACE


VALIDATION_DIR = WORKSPACE / 'tools/font_validation'
DATA_DIR = RULES.parent
OUTPUT_DIR = TYPESETTING_DIR / 'output'
FONTS_DIR = OUTPUT_DIR / 'fonts'
CORPORA_DIR = OUTPUT_DIR / 'corpora'
GLYPH_USAGE_DIR = VALIDATION_DIR / 'output' / 'portable-glyph-usage'


def built_ttf(folder, family):
    """Find the TTF in a development build, including archived builds."""
    current = folder / (FONT_STEMS[family] + '.ttf')
    previous = folder / (FAMILIES[family] + '-Portable-VF.ttf')
    return previous if not current.is_file() and previous.is_file() else current
