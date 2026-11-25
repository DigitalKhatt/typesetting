# DigitalKhatt Typesetting

DigitalKhatt builds Quran fonts for apps and browsers. The OpenType collection
includes New Madina, Old Madina, and IndoPak. Each family offers a continuous
`wght` axis from 400 to 700, with Regular at 400 and Bold at 700.

This project is sponsored by [@tarteelAI](https://github.com/TarteelAI) [<img src="https://assets-global.website-files.com/6167e862f6dfba5084eb5554/61680717c50ec79defcdb062_logo-group.svg">](https://www.tarteel.ai/)

## Downloads

| Family | Install or use in an app | Use on the web | License |
| --- | --- | --- | --- |
| New Madina | [TTF](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhattNewMadina-VF.ttf) | [WOFF2](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhattNewMadina-VF.woff2) | [OFL](https://github.com/DigitalKhatt/madinafont/blob/76fec9e1255c9ffebe68521d5f3ec3cb26e34388/LICENSE) |
| Old Madina | [TTF](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhattOldMadina-VF.ttf) | [WOFF2](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhattOldMadina-VF.woff2) | [OFL](https://github.com/DigitalKhatt/oldmadinafont/blob/04d33f799965b50c10d5518a9c6bcddf508bea98/LICENSE) |
| IndoPak | [TTF](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhattIndoPak-VF.ttf) | [WOFF2](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhattIndoPak-VF.woff2) | [OFL](https://github.com/DigitalKhatt/indopakfont/blob/7bccd4acf0662eb7433a2acfedb8fa4c201a9fe0/LICENSE) |

The collection ZIP includes all six files and their font licenses. Asset names
stay the same across releases. The release tag identifies the exact version,
and the manifest records sizes, checksums, and source revisions.
[Collection ZIP](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhatt-OpenType-Fonts.zip) · [Release manifest](releases/fonts-v0.1.0.json)

Host the WOFF2 next to your stylesheet and declare the full weight range.

```css
@font-face {
  font-family: "DigitalKhatt New Madina";
  src: url("DigitalKhattNewMadina-VF.woff2") format("woff2");
  font-weight: 400 700;
  font-style: normal;
}
```

## Build the OpenType fonts

Use Python 3.11 or later. Download and extract the [Regular OTF inputs](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/portable-regular-inputs.zip)
beside this README. The archive contains the three static Portable CFF2 exports
used for this release. The build applies the saved spacing rules and writes a
TTF, a WOFF2, and a build report per family.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
SOURCE_DATE_EPOCH=1767225600 python -m fonts.portable.build_portable \
  --input-dir portable-regular-inputs --output dist
```

Use `--font madina`, `--font oldmadina`, or `--font indopak` to select one
family. To use your own static Portable export, pass `--regular-otf` with
that family. Each JSON report records the input hash, settings, and output
hashes. Use the release’s tool versions and timestamp to reproduce its bytes.
[Bolding and compilation](docs/bolding.md) · [Spacing rules](docs/spacing.md)

## Develop from the font sources

In the full DigitalKhatt workspace, open `digitalkhatt.code-workspace` and run
**Build fonts from scratch** in the **Typesetting** folder. The task exports
Regular OTFs and the original corpora, fits spacing rules from an empty set,
and builds all three TTFs and WOFF2s. Saved-rule builds, validation, and the
corpus glyph viewer are available in the same workspace. Both build workflows
use the same compiler. [Development setup and commands](docs/development.md)

To regenerate the Regular inputs from authored glyphs, build
`digitalkhatt_generate_opentype` and the family plugins from the pinned
revisions in the release manifest. Run the exporter with the matching
VisualMetaFont resource directory and the family’s `.mp` project file.

```sh
digitalkhatt_generate_opentype --profile portable --no-variable \
  --resources /path/to/visualmetafont/src/Resources/metafont \
  --features features.fea -o portable-regular-inputs/madina.otf \
  /path/to/madinafont/madina.mp
```

The compiler verifies decoded OpenType layout tables and compares outlines and
advances at weights 400, 550, and 700. Run the compiler tests with
`python -m unittest discover -s tests`. See the [release validation summary](releases/fonts-v0.1.0.md)
for the corpus and packaging results.

## Licenses

The build code and documentation use MIT. [License text](LICENSE)

The download table links to each family’s source license. Both release ZIPs
include the corresponding copyright notices and the SIL Open Font License text.
