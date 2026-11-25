"use strict";

const byId = (id) => document.getElementById(id);
const grid = byId("grid");
const slider = byId("weight");
const detailSlider = byId("detail-weight");
const dialog = byId("detail");
const formatter = new Intl.NumberFormat("en", { maximumFractionDigits: 5 });
const families = byId("families");
const rootStyle = getComputedStyle(document.documentElement);
const colors = Object.fromEntries(["text", "lost", "added", "shared"].map(
  (name) => [name, rootStyle.getPropertyValue(`--${name}`).trim()],
));

let corpus;
let requested;
let weight = Number(slider.value);
let glyphById = new Map();
let selected;
let scheduled = false;
const dataCache = new Map();
const fonts = new Map();
const visible = new Set();

const observer = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (entry.isIntersecting) visible.add(entry.target);
    else visible.delete(entry.target);
  }
  schedulePaint();
}, { rootMargin: "12px" });

async function loadJson(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Could not read ${url} (HTTP ${response.status}).`);
  return response.json();
}

function normalizedWeight(value) {
  const { min, default: regular, max, map } = corpus.axis;
  const n = value === regular ? 0 : (value - regular) / (value < regular ? regular - min : max - regular);
  const upper = map.findIndex(([x]) => n <= x);
  if (upper <= 0) return map[upper === 0 ? 0 : map.length - 1][1];
  const [x0, y0] = map[upper - 1];
  const [x1, y1] = map[upper];
  return y0 + (n - x0) / (x1 - x0) * (y1 - y0);
}

function masterPosition(value) {
  const n = normalizedWeight(value);
  const { knots } = corpus;
  const upper = knots.findIndex((knot) => n <= knot);
  if (upper <= 0) {
    const end = upper === 0 ? 0 : knots.length - 1;
    return [end, end, 0];
  }
  const lower = upper - 1;
  return [lower, upper, (n - knots[lower]) / (knots[upper] - knots[lower])];
}

function outline(glyph, [low, high, fraction]) {
  const path = new Path2D();
  const a = glyph.masters[low] ?? glyph.masters[0];
  const b = glyph.masters[high] ?? glyph.masters[0];
  let index = 0;
  const coordinate = () => {
    const start = a[index];
    return start + (b[index++] - start) * fraction;
  };
  for (const operation of glyph.commands) {
    switch (operation) {
      case "M": path.moveTo(coordinate(), coordinate()); break;
      case "L": path.lineTo(coordinate(), coordinate()); break;
      case "Q": path.quadraticCurveTo(coordinate(), coordinate(), coordinate(), coordinate()); break;
      case "C": path.bezierCurveTo(coordinate(), coordinate(), coordinate(), coordinate(),
        coordinate(), coordinate()); break;
      case "Z": path.closePath(); break;
    }
  }
  return path;
}

function paint(canvas, glyph, position, regular = null, scaleBasis = null) {
  const box = canvas.getBoundingClientRect();
  const width = Math.round(box.width);
  const height = Math.round(box.height);
  if (!width || !height || !glyph.bounds) return;
  const dpi = Math.min(window.devicePixelRatio || 1, 2);
  const pixelsWide = Math.round(width * dpi);
  const pixelsHigh = Math.round(height * dpi);
  if (canvas.width !== pixelsWide) canvas.width = pixelsWide;
  if (canvas.height !== pixelsHigh) canvas.height = pixelsHigh;
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpi, 0, 0, dpi, 0, 0);
  ctx.clearRect(0, 0, width, height);
  const [left, bottom, right, top] = glyph.bounds;
  const fitWidth = scaleBasis ? Math.min(width, scaleBasis.width) : width;
  const fitHeight = scaleBasis ? Math.min(height, scaleBasis.height) : height;
  // Match the em scale across tiles and fit wide forms within their tiles.
  const scale = Math.min(fitHeight * 0.83 / corpus.em, (fitWidth - 12) / Math.max(right - left, 1),
    (fitHeight - 10) / Math.max(top - bottom, 1));
  if (scale <= 0) return;
  ctx.translate((width - (left + right) * scale) / 2, (height + (top + bottom) * scale) / 2);
  ctx.scale(scale, -scale);
  const current = outline(glyph, position);
  if (regular) {
    const original = outline(glyph, regular);
    ctx.fillStyle = colors.lost;
    ctx.fill(original);
    ctx.fillStyle = colors.added;
    ctx.fill(current);
    ctx.save();
    ctx.clip(current);
    ctx.fillStyle = colors.shared;
    ctx.fill(original);
    ctx.restore();
  } else {
    ctx.fillStyle = colors.text;
    ctx.fill(current);
  }
}

function schedulePaint() {
  if (scheduled) return;
  scheduled = true;
  requestAnimationFrame(() => {
    scheduled = false;
    if (!corpus) return;
    const position = masterPosition(weight);
    const regular = masterPosition(corpus.axis.default);
    const compare = byId("overlay").checked;
    for (const canvas of visible) {
      const glyph = glyphById.get(Number(canvas.closest("button").dataset.gid));
      if (glyph) paint(canvas, glyph, position, compare ? regular : null);
    }
    if (selected && dialog.open && selected.bounds) {
      const first = byId("detail-regular");
      const second = byId("detail-selected");
      paint(first, selected, regular, null, second.getBoundingClientRect());
      paint(second, selected, position, null, first.getBoundingClientRect());
    }
  });
}

function frequency(glyph) {
  return Math.max(...glyph.counts);
}

function renderGrid() {
  if (!corpus) return;
  const filter = byId("search").value.trim().toLowerCase();
  const id = /^(?:gid\s*:?\s*)?(\d+)$/.exec(filter);
  const kind = byId("kind").value;
  const changedOnly = byId("changed").checked;
  const glyphs = corpus.glyphs.filter((glyph) =>
    (!filter || (id ? glyph.id === Number(id[1]) : glyph.name.toLowerCase().includes(filter))) &&
    (kind === "all" || (kind === "empty" ? !glyph.bounds : glyph.kind === kind)) &&
    (!changedOnly || glyph.changed));
  const sort = byId("sort").value;
  if (sort === "name") glyphs.sort((a, b) => a.name.localeCompare(b.name) || a.id - b.id);
  if (sort === "frequency") glyphs.sort((a, b) => frequency(b) - frequency(a) || a.id - b.id);

  observer.disconnect();
  visible.clear();
  const cards = document.createDocumentFragment();
  const template = byId("card");
  for (const glyph of glyphs) {
    const card = template.content.firstElementChild.cloneNode(true);
    card.dataset.gid = glyph.id;
    card.title = glyph.name;
    card.querySelector(".glyph-name").textContent = glyph.name;
    card.querySelector(".glyph-meta").textContent = `GID ${glyph.id} · ${glyph.bounds ? glyph.kind : "Empty"}`;
    card.querySelector(".no-ink").hidden = Boolean(glyph.bounds);
    if (glyph.bounds) observer.observe(card.querySelector("canvas"));
    cards.append(card);
  }
  grid.replaceChildren(cards);
  byId("empty").hidden = glyphs.length > 0;
  const count = formatter.format(glyphs.length);
  const total = formatter.format(corpus.glyphs.length);
  byId("summary").textContent = count === total ? `${total} used glyphs` : `${count} of ${total} used glyphs`;
  byId("corpus-info").textContent = `${formatter.format(corpus.corpus.lines)} corpus lines · ${corpus.corpus.layout}`;
  schedulePaint();
}

function loadFont() {
  const name = `GlyphReview_${corpus.id}`;
  if (!fonts.has(corpus.id)) {
    const face = new FontFace(name, `url(${corpus.font})`,
      { weight: `${corpus.axis.min} ${corpus.axis.max}` });
    document.fonts.add(face);
    fonts.set(corpus.id, face.load());
  }
  byId("example-text").style.fontFamily = name;
  return fonts.get(corpus.id);
}

function showExample() {
  const seen = selected.examples.reduce((nearest, next) =>
    Math.abs(corpus.weights[next[0]] - weight) < Math.abs(corpus.weights[nearest[0]] - weight) ? next : nearest);
  const [sample, index] = seen;
  const line = corpus.lines[index];
  const exampleWeight = corpus.weights[sample];
  const count = formatter.format(selected.counts[sample]);
  byId("example-location").textContent =
    `Page ${line.page}, line ${line.line} · ${count} uses at weight ${formatter.format(exampleWeight)}`;
  const paragraph = byId("example-text");
  paragraph.textContent = line.text;
  paragraph.style.fontWeight = exampleWeight;
  paragraph.style.fontFeatureSettings = `"tjwd" ${corpus.corpus.tajweed ? 1 : 0}`;
}

function updateWeight(value) {
  weight = Math.max(corpus.axis.min, Math.min(corpus.axis.max, Number(value)));
  slider.value = detailSlider.value = weight;
  byId("weight-value").textContent = byId("detail-value").textContent = formatter.format(weight);
  byId("selected-label").textContent = `Selected · ${formatter.format(weight)}`;
  for (const button of byId("presets").children) {
    button.setAttribute("aria-pressed", Number(button.value) === weight);
  }
  if (selected && dialog.open) showExample();
  schedulePaint();
}

function renderPresets() {
  const { min, max, default: regular } = corpus.axis;
  for (const input of [slider, detailSlider]) {
    input.min = min;
    input.max = max;
  }
  byId("presets").replaceChildren();
  for (const value of [...new Set([min, regular, Math.round((min + max) / 2), max])].sort((a, b) => a - b)) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = formatter.format(value);
    button.value = value;
    button.addEventListener("click", () => updateWeight(value));
    byId("presets").append(button);
  }
  updateWeight(weight);
}

async function chooseFamily(family) {
  requested = family.id;
  byId("error").hidden = true;
  byId("summary").textContent = `Loading ${family.label}…`;
  grid.setAttribute("aria-busy", "true");
  try {
    if (!dataCache.has(family.id)) dataCache.set(family.id, loadJson(family.data));
    const next = await dataCache.get(family.id);
    if (requested !== family.id) return;
    corpus = next;
    glyphById = new Map(corpus.glyphs.map((glyph) => [glyph.id, glyph]));
    for (const button of families.children) button.setAttribute("aria-pressed", button.value === corpus.id);
    renderPresets();
    renderGrid();
    window.scrollTo(0, 0);
  } catch (error) {
    dataCache.delete(family.id);
    if (requested === family.id) showError(error);
  } finally {
    if (requested === family.id) grid.removeAttribute("aria-busy");
  }
}

function showError(error) {
  byId("error").textContent = error.message;
  byId("error").hidden = false;
  if (corpus) renderGrid();
  else byId("summary").textContent = "Glyph data unavailable";
}

grid.addEventListener("click", (event) => {
  const card = event.target.closest("button[data-gid]");
  if (!card) return;
  selected = glyphById.get(Number(card.dataset.gid));
  byId("detail-title").textContent = selected.name;
  byId("detail-meta").textContent = `${corpus.label} · GID ${selected.id} · ${selected.kind}`;
  byId("detail-empty").hidden = Boolean(selected.bounds);
  byId("detail-shapes").hidden = !selected.bounds;
  byId("regular-label").textContent = `Regular · ${formatter.format(corpus.axis.default)}`;
  dialog.showModal();
  updateWeight(weight);
  loadFont().catch(() => { byId("example-text").title = "The corpus font could not be loaded."; });
});
dialog.addEventListener("close", () => { selected = undefined; });
for (const input of [slider, detailSlider]) {
  input.addEventListener("input", () => { if (corpus) updateWeight(input.value); });
}
for (const id of ["search", "kind", "sort", "changed"]) byId(id).addEventListener("input", renderGrid);
byId("overlay").addEventListener("change", (event) => {
  byId("legend").hidden = !event.target.checked;
  schedulePaint();
});
byId("density").addEventListener("change", (event) => {
  grid.dataset.density = event.target.value;
  schedulePaint();
});
window.addEventListener("resize", schedulePaint);

loadJson("manifest.json").then(({ families: entries }) => {
  for (const family of entries) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = family.label;
    button.value = family.id;
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => chooseFamily(family));
    families.append(button);
  }
  return chooseFamily(entries[0]);
}).catch(showError);
