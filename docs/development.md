# Build OpenType fonts from source

Open the `typesetting` checkout in VS Code with **File > Open Folder**. Run the commands below from that checkout. Set up `.venv` and the pinned Python dependencies to rebuild the release. [Standalone setup](../README.md#build-the-opentype-fonts)

## Rebuild with the pinned glyph sources

Install Git, CMake 3.24 or later, a C++23 toolchain, Flex, Bison, and the SQLite development library. The `fonts-v0.1.0` source rebuild was verified on macOS with Homebrew LLVM 22.1.2, Flex, and Bison. The task detects those Homebrew tools and honors `CC` and `CXX` when set. The source exporter’s generated glyph names can vary by toolchain, and the saved spacing rules refer to those names.

```sh
python -m fonts.portable.rebuild_release releases/fonts-v0.1.0.json --from-source
```

In the VS Code Command Palette, choose **Tasks: Run Task**, then **Build OpenType fonts from pinned glyph sources**. The task caches the Git checkouts and the exporter build under `output/rebuild/`. It applies the release’s saved spacing rules and writes the variable fonts to `dist/from-source/`.

## Build from scratch

Refitting and full-corpus validation use the native renderer and shared corpus tools. For these tasks, place `typesetting/` inside the DigitalKhatt development workspace with VisualMetaFont, the three font projects, and `tools/font_validation/`.

Run **Build fonts from scratch** in VS Code. It builds the native exporter and renderer, exports the complete original corpora with CGJs retained, fits mark and kerning adjustments from an empty set, and builds TTF and WOFF2 for all three families. The equivalent commands use Python 3.11 or newer and the prerequisites for the macOS CMake preset.

```sh
python3 -m pip install -r requirements.txt
(
  cd .. &&
  cmake --preset macos &&
  cmake --build --preset release-macos --target digitalkhatt_generate_opentype \
    digitalkhatt_generate_mushaf digitalkhatt_geometry digitalkhatt_layout \
    madina_font oldmadina_font indopak_font
)

python3 -m fonts.portable.development.prepare_corpora --output output/corpora
python3 -m fonts.portable.development.regenerate_spacing fit \
  --corpus-root output/corpora --output output/fit
python3 -m fonts.portable.development.build_portable --woff2 --check-shaping --keep-intermediates \
  --corpus-root output/corpora --spacing-rules output/fit/rules --output output/fit/fonts
```

The fitter also needs a C++20 compiler, HarfBuzz and Clipper2 available through `pkg-config`. It tests HarfBuzz at 400, 550 and 700 by default. Use the [release checks](validation.md#release-checks) to test the full weight axis and the other engines. A fresh fit is a candidate. Reproduce an existing release using its pinned glyph sources, calibrated rules, dependencies and timestamp. [Release manifest](../releases/fonts-v0.1.0.json) · [How the rules are fitted](spacing.md#how-the-fitter-generates-the-json)

## Build from saved inputs

| VS Code task | Inputs and output |
| --- | --- |
| **Build fonts from saved rules** | Uses the calibrated rules in `fonts/portable/data/mark_spacing/`. Writes `output/fonts/`. |
| **Rebuild fonts from last fit** | Uses the generated rules in `output/fit/rules/`, including local edits. Writes `output/fit/fonts/`. |
| **Build fonts from archived checkpoints** | Replays the archived calibration and verifies the three rule files. Writes `output/replay/fonts/`. |
| **Build and check release fonts** | Uses the calibrated rules, builds TTF and WOFF2, and runs the release checks. |

These tasks prepare missing corpora. Run **Refresh original corpora** when changing a corpus input, layout, or normalization setting. Each family’s `latest.json` points to its latest complete original text export. The native builder exports a fresh Regular OTF from the selected font project, then calls the same compiler. [Standalone build](../README.md#build-the-opentype-fonts)

```sh
python3 -m fonts.portable.development.prepare_corpora --if-missing
python3 -m fonts.portable.development.build_portable --woff2 --check-shaping --keep-intermediates
```

Use `--font madina`, `--font oldmadina`, or `--font indopak` for a single family. Each build writes a family directory with the font files and `report.json`. The report records outline and layout checks, full-corpus shaping comparisons at 400, 550 and 700 when enabled, and byte counts at each step. Set `SOURCE_DATE_EPOCH` for reproducible font bytes with the same sources, rules and tool versions. [Task definitions](../.vscode/tasks.json)

## Review used glyphs

Run **Serve corpus glyph review** in VS Code and select an existing build. The task generates a site under that build’s `glyph-review/` directory and serves it on loopback. Switch families, drag **Weight**, or turn on **Overlay Regular** to see gained and lost ink. **Compact** shows more glyphs at once. Search by name or GID, filter by glyph class, or click a tile to view its outlines beside an original corpus line.

The grid includes glyphs produced by HarfBuzz on the full **No justification** corpora at 400, 550 and 700 and at weights around GSUB feature switches. The glyph detail shows the observed weight for its corpus example.

```sh
python3 -m fonts.portable.development.viewer.review_glyphs --serve --port 0 --open
```

Use `--fonts` to select another build and `--output` to choose the site directory. Stop the VS Code task or use Ctrl+C to stop the server.

## Source layout and tests

| Directory | Contents |
| --- | --- |
| [`fonts/portable/`](../fonts/portable/) | Shared compiler, Bézier bolding, TrueType conversion, saved rules and checkpoint archive |
| [`fonts/portable/development/`](../fonts/portable/development/) | Native export, corpus preparation and rule fitting |
| [`development/validation/`](../fonts/portable/development/validation/) | Corpus shaping, clearance, glyph usage, and release checks with native probes |
| [`development/viewer/`](../fonts/portable/development/viewer/) | Glyph review generator, site assets and interactive examples |
| [`tests/`](../tests/) | Standalone compiler tests and workspace tests under `tests/development/` |

Run **Run variable font unit tests** in VS Code or run the two suites directly.

```sh
python3 -m unittest discover -s tests
python3 -m unittest discover -s tests/development
```

Install the optional dependencies with **Install release validation dependencies** for the image and attachment checks. [Validation workflows](validation.md)
