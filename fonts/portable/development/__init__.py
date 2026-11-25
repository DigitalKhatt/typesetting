"""Portable font development tools for the full DigitalKhatt workspace."""
from pathlib import Path
import sys


TYPESETTING_DIR = Path(__file__).resolve().parents[3]
WORKSPACE = TYPESETTING_DIR.parent

# Native export and validation use the shared tools in the enclosing workspace.
if (WORKSPACE / 'tools/font_validation/validate.py').is_file() and str(WORKSPACE) not in sys.path:
    sys.path.insert(1, str(WORKSPACE))
