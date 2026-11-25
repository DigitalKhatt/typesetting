# Validate the portable fonts

Run these commands from the `typesetting` checkout. The font tools use `output/fonts` and `output/corpora` by default. Use `--fonts` and `--corpus-root` for another build or corpus directory. The shared validation setup provides the saved corpus profiles and original text with CGJs retained. See `tools/font_validation/README.md` in the enclosing workspace.

## Build checks

The builder expands the serialized TTF at 400, 550, and 700 and checks outlines, contour boundaries, on-curve flags, advances, and left sidebearings against the unshared masters. It checks character mapping, original glyph order, the expected substitutions, and the packed positioning. With `--check-shaping`, HarfBuzz OpenType and the HarfBuzz CoreText backend, when available, each compare glyph IDs, clusters, advances, and offsets against the cubic reference and flat TTF across their full original corpora. [Validation code](../fonts/portable/development/validation/portable_checks.py)

Use `--keep-intermediates` to retain the native `portable-regular.otf`, cubic `weight-reference.otf`, flat and component-shared TTFs, `layout.ttx`, spacing rule snapshot, and export log. The build report includes byte counts for each stage and its OpenType tables. The Regular comparison uses the positioned cubic if the rules adjust Regular positions. Use `--reference-run` with a single `--font` to choose a particular saved original corpus.

Check the saved cubic reference for newly introduced loops and segment crossings relative to its Regular master.

```sh
python3 -m fonts.portable.check_outlines \
  output/fonts/madina/intermediates/weight-reference.otf \
  --weights 450 500 550 600 650 700 \
  --output ../tools/font_validation/output/portable-outlines.json
```

The report lists the glyph, contour, segment, contact coordinates and extent of each new single-cubic loop, and pairs of segments that cross more often than in Regular. Use the corpus viewer and native reading proofs to inspect the compiled TTF in context. [Outline checker](../fonts/portable/check_outlines.py) · [Outline tests](../tests/test_bezier_weight.py)

## Within-line clearance in the portable fonts

The native probes use the GUI's **No justification** profile for each complete original corpus. They keep CGJs, apply the family's Tajweed setting, and test each line separately. New Madina uses QPC v2, Old Madina uses QPC v1, and IndoPak uses its 15-line edition. The profile definitions are in `tools/font_validation/config/profiles.json` in the enclosing workspace.

The checker uses the GUI's exclusions for intentional joins and spaces. A negative gap indicates an intersection. A positive gap below 10 font units is a near contact under the shared settings. HarfBuzz and CoreText use instanced TTF outlines drawn by HarfBuzz for this check. DirectWrite supplies its own positioned native curves under Wine and verifies the variable weight selected by the engine. The shared workspace contains the GUI rules in `visualmetafont/lib/digitalkhatt/src/layout/CollisionReport.cpp` and the DirectWrite probe in `tools/directwrite/shape.cpp`.

Build the native `digitalkhatt_geometry` and `digitalkhatt_layout` targets, and make a C++20 compiler, HarfBuzz and Clipper2 available through `pkg-config`. Use the shared DirectWrite setup before selecting that engine.

```sh
python3 -m fonts.portable.development.validation.check_mark_spacing --engine ot --weight-step 1 --stage hb
python3 -m fonts.portable.development.validation.check_mark_spacing --engine coretext --weight-step 25 --stage coretext
python3 ../tools/font_validation/validate.py setup-directwrite --dwrite-version 2.6.4
python3 -m fonts.portable.development.validation.check_mark_spacing --engine directwrite \
  --dwrite-version 2.6.4 --weight-step 25 --interval-midpoints
```

Add `--weight` to sample a specific weight. With a step of 25, a separate `--interval-midpoints` run checks 412.5, 437.5, and the rest through 687.5. A JSON summary and compressed TSV per engine and weight record the page, line, glyph names, UTF-16 clusters, and signed clearance. Standalone runs use `tools/font_validation/output/portable-mark-spacing/<family>/unjustified/<stage>/` unless `--output` is set. A nonzero status reports a remaining clearance finding.

### Measure a gap on the native curves

The GUI flattens curves with a 5-unit tolerance. Use `--review-margin 10` to save candidates below 20 units in a separate TSV, with the ordinary 10-unit threshold still used for the summary and exit code. Use `--keep-records` to retain the positioned native curves for independent measurement.

```sh
spacing_output=../tools/font_validation/output/portable-spacing-audit
python3 -m fonts.portable.development.validation.check_mark_spacing --engine directwrite \
  --dwrite-version 2.6.4 --family oldmadina --weight 700 --review-margin 10 --keep-records \
  --output "$spacing_output" --stage bold-review
python3 -m fonts.portable.development.validation.verify_outline_gap --family oldmadina --weight 700 --nearby \
  --run "$spacing_output/oldmadina/unjustified/bold-review" --output "$spacing_output/bold-curves.json"
```

The curve checker bounds the shortest ink distance by subdividing the positioned Béziers. It accounts for winding and filled containment so a counter stays open space. A gap passes when the lower bound reaches the threshold. A confirmed shortfall or an interval that remains indeterminate after refinement fails. Use `--case PAGE LINE FIRST SECOND` to select a pair directly. Page and line numbers start at one, and the glyph indices are zero-based logical indices from the TSV. [Curve verifier](../fonts/portable/development/validation/verify_outline_gap.py)

## Release checks

Use **Build and check release fonts** or run the builder with `--release`. To check an existing build with its saved intermediates, run

```sh
python3 -m pip install -r requirements.txt skia-pathops opentype-sanitizer Pillow
python3 -m fonts.portable.development.validation.release_checks --goldens reading-goldens
```

| Order | Check | Switch to disable |
| --- | --- | --- |
| 1 | Within-line clearance across the axis in HarfBuzz, CoreText, and DirectWrite | `--no-weight-coverage` |
| 2 | Mark ownership and stacking, cursive anchors and ink contact, and word advances after kerning | `--no-attachments` |
| 3 | OpenType Sanitizer and reading-size image comparisons on macOS, Windows, and Android | `--no-app-rendering` |

Weight coverage uses variation breakpoints in the live compiled tables, samples around contextual feature switches, and bisects intervals with near-threshold gaps. Attachment validation compares native and weighted positioning and checks which glyph each mark attaches to. It checks cursive anchor alignment and uses PathOps to check that the shaped strokes meet. It runs at 400, 550, 700, intermediate contextual kern breakpoints, and the weights around feature switches. [Weight selection](../fonts/portable/development/validation/release_weights.py) · [Attachment checks](../fonts/portable/development/validation/release_attachments.py)

The release report is `release/release.json` next to the chosen `fonts/` folder by default. A failed check or missing review returns a nonzero exit after the other stages have run. A subset that passes is recorded as `passed-selected`. Use `--output` on the standalone checker or `--release-output` on the builder to select a different destination. [Release runner](../fonts/portable/development/validation/release_checks.py)

### Native reading proofs

**Generate native reading proofs** creates a review gallery and marks the platform captures or approvals still needed. The gallery at `<release-output>/<family>/app-rendering/review.html` shows native images and flags fallback, missing glyphs, clipping, and closed counters. Approvals match a family, platform, sample profile, and renderer. [Rendering checks](../fonts/portable/development/validation/release_rendering.py)

The release runner captures on its macOS or Windows host using Qt with the native platform plugin. Desktop capture requires CMake, a C++17 compiler, and Qt 6.7 or newer. To capture on a separate Windows host, copy the complete kit there and run from a workspace checkout. Use `--platform apple` on macOS.

```sh
python -m fonts.portable.development.validation.native_capture capture --platform windows \
  --kit output/release/oldmadina/app-rendering/kit \
  --output output/release/captures
```

For Android, connect a device or emulator with API 31 or newer and set `ANDROID_HOME` to an SDK with platform and build tools. Put `javac` and `keytool` on `PATH`. Add `--capture-android` to the release command and use `--android-serial` if several devices are attached. The probe captures paragraphs through Android's StaticLayout, collects the font used for each glyph, and removes its test app when capture completes. [Native capture tool](../fonts/portable/development/validation/native_capture.py)

After visual review, approve a specific capture revision by passing its directory containing `capture.json`. The VS Code tasks use `reading-goldens` for these approved references.

```sh
python3 -m fonts.portable.development.validation.native_capture approve \
  --capture output/release/captures/apple/oldmadina/runs/REVISION \
  --goldens reading-goldens
```

## Audit glyph usage and compare builds

The corpus viewer shows glyphs actually used on the **No justification** corpus. The lookup audit also traces the configured **justified** layout and examines the GSUB lookup that allocated each alternate. Build with `--check-shaping --keep-intermediates` first. Native capture requires the configured macOS Ninja Release build and its `compile_commands.json`. The audit uses the HarfBuzz OpenType and CoreText backends.

```sh
python3 -m fonts.portable.development.validation.trace_portable_export
python3 -m fonts.portable.development.validation.audit_portable_usage --check-unlinked
```

The capture builds instrumented copies of the native exporter and renderer in a temporary directory. It verifies that the captured Regular font tables match the checked build and records the native feature ranges for each original line. The audit writes inventories under `tools/font_validation/output/portable-glyph-usage`. The files identify lookups that create alternates, features that reach them, GSUB intermediates, and components required by used glyphs. A glyph absent from these corpora can still be needed by other text or an optional feature. `--check-unlinked` measures temporary exports with unreachable generators disabled. [Capture](../fonts/portable/development/validation/trace_portable_export.py) · [Audit](../fonts/portable/development/validation/audit_portable_usage.py)

Keep the original checked build and its native feature plan to compare a candidate. The comparison matches renumbered glyphs by identity and checks outlines, metrics, and full shaping records at three weights. Add `--feature sk01` to test an explicitly enabled feature at Regular.

```sh
python3 -m fonts.portable.development.validation.compare_portable_builds "$BEFORE" "$AFTER" \
  --usage "$BEFORE_USAGE" --output ../tools/font_validation/output/portable-comparison
```
