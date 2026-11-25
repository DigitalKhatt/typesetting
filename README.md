# DigitalKhatt Typesetting

DigitalKhatt builds Quran fonts for apps and browsers. The OpenType collection
includes New Madina, Old Madina, and IndoPak. Each family offers a continuous
`wght` axis from 400 to 700, with Regular at 400 and Bold at 700.

This project is sponsored by [@tarteelAI](https://github.com/TarteelAI) [<img src="https://assets-global.website-files.com/6167e862f6dfba5084eb5554/61680717c50ec79defcdb062_logo-group.svg">](https://www.tarteel.ai/)

## Downloads

| Family | Install or use in an app | Use on the web |
| --- | --- | --- |
| New Madina | [TTF](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhattNewMadina-VF.ttf) | [WOFF2](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhattNewMadina-VF.woff2) |
| Old Madina | [TTF](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhattOldMadina-VF.ttf) | [WOFF2](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhattOldMadina-VF.woff2) |
| IndoPak | [TTF](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhattIndoPak-VF.ttf) | [WOFF2](https://github.com/DigitalKhatt/typesetting/releases/download/fonts-v0.1.0/DigitalKhattIndoPak-VF.woff2) |

The collection ZIP includes all six files and their font licenses. Asset names
stay the same across releases. The release tag identifies the exact version,
and the manifest records the inputs and settings needed to rebuild it.
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

Run these commands from a standalone `typesetting` checkout with Python 3.11
or later.

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m fonts.portable.rebuild_release releases/fonts-v0.1.0.json
```

The build downloads and checks the release’s three Regular OTFs, applies the
recorded build settings and saved spacing rules, and writes TTF, WOFF2, and
build reports to `dist/`. Later builds reuse the downloaded archive. After
setting up `.venv`, you can also open the `typesetting` folder in VS Code and
select **Build OpenType fonts** from **Tasks: Run Task**.

To build with your own static Portable exports, run
`python -m fonts.portable.build_portable --input-dir PATH`. For one family, pass
`--font madina`, `--font oldmadina`, or `--font indopak` with `--regular-otf`.
[Bolding and compilation](docs/bolding.md) · [Spacing rules](docs/spacing.md)

## Generate the release manifest

Run this command after building all three families.

```sh
python -m fonts.portable.release_manifest
```

The command reads `dist/*-build.json` and the source and exporter revisions
in `inputs.json`. It looks for `dist/portable-regular-inputs.zip` or the
extracted `portable-regular-inputs/` directory. Use `--inputs` for another
location. For fresh Regular exports, use the archive’s `inputs.json` as a
template beside the OTFs, record their source revisions, and omit the old
`regularInput` records. The command calculates the input checksums.

It checks the font files, Regular inputs, and spacing rules against the build
reports. Use `--spacing-rules` if you built with a different rule directory.

The default output is `dist/<tag>.json`. `--tag` selects a new release tag and
`--output` selects the JSON path under `releases/` when preparing a release.

## Build from glyph sources

With the native build prerequisites installed, run **Build OpenType fonts
from pinned glyph sources** in VS Code or use the command below. It downloads
the font projects, VisualMetaFont, and HarfBuzz at the commits in the manifest,
builds the native exporter, then generates the Regular OTFs and compiles the
variable fonts with the saved spacing rules. The output is `dist/from-source/`.
[Native prerequisites and development tasks](docs/development.md)

```sh
python -m fonts.portable.rebuild_release releases/fonts-v0.1.0.json --from-source
```

To regenerate the corpora and fit mark and kerning adjustments from an empty
set, use **Build fonts from scratch** in the full DigitalKhatt development
workspace. [Development setup](docs/development.md#build-from-scratch)

The compiler verifies decoded OpenType layout tables and compares outlines and
advances at weights 400, 550, and 700. Run the compiler tests with
`python -m unittest discover -s tests`. See the [release validation summary](releases/fonts-v0.1.0.md)
for the corpus and packaging results.
