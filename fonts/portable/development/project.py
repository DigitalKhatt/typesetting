"""Paths shared by the Portable development tools."""
from ..build_portable import FAMILIES
from ..mark_spacing import RULES
from . import TYPESETTING_DIR, WORKSPACE


VALIDATION_DIR = WORKSPACE / 'tools/font_validation'
DATA_DIR = RULES.parent
OUTPUT_DIR = TYPESETTING_DIR / 'output'
FONTS_DIR = OUTPUT_DIR / 'fonts'
CORPORA_DIR = OUTPUT_DIR / 'corpora'
GLYPH_USAGE_DIR = VALIDATION_DIR / 'output' / 'portable-glyph-usage'
