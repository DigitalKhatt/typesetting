# DigitalKhatt OpenType fonts 0.1.0

This release includes **New Madina**, **Old Madina**, and
**IndoPak**. Each font uses standard OpenType shaping and a continuous
`wght` axis from 400 to 700.

| Family | TTF bytes | WOFF2 bytes |
| --- | ---: | ---: |
| New Madina | 759,552 | 154,028 |
| Old Madina | 903,040 | 165,452 |
| IndoPak | 736,724 | 128,364 |

Use the TTFs for installation or app bundling and the WOFF2s for websites.
`DigitalKhatt-OpenType-Fonts.zip` contains all six files and their SIL
Open Font License notices. The `portable-regular-inputs.zip` archive
contains the exact CFF2 inputs for the standalone build. Verify downloads
with `SHA256SUMS.txt`. [Rebuild instructions](https://github.com/DigitalKhatt/typesetting/blob/fonts-v0.1.0/README.md#build-the-opentype-fonts) · [Source revisions and checksums](https://github.com/DigitalKhatt/typesetting/blob/fonts-v0.1.0/releases/fonts-v0.1.0.json)

## Validation

- All six font files passed OpenType Sanitizer. At weights 400, 550, and
  700, the WOFF2 outlines and advances matched the TTFs and their layout
  tables matched byte for byte.
- Full-corpus HarfBuzz and HarfBuzz CoreText comparisons matched the cubic
  and flat TrueType references at 400, 550, and 700. The within-line
  clearance screen reported zero contacts at those weights across
  HarfBuzz, the HarfBuzz CoreText backend, and DirectWrite Core 2.1.0.
  All runs retained the original CGJs.
- The complete native justified layouts for the three families matched
  their references across 27,223 lines.
- The attachment check found zero anchor alignment errors and zero new
  stroke separations at the sampled weights. Its strict ink-contact check
  flagged existing lam–alef separations in Old Madina and IndoPak.
  Full-axis clearance and native reading-size review remain open.
