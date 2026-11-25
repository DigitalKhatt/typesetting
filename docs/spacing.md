# Contextual spacing rules

The native Portable export supplies the default mark anchors, cursive joins, kerning, and advances. The builder appends local GPOS adjustments for measured within-line clearance problems. The rules are shared by the three engine checks and reviewed with the fonts at actual weight instances. [Calibrated rules](../fonts/portable/data/mark_spacing/) · [GPOS compiler](../fonts/portable/mark_spacing.py)

Each JSON row gives a glyph sequence in logical reading order, the zero-based index of the glyph to adjust, the positioning kind, and the values at Regular and Bold. Optional intermediate entries add a weight and its adjustment. Mark values are `[xPlacement, yPlacement]` in font units. Kern values hold the extra horizontal advance and optional vertical placement. The same glyph context can occur on multiple lines. The longest matching context takes priority.

The compiler adds kerning, then parent shifts, the attachment pass, and child shifts. For example, some Old Madina fatḥas carry an iqlāb mīm. Moving the fatḥa and reapplying its native mark-to-mark attachment lets the child follow before its own local adjustment. Each chained lookup indexes the input by the glyph it positions, so a rule consumes one glyph and adjacent marks can be adjusted independently. Nested SinglePos lookups store different values for each covered glyph and share the encoded deltas through the GDEF variation store.

## Which bases can receive automatic kerning?

For a contact between two bases, the fitter tests the second glyph in logical reading order. An isolated GDEF base with an `.isol` form and no cursive anchors can receive an `XAdvance` adjustment and up to 12 font units of vertical placement in either direction. An initial base can receive only `XAdvance`. Both policies require a GDEF base and knowledge of its cursive anchors. The fitter accepts an `.init` form without an entry anchor, or a base with an exit-only anchor, as a run start. It skips medial and final forms and any glyph that can receive a cursive join. For example, New Madina’s `lam.init` has an entry anchor. Marks have their own placement search. [Proposal search](../fonts/portable/development/validation/native/mark_contacts.cpp)

With the right edge of an RTL line held fixed, each glyph origin includes the accumulated advances through that glyph. Adding 36 units to the initial glyph’s advance shifts its origin and every following glyph 36 units left. Its exit anchor and the following letter’s entry anchor move together. The extra space opens between the initial and the preceding glyph. [OpenType positioning](https://learn.microsoft.com/en-us/typography/opentype/spec/gpos#overview)

## Contexts that need intermediate weights

| Family | Calibrated contexts | Contexts with intermediate positions | Distinct intermediate weights |
| --- | ---: | ---: | ---: |
| New Madina | 89 | 1 | 1 |
| Old Madina | 273 | 71 | 251 |
| IndoPak | 84 | 0 | 0 |

In Old Madina's spelling of **Idrīs**, on page 309 line 4 and page 329 line 6, the extra advance on the initial yāʾ (`behshape.init.beforeseen`) rises from 0 at 400 to 36 font units at 410, holds through 470, then returns to 0 at 485. At 470, HarfBuzz, native CoreText and DirectWrite each moved the initial, its two dots, and the final sīn 36 units left. The preceding rāʾ stayed in place and the two cursive anchors still coincided. The resulting gap gives the dots room between the strokes. [Old Madina rules](../fonts/portable/data/mark_spacing/oldmadina.json)

A different Old Madina case places a small low mīm inside a final ḥāʾ bowl on page 97 line 2 and page 531 line 1. At weights above Regular, the sequence `hah.fina kasra smalllowmeem` selects an unencoded copy with the same Regular outline, zero advance, class and anchors. At Bold the copy is 94% of the ordinary bolded outline around its ink center. Other occurrences use the usual small low mīm. [Contextual outline](../fonts/portable/compact_mark.py)

The original fit measured Old Madina's 9,046 lines with the GUI's 10-unit minimum. Later passes sampled difficult cases at every integer weight and kept the intermediate positions needed to reproduce their rounded adjustments. The last DirectWrite pass kept 11 movements across eight contexts and added weight 545 as a new breakpoint. For one two-dot mark, the extra `(+6, +1)` at 550 and 551 starts tapering from the previous curve at 545 and rejoins it at 556. [Recorded inputs](../fonts/portable/data/spacing-checkpoints.zip) · [Replay code](../fonts/portable/development/spacing_history.py)

## Replay the calibrated rule files

From the `typesetting` checkout, run

```sh
python3 -m fonts.portable.development.regenerate_spacing replay --output output/replay
python3 -m fonts.portable.development.build_portable --woff2 --check-shaping --keep-intermediates \
  --spacing-rules output/replay/rules --output output/replay/fonts
```

Replay loads the archived rule snapshots and applies the recorded final edits for New Madina and Old Madina. It verifies the SHA-256 of each archived input and each regenerated JSON. Use `--font oldmadina` on either command to select one family. `--archive` also accepts the same extracted checkpoint files in a directory. In VS Code, **Build fonts from archived checkpoints** runs the replay and build together.

## Fit new rules

Prepare the native tools and original corpus first. To start with the calibrated rules, use

```sh
python3 -m fonts.portable.development.prepare_corpora --if-missing
python3 -m fonts.portable.development.regenerate_spacing fit --font oldmadina --weight-step 25 \
  --seed-rules fonts/portable/data/mark_spacing --output output/fit
```

Omit `--seed-rules` to start with empty adjustment data. The default weights are 400, 550, and 700. Use `--weight-step 1` for integer weights or repeat `--weight` for extra fractional samples. The default engine is HarfBuzz OpenType. Repeat `--engine` with `ot`, `coretext` and `directwrite` to fit one set of rules against all three. [Native setup](validation.md)

### How the fitter generates the JSON

1. Build a complete Portable TTF from the current font sources, the Bold outlines, and the current rules. Shape the original corpus with CGJs retained using the family's **No justification** profile and Tajweed setting. Repeat at each selected weight in each selected engine. The checker measures positioned ink within each line using the GUI's collision rules and the shared 10-unit minimum. [Corpus preparation and clearance check](../fonts/portable/development/validation/check_mark_spacing.py)
2. Try movements on the flagged lines. The geometry probe searches a two-unit grid for a mark translation or kerning on an eligible base. With the current settings it aims for 11 units of clearance. When a sample yields no single-glyph proposals, it tries moving two marks in the same mark run. It searches up to 24 units per mark and checks that the marks clear surrounding ink and each other. The paired search tries 11 units, then 10. [Geometry search](../fonts/portable/development/validation/native/mark_contacts.cpp)
3. Turn each proposal into a context. The fitter records the proposed span of glyph names in logical reading order, the target's index within that span, and whether the move is a mark adjustment or a kern. It includes the full context of any matching existing rule. Later rounds include more neighboring glyphs so occurrences of a short sequence can receive different corrections. [Context matching](../fonts/portable/development/spacing_curves.py)
4. Group proposals by context and weight across corpus occurrences and engines. Pick the move proposed most often, breaking ties by shorter displacement and then coordinate order. Add the chosen move to the rounded correction already applied by the matching rule. A sampled weight without a proposal keeps that rounded correction. Each stored value gives the total adjustment relative to the native positioning. [Vote selection and fitting](../fonts/portable/development/spacing_curves.py)
5. Keep Regular, Bold, and the inherited breakpoint weights. Keep an additional sample only where the rounded linear interpolation would otherwise miss a measured value. The resulting JSON stores the intermediate weights that the compiler needs for the piecewise-linear GPOS adjustment. [Curve simplification](../fonts/portable/development/spacing_curves.py)
6. Rebuild with the proposed rules and measure the actual shaped result. Keep the round with the fewest intersections, then the fewest total findings. Stop when a round fails to improve, no proposal changes the rules, or the revision limit is reached. `--rounds` defaults to five. [Fitter](../fonts/portable/development/regenerate_spacing.py)

This recorded New Madina rule moves the qāf stop sign after a maddah. Index `1` selects `waqf.qaf`. Its Y correction is 0 at 400, +3 at 450, and +8 at 700. Linear interpolation between the last two points gives +5 at 550. [New Madina rules](../fonts/portable/data/mark_spacing/madina.json)

```json
[["maddahabove", "waqf.qaf"], 1, "mark", [0, 0], [0, 8], [[450, [0, 3]]]]
```

The chosen rules and fonts appear under `rules/` and `fonts/<family>/` in the output directory. The `<family>-fit.json` report names the selected round and its corpus. That round's `checks/` records the measured gaps, and `proposals.json` records the vote counts when the round collected proposals. Exit status 1 means the selected samples still have clearance findings.

The rule contexts use glyph names from the current export. If a source change renumbers expandable glyphs, a seeded fit needs the names updated to their verified equivalent glyphs. A fresh fit with an empty seed records names from the new export.

To inspect proposed movements for a particular build, run the clearance checker with `--suggest`. To measure how much the calibrated rules change positioning, build a separate reference with `--no-spacing-adjustments` and use the same corpus, engine, and weights for both builds. [Clearance checker](validation.md#within-line-clearance-in-the-portable-fonts)

A candidate that clears the fitted weights can still have close pairs between them. An earlier Old Madina fit checked only at 400, 550, and 700 and cleared those three samples. At 475 it had 17 close pairs. At 625 it had 175, including 32 intersections. Use the independent checker at additional weights after a change to the bolding recipe, quadratic fit or positioning data. [Clearance validation](validation.md#within-line-clearance-in-the-portable-fonts)
