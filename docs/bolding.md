# Bolding and TrueType compilation

## How the bolding works

A cubic Bézier has two endpoints on the outline and two control points, or *handles*, that set the direction and curvature between them. The generator moves the endpoints, then fits the handles to the heavier shape. It keeps the same contours and the same sequence of lines and cubics, so every Regular point has a corresponding Bold point.

1. **Choose which side gains ink.** `bold_outline` uses contour winding and containment to distinguish outside boundaries from counters (enclosed white space). An outer boundary grows outward. A counter shrinks inward by a smaller amount. A separate inked component can grow correctly with either winding direction.
2. **Set how far the edge should move.** The defaults are 12 units horizontally and 10 vertically **per edge**, measured on a 1,000-unit em and rescaled for the source font. On a straight vertical stroke with two exterior edges, this adds about 24 units to its thickness. The tool divides the coordinates by the horizontal and vertical offsets before computing normals. This gives a single offset distance in that space and a smooth transition between the two strengths on diagonals.
3. **Move each endpoint.** At a smooth point, `offset_contour` moves the endpoint along the normal, perpendicular to the tangent. At a corner, it uses the intersection of the adjacent offset tangent lines and limits how far a sharp corner can project. On short, nearly straight segments it also limits the relative movement of neighboring endpoints to reduce the risk of reversing the segment.
4. **Refit each cubic.** At seven interior positions, from `t = 1/8` through `7/8`, `fit_handles` evaluates the Regular curve’s tangent to find its normal. Each target is the original curve point plus the desired offset along that normal. A least-squares fit adjusts the lengths of the two handles along their original tangent directions. The fit limits handle shortening and extension. When the fit is singular, each handle follows its endpoint. This approximates a curved offset with a single compatible cubic.
5. **Prevent folds during interpolation.** The fitter checks the proposed handles and the available space between segments of the same contour. It shortens handles or reduces movement at the affected knots before writing the master. [Why loops form and how the fitter prevents them](#why-loops-form-and-how-the-fitter-prevents-them)
6. **Interpolate the result.** CFF2 stores the coordinate differences between Regular and Bold. For any knot or handle, `P(w) = P400 + ((w − 400) / 300) × (P700 − P400)`. At 550 every point is halfway between its two masters. The font keeps Regular’s layout rules and interpolates horizontal metrics through `HVAR`.

### How far each point moves

The defaults `--x 12 --y 10` specify **per-edge targets at weight 700** for an ordinary exterior contour on a 1,000-unit em. Regular (400) has zero displacement. The generator scales these distances by `unitsPerEm / 1000` for other em sizes. At 550, every knot and handle has half of its own Regular-to-Bold displacement.

For a smooth endpoint, let `hx` and `hy` be the two offsets in font units. Divide the surrounding coordinates by `hx` in X and by `hy` in Y, then take the unit tangent `T = (u, v)` in that scaled space. The perpendicular direction is `(−v, u)`. With signed contour strength `r`, the displacement back in font units is `(−r × hx × v, r × hy × u)`. The sign selects the side that gains ink. At a corner, the generator combines the two adjacent normals and caps their intersection distance at twice the offset in scaled space. If the tangents reverse completely, it extends the tip along the incoming tangent. The short-segment limit can further adjust the endpoint.

On the top-right cubic of initial mīm, the starting tangent points right and the ending tangent points down. The first handle follows the starting endpoint up and extends along the horizontal tangent. The second handle follows the ending endpoint right and shortens along the vertical tangent. The seven-sample fit determines those two changes in handle length.

| Point on initial mīm | Regular (400) | Bold (700) | Bold displacement |
| --- | --- | --- | --- |
| Starting endpoint | (269, 341) | (269, 351) | (0, +10) |
| First handle | (325.09, 341) | (341.86, 351) | (+16.77, +10) |
| Second handle | (367, 199.27) | (379, 196.87) | (+12, −2.40) |
| Ending endpoint | (367, 149) | (379, 149) | (+12, 0) |

Points between the endpoints move according to the cubic blend of these four displacements. Halfway along this segment, the blend has weights `1/8, 3/8, 3/8, 1/8` and gives a Bold displacement of approximately `(+12.29, +4.10)`. [Explore the cubic on initial mīm](../fonts/portable/development/viewer/examples/initial-mim-weight.html)

### Why loops form and how the fitter prevents them

The seven offset samples describe where the edge should go. A least-squares fit can get close to those samples while doubling back between them. On final mīm at weight 550, the previous fit put the two knots **3.72 units apart** and extended the first handle **8.08 units**. The middle of the cubic turned back past its own tip and formed a 0.78-unit loop. The corrected instance has 4.61 units between knots and a 2.59-unit first handle.

For each cubic, the fitter looks for a direction in which the curve travels forward in both Regular and Bold. The projected derivative at any intermediate weight is a weighted average of the two masters’ derivatives. If both stay forward, the curve can never revisit an earlier point along that direction. When a proposed handle overshoots, the fitter shortens the handles along their existing tangents. A bend that turns through several directions can be checked in smaller pieces.

Two different segments can also cross as each expands into the same narrow space. This happened on **ﷺ**. A Bézier stays inside the convex hull of its controls. The fitter looks for a direction that puts the controls of one piece on one side of the other piece in both masters. That ordering holds through interpolation. It subdivides curves and, when necessary, the weight interval to check a close approach. If the proposed fit loses a separation established in Regular, it halves the movement at the participating knots and retests. At a cursive join it reads the authored GPOS anchor and measures the opposite edge from the font’s own join shapes. If a correction would move a joining edge off target, it also tries moving the nearby wall with that edge. Subdivision is used for checking only. The output keeps the original knots and curve sequence. [Fitting constraints](../fonts/portable/curve_constraints.py)

The join stretch uses each knot’s distance from the line segment between the Regular join edges, measured in the fitter’s scaled coordinates. Its reach is 1.5 times the span between those edges, with a minimum of three times the larger proposed edge movement to cover single-point joins. The stretch fades smoothly to zero over the outer half of that reach. Both handles follow their knot’s transform. Beyond the reach the fitter reduces a risky offset toward Regular, which keeps a join correction from pulling in the distant tail of a glyph.

The separation check works within each contour. For adjacent curves it checks the pieces away from their shared knot, including the first and last curves at a closing seam. Regions whose separation cannot be established in Regular are treated as existing or unresolved contacts. Separate contours, marks on a base, connections between glyphs and hand-edited UFO overrides still need proofing in context.

### Controlling the weight

| Option | Default | Effect on Bold |
| --- | --- | --- |
| `--x`, `--y` | `12`, `10` | Horizontal and vertical growth per exterior edge, in units per 1,000 em. |
| `--mark-scale` | `0.45` | Multiplies the strength for glyphs classified as marks in GDEF. |
| `--counter-scale` | `0.5` | Sets the fraction of the glyph’s strength used on a counter. Its offset is also capped at 12% of the smaller dimension of its normalized bounding box. |
| `--scale 'GLYPH_GLOB=FACTOR'` | `1` | Multiplies the glyph’s strength. Repeat the option for different families. The last matching pattern wins. |
| `--fixed-advances` | Off | Keeps the original advances throughout the weight axis for fixed Mushaf spacing. |

For example, `--scale 'meem.*=0.8'` reduces automatic growth on matching mīm forms to 80% of their usual strength. A factor of zero keeps the Regular outline for those glyphs. An explicit UFO override takes precedence.

### How the shaper gets the advance width

For ordinary spacing glyphs, the builder adds the change in horizontal ink width to the Regular advance. It measures the extrema of the final curves, including any edited Bold contours, and rounds each edge to font units. The shaper reads the Regular advance from `hmtx` and the interpolated delta from `HVAR`. GPOS can then adjust the result for a cursive join or a kerning context. [OpenType horizontal metrics](https://learn.microsoft.com/en-us/typography/opentype/spec/hvar)

```text
left edge movement  = xMinBold − xMinRegular
right edge movement = xMaxBold − xMaxRegular
advance delta       = right edge movement − left edge movement
LSB delta           = left edge movement
RSB delta           = advance delta − right edge movement
```

This spacing rule preserves the **sum** of the two sidebearings for a spacing glyph. Each outline stays in its authored coordinate system, so the individual bearings can change. For isolated alif, the ink grows 12 units on each side.

| Weight | Shaped advance of **ا** | Left sidebearing | Right sidebearing |
| --- | ---: | ---: | ---: |
| 400 | 146 | 0 | 1 |
| 550 | 158 | −6 | 7 |
| 700 | 170 | −12 | 13 |

GDEF marks keep their authored advances, including a mark designed with a positive advance. Zero-advance glyphs stay at zero. Blank glyphs such as space have no ink growth. With `--fixed-advances`, the advance delta is zero for every glyph and the same sidebearing formulas apply.

### What happens at a cursive join

OpenType’s `curs` lookup aligns one glyph’s exit anchor with the next glyph’s entry anchor. The builder takes those anchors from Regular. The visible connection also depends on the upper and lower edges of the ink, their tangent directions, and the overlap between the glyphs.

In New Madina’s **مب**, the mīm exit is locally at `(0, 0)` and the bāʾ entry at `(849, 0)`. Shaping brings them to the same point in the word. Measured from that shared point, both outlines meet at the following coordinates, rounded to font units.

| Weight | Lower edge | Upper edge | Join height |
| --- | --- | --- | --- |
| 400 | (0, 0) | (16, 88) | 88 |
| 550 | (0, −5) | (16, 93) | 98 |
| 700 | (0, −10) | (16, 98) | 108 |

Both edges have horizontal tangents here. The fitter processes each glyph independently, so other joining forms, especially forms with different strength overrides, need visual proofing. When correcting a pair, give both letters matching edge positions relative to their anchors, match the tangent directions, and retain their intended overlap. A future `wdth` master that moves a joining end would also need to carry its anchor with it. [Inspect the mīm–bāʾ join](../fonts/portable/development/viewer/examples/mim-ba-join.html)

The shaped advances of **مب** are 1,216, 1,228, and 1,240 units at weights 400, 550, and 700. Both letters gain nominal advance, and the cursive lookup sets their relative positions so their anchors meet. In both HarfBuzz’s OpenType and CoreText backends, mīm’s origin stays 849 units to the right of bāʾ’s origin for this pair.

## OpenType tables, mark anchors, and kerning

The cubic master generator updates the outlines, metrics, axis and family names. The builder then applies the measured positioning adjustments and converts the result to TrueType. [Spacing rules](spacing.md)

| Tables | What the weight builder does |
| --- | --- |
| `CFF2`, `fvar`, `STAT`, `name` | Writes Bézier deltas, declares the weight axis and its instances, and names the Portable family. |
| `hmtx`, `HVAR` | Retains the source metrics at 400. Stores an advance delta for each spacing glyph whose ink width changes and left and right sidebearing deltas for the new bounds. |
| `cmap`, `GSUB`, `GDEF`, `GPOS` | Preserves the source character mapping and carries forward the source layout. Applies measured GPOS adjustments with a GDEF variation store and adds a contextual glyph switch in Old Madina. The UFO override reader imports contours only. |

The cubic intermediate reserializes `post` with the same glyph names and order and updates the `head` checksum.

**Mark attachment.** For **اَ** (alif with fatḥa), the `mark` lookup places the mark origin at the base anchor minus the mark’s own anchor. Here that gives `(32, 810) − (116, 1) = (−84, 809)` at every weight. The vertical gap between the top of the alif’s bounding box and the bottom of the positioned fatḥa’s bounding box measures 25.8 units at 400, 18.6 at 550, and 11.3 at 700. The `--mark-scale` option controls the mark’s ink. A separate positioning decision controls this gap. The same two-anchor calculation applies to `mkmk`, where the receiving glyph is another mark, such as shadda.

**Kerning.** In New Madina, `kern` is a feature tag inside `GPOS`. Four contextual lookups implement it, including rules that act on sequences with marks. For example, in **إِ**, alif followed by hamza below and kasra triggers a 50-unit increase in alif’s advance. At weights 400, 550, and 700 the shaped advances are 146, 158, and 170 with `kern` disabled, or 196, 208, and 220 with it enabled. The source rule is in `madinafont/features.fea` in the workspace.

### Varying positioning across masters

For a production weight axis, set Bold targets for the receiving anchor and the mark’s own anchor wherever the ink needs different clearance. Review mark-to-base and mark-to-mark attachments separately. For kerning, keep the existing context and adjust the placement or advance value that it invokes. A `wdth` master can also change each glyph’s intrinsic advance through `HVAR` and move its attachment anchors with the outlines.

OpenType encodes moving X or Y anchors in GPOS Anchor Format 3. Kerning deltas attach to a GPOS positioning value. Both reference an Item Variation Store in `GDEF`. DigitalKhatt’s native exporter already has a variable anchor writer for its source axes. [GPOS anchors and positioning values](https://learn.microsoft.com/en-us/typography/opentype/spec/gpos) · [GDEF variation store](https://learn.microsoft.com/en-us/typography/opentype/spec/gdef)

In a temporary copy of the fixed-advance proof, the Bold master raised the alif’s receiving anchor by 14 units and added 10 units to the contextual kerning value above. HarfBuzz’s OpenType and CoreText backends both produced the values below. The portable build keeps the authored attachment anchors and applies measured contextual adjustments after attachment. See the [within-line clearance check](validation.md#within-line-clearance-in-the-portable-fonts).

| Measurement in the temporary font (font units) | 400 | 550 | 700 |
| --- | ---: | ---: | ---: |
| Fatḥa Y offset relative to alif in **اَ** | 809 | 816 | 823 |
| Extra advance from `kern` in **إِ** | 50 | 55 | 60 |

With authored layout deltas, compare weight 400 with the original corpus traces. At intermediate weights and Bold, check the intended placement and advance changes as well as the visible spacing.

## From cubic masters to TrueType

1. **Export portable Regular.** The native exporter opens each family's own `.mp` project with `--profile portable`. The three current profiles produce unhinted static CFF2 outlines. This export defines the source glyph IDs, character map, mark classes, substitutions, cursive joins, anchors and kerning.
2. **Generate Bold in cubic space.** At weight 700 an unconstrained vertical exterior edge moves 12 units in X, and a horizontal edge moves 10 in Y, per 1,000 em. Marks use 45% strength and counters use 50% of the glyph's strength. The fitter accounts for corners, short spans and measured joining edges. The portable recipe keeps `endofaya*` verse-ending ornaments at their authored weight. Use `--scale 'endofaya*=1'` to include them, or `--font madina --overrides corrected-bold.ufo` for hand-edited compatible Béziers. [Bolding geometry](#how-the-bolding-works)
3. **Apply the portable spacing corrections.** The builder adds contextual mark placement and the measured base-pair kerning to the cubic reference. Both quadratic outputs inherit that positioning. [Within-line clearance](spacing.md)
4. **Convert Regular and Bold together.** FontTools chooses one quadratic subdivision for both versions of each cubic. The default fitting tolerance is 0.5 units per 1,000 em before rounding to the TrueType integer grid. The flat reference TTF stores the Regular points in `glyf` and compatible point deltas in `gvar`. Use `--quadratic-error` to trade outline size against fitting precision. [FontTools cu2qu](https://fonttools.readthedocs.io/en/latest/cu2qu/index.html)
5. **Share repeated contours.** The builder compares integer on-curve and off-curve points in contour order at **both** masters. A copy can refer to a base glyph when the same translation aligns both versions. If a contour recurs inside several glyphs, an unencoded single-contour helper can hold its outline and weight variation. A multi-contour glyph becomes a composite only if every contour has a reusable base. Helpers are appended after the source glyph IDs. Each composite retains its own advance and positioning rules. [TrueType conversion and sharing](../fonts/portable/truetype_weight.py)
6. **Pack GSUB and GPOS with TTX.** TTX dumps those two tables to XML and merges them back into the shared TTF. Recompilation lets repeated layout data share binary offsets. The output is an ordinary uncompressed SFNT `.ttf` file. The builder re-dumps the packed layout and requires its decoded XML to match the original layout dump. [FontTools TTX](https://fonttools.readthedocs.io/en/latest/ttx.html)
7. **Encode WOFF2.** The standalone builder and the native build with `--woff2` read the WOFF2 back and compare its layout tables, outlines and advances with the TTF. [Output checks](../fonts/portable/font_checks.py)

**Advances and sidebearings.** Regular uses the portable source advances. By default a positive-width spacing glyph gains the change in its cubic ink width at Bold. Marks and zero-width glyphs retain their advance. For a fixed-width Mushaf proof, pass `--fixed-advances`. The TTF supplies interpolated advances in both `HVAR` and the `gvar` phantom points. Its default left sidebearing equals the quadratic bounding box's `xMin`. At other weights the sidebearings follow the actual instanced bounds, including when a different point becomes the leftmost or rightmost. Rounding and quadratic control bounds can give a slightly different numeric sidebearing from the cubic source. [OpenType horizontal metrics](https://learn.microsoft.com/en-us/typography/opentype/spec/hmtx)

## Edit individual Bold glyphs

From the `typesetting` checkout, build with `--keep-intermediates` and export the glyphs you want to edit from the saved cubic reference. The new UFO contains Bold in its default layer and Regular in a second layer for comparison.

```sh
python3 -m fonts.portable.export_bold_ufo \
  output/fonts/madina/intermediates/weight-reference.otf \
  output/CorrectedBold.ufo --glyph meem.init
```

Edit the Bold layer in a UFO editor. Keep the contour count, start points, segment types, point order, and units per em. The pipeline imports its outlines and computes metrics from the new ink bounds.

```sh
python3 -m fonts.portable.development.build_portable --font madina \
  --overrides output/CorrectedBold.ufo --check-shaping --keep-intermediates \
  --output output/corrected/fonts
```

Check contours at intermediate weights and inspect marks and cursive joins in context after applying edits. [Validation commands](validation.md)
