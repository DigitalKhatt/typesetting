// Check shaped, variable TrueType ink using the GUI's Save Collision geometry.
#include <hb-ot.h>
#include "digitalkhatt/layout/CollisionReport.h"

#include <algorithm>
#include <array>
#include <charconv>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <limits>
#include <map>
#include <optional>
#include <sstream>
#include <stdexcept>
#include <string_view>
#include <tuple>
#include <utility>
#include <vector>

using namespace geometry;
using namespace digitalkhatt::layout;

static GlyphCubic& outline(void* data) { return *static_cast<GlyphCubic*>(data); }
static void moveTo(hb_draw_funcs_t*, void* data, hb_draw_state_t*, float, float, void*) {
  outline(data).paths.emplace_back();
}
static void lineTo(hb_draw_funcs_t*, void* data, hb_draw_state_t* s, float x, float y, void*) {
  Vec2 p{s->current_x, s->current_y}, q{x, y};
  outline(data).paths.back().segs.push_back({p, p, q, q});
}
static void quadraticTo(hb_draw_funcs_t*, void* data, hb_draw_state_t* s,
                        float cx, float cy, float x, float y, void*) {
  Vec2 p{s->current_x, s->current_y}, c{cx, cy}, q{x, y};
  outline(data).paths.back().segs.push_back({p, p + (c - p) * (2. / 3), q + (c - q) * (2. / 3), q});
}
static void cubicTo(hb_draw_funcs_t*, void* data, hb_draw_state_t* s,
                    float ax, float ay, float bx, float by, float x, float y, void*) {
  outline(data).paths.back().segs.push_back({{s->current_x, s->current_y}, {ax, ay}, {bx, by}, {x, y}});
}
static void closePath(hb_draw_funcs_t*, void* data, hb_draw_state_t*, void*) {
  auto& segs = outline(data).paths.back().segs;
  if (!segs.empty() && !(segs.back().p3 == segs.front().p0)) {
    Vec2 p = segs.back().p3, q = segs.front().p0;
    segs.push_back({p, p, q, q});
  }
}

struct ShapeGlyph {
  int gid, cluster;
  double advance, dyAdvance, dx, dy;
};
static std::vector<ShapeGlyph> parseRun(const std::string& line) {
  std::vector<ShapeGlyph> run;
  const char* it = line.data();
  const char* end = it + line.size();
  while (it < end) {
    std::array<double, 6> fields;
    for (size_t k = 0; k < fields.size(); ++k) {
      int integer;
      auto [next, err] = std::from_chars(it, end, integer);
      if (err == std::errc()) fields[k] = integer;
      if (k >= 2 && next < end && (*next == '.' || *next == 'e' || *next == 'E')) {
        char* tail;
        fields[k] = std::strtod(it, &tail);
        next = tail;
        if (!std::isfinite(fields[k])) throw std::runtime_error("Nonfinite shaping position");
      }
      if (err != std::errc() || (next < end && *next != (k == 5 ? '|' : ',')) ||
          (next == end && k != 5)) throw std::runtime_error("Malformed shaping record");
      it = next == end ? end : next + 1;
    }
    run.push_back({int(fields[0]), int(fields[1]), fields[2], fields[3], fields[4], fields[5]});
  }
  std::reverse(run.begin(), run.end());  // native checker takes logical RTL order
  return run;
}

// One row per glyph drawn by DirectWrite at the requested axis location.
// Coordinates are already in font units with upward-positive Y.
static std::map<int, GlyphCubic> readOutlines(const char* path, size_t glyphCount) {
  std::ifstream input(path);
  if (!input) throw std::runtime_error("Cannot open DirectWrite outlines");
  std::map<int, GlyphCubic> outlines;
  std::string line;
  while (std::getline(input, line)) {
    std::istringstream row(line);
    int gid;
    if (!(row >> gid) || gid < 0 || size_t(gid) >= glyphCount || outlines.contains(gid))
      throw std::runtime_error("Invalid DirectWrite outline glyph ID");
    auto& glyph = outlines[gid];
    auto point = [&]() {
      Vec2 p;
      if (!(row >> p.x >> p.y) || !std::isfinite(p.x) || !std::isfinite(p.y))
        throw std::runtime_error("Invalid DirectWrite outline coordinate");
      return p;
    };
    Vec2 start{}, current{};
    bool open = false;
    char op;
    while (row >> op) {
      if (op == 'M' && !open) {
        start = current = point();
        glyph.paths.emplace_back();
        open = true;
      } else if (op == 'L' && open) {
        Vec2 end = point();
        glyph.paths.back().segs.push_back({current, current, end, end});
        current = end;
      } else if (op == 'C' && open) {
        Vec2 a = point(), b = point(), end = point();
        glyph.paths.back().segs.push_back({current, a, b, end});
        current = end;
      } else if (op == 'Z' && open) {
        if (!(current == start)) glyph.paths.back().segs.push_back({current, current, start, start});
        open = false;
      } else throw std::runtime_error("Invalid DirectWrite outline command");
    }
    if (open) throw std::runtime_error("Incomplete DirectWrite contour");
  }
  return outlines;
}

// Search for the shortest local move that clears all surrounding ink. The
// report always comes from findCollisionContacts, independently of this search.
struct Move {
  int x, y;
};
static std::vector<Move> candidateMoves(int radius, int verticalLimit, int verticalCost = 1) {
  std::vector<Move> moves;
  for (int dx = -radius; dx <= radius; dx += 2)
    for (int dy = -verticalLimit; dy <= verticalLimit; dy += 2)
      if (dx * dx + dy * dy <= radius * radius) moves.push_back({dx, dy});
  std::sort(moves.begin(), moves.end(), [verticalCost](Move a, Move b) {
    return std::tuple{a.x * a.x + verticalCost * a.y * a.y, std::abs(a.x), a.y, a.x} <
           std::tuple{b.x * b.x + verticalCost * b.y * b.y, std::abs(b.x), b.y, b.x};
  });
  return moves;
}

static bool near(const AABB& a, const AABB& b, double gap) {
  return !(a.maxx + gap < b.minx || b.maxx + gap < a.minx ||
           a.maxy + gap < b.miny || b.maxy + gap < a.miny);
}

struct MarkPosition {
  Move move;
  int cost;
  AABB bounds;
  GeometrySet ink;
};

// A mark can be trapped between the base outline and a second mark. First find
// positions for each mark that clear all fixed ink, then check the pair together.
static std::vector<MarkPosition> markPositions(const std::vector<CollisionGlyph>& scene,
                                               int at, int partner, double gap) {
  constexpr int radius = 24;
  static const auto steps = candidateMoves(radius, radius);
  const auto& ink = *scene[at].geometry;
  auto bounds = ink.boundingAABB();
  std::vector<const GeometrySet*> neighbours;
  for (int i = 0; i < int(scene.size()); ++i) {
    if (i == at || i == partner || isCollisionSpace(scene[i].name) || scene[i].geometry->empty()) continue;
    if (near(bounds, scene[i].geometry->boundingAABB(), radius + gap))
      neighbours.push_back(scene[i].geometry);
  }
  std::vector<MarkPosition> positions;
  for (auto step : steps) {
    auto moved = ink.translate(step.x, step.y);
    auto box = bounds.translate(step.x, step.y);
    if (std::none_of(neighbours.begin(), neighbours.end(), [&](const GeometrySet* other) {
          return near(box, other->boundingAABB(), gap) && getDistance(moved, *other, gap).contact.depth_or_gap < gap;
        }))
      positions.push_back({step, step.x * step.x + step.y * step.y, box, std::move(moved)});
  }
  return positions;
}

static void suggestPair(const std::vector<CollisionGlyph>& scene, const std::vector<CollisionContact>& hits,
                        int line, double gap) {
  struct Pair { int at, other, left, right, cost; Move a, b; };
  std::optional<Pair> best;
  for (const auto& hit : hits) {
    for (int at : {int(hit.first), int(hit.second)}) {
      if (!scene[at].isMark) continue;
      int first = at, last = at;
      while (first > 0 && scene[first-1].isMark) --first;
      while (last + 1 < int(scene.size()) && scene[last+1].isMark) ++last;
      for (int other = first; other <= last; ++other) {
        if (other == at || scene[other].geometry->empty()) continue;
        auto positions = markPositions(scene, at, other, gap);
        if (positions.empty()) continue;
        auto partners = markPositions(scene, other, at, gap);
        for (const auto& a : positions) {
          for (const auto& b : partners) {
            int cost = a.cost + b.cost;
            if (best && cost >= best->cost) break; // Partners are ordered by displacement.
            if (near(a.bounds, b.bounds, gap) && getDistance(a.ink, b.ink, gap).contact.depth_or_gap < gap) continue;
            best = Pair{at, other, std::min(std::max(0, first-1), int(hit.first)),
                        std::max(last, int(hit.second)), cost, a.move, b.move};
            break;
          }
        }
      }
    }
  }
  if (best) {
    for (auto [at, move] : {std::pair{best->at, best->a}, std::pair{best->other, best->b}})
      if (move.x || move.y)
        std::cout << line << '\t' << at << '\t' << best->left << '\t' << best->right << '\t'
                  << move.x << '\t' << move.y << "\t0\n";
  }
}

enum class KernTarget { Isolated, Initial };
using KernTargets = std::map<std::string, KernTarget, std::less<>>;

static std::optional<KernTarget> kernTarget(std::string_view name, bool cursiveAnchor, bool entryAnchor) {
  if (entryAnchor) return std::nullopt;
  bool initial = cursiveAnchor;  // An exit-only base can start a cursive run.
  bool isolated = false;
  for (auto dot = name.find('.'); dot != name.npos; dot = name.find('.')) {
    name.remove_prefix(dot + 1);
    auto part = name.substr(0, name.find('.'));
    if (part == "medi" || part == "fina") return std::nullopt;
    if (part == "init") initial = true;
    if (part == "isol") isolated = true;
  }
  if (initial) return KernTarget::Initial;
  if (isolated) return KernTarget::Isolated;
  return std::nullopt;
}

static void suggest(const std::vector<CollisionGlyph>& scene, const std::vector<CollisionContact>& hits,
                    const KernTargets& kernTargets, double sx, int number, double target) {
  struct Neighbour {
    int index;
    const GeometrySet* ink;
    AABB bounds;
  };
  std::map<std::pair<int, bool>, std::vector<int>> groups;
  for (const auto& c : hits) {
    const bool kern = !scene[c.first].isMark && !scene[c.second].isMark;
    bool second = kern || scene[c.second].isMark;
    groups[{int(second ? c.second : c.first), kern}].push_back(int(second ? c.first : c.second));
    if (scene[c.first].isMark && scene[c.second].isMark)
      groups[{int(c.first), false}].push_back(int(c.second));
  }
  static const auto shifts = candidateMoves(300, 300);
  static const auto kernMoves = candidateMoves(300, 12, 9);
  static const auto horizontalKerns = candidateMoves(300, 0);
  struct Proposal {
    int at, left, right, dx, dy;
    bool kern;
  };
  std::map<int, Proposal> proposed;
  for (const auto& [group, others] : groups) {
    const auto [at, kern] = group;
    const auto* moves = &shifts;
    if (kern) {
      auto kind = kernTargets.find(scene[at].name);
      if (kind == kernTargets.end()) continue;
      // At a run start, XAdvance moves both sides of the outgoing join together.
      moves = kind->second == KernTarget::Initial ? &horizontalKerns : &kernMoves;
    }
    const auto& ink = *scene[at].geometry;
    auto bounds = ink.boundingAABB();
    std::vector<Neighbour> neighbours;
    int left = std::min(at, *std::min_element(others.begin(), others.end()));
    int right = std::max(at, *std::max_element(others.begin(), others.end()));
    for (int j = 0; j < int(scene.size()); ++j) {
      if (j == at || scene[j].geometry->empty() || isCollisionSpace(scene[j].name)) continue;
      if (kern && std::find(others.begin(), others.end(), j) == others.end()) continue;
      auto box = scene[j].geometry->boundingAABB();
      if (near(bounds, box, target + 300 * std::max(1., sx))) neighbours.push_back({j, scene[j].geometry, box});
    }
    GeometrySet moved;
    for (auto step : *moves) {
      double dx = step.x * sx, dy = step.y;
      auto box = bounds.translate(dx, dy);
      bool clear = true, translated = false;
      for (auto other : neighbours) {
        if (!near(box, other.bounds, target)) continue;
        if (!translated) {
          moved.assignTranslated(ink, dx, dy);
          translated = true;
        }
        if (getDistance(moved, *other.ink, target).contact.depth_or_gap < target) {
          left = std::min(left, other.index);
          right = std::max(right, other.index);
          clear = false;
          break;
        }
      }
      if (clear) {
        proposed[at] = {at, left, right, kern ? -step.x : step.x, step.y, kern};
        break;
      }
    }
  }
  std::map<int, Proposal> selected;
  for (const auto& c : hits) {
    int at = !scene[c.first].isMark || scene[c.second].isMark ? int(c.second) : int(c.first);
    if (scene[c.first].isMark && scene[c.second].isMark && proposed.count(int(c.first))) {
      const auto& alternative = proposed.at(int(c.first));
      auto cost = [](const Proposal& p) { return p.dx * p.dx + p.dy * p.dy; };
      if (!proposed.count(at) || cost(alternative) * 2 < cost(proposed.at(at))) at = int(c.first);
    }
    if (proposed.count(at)) selected[at] = proposed.at(at);
  }
  for (const auto& [at, p] : selected)
    std::cout << number << '\t' << at << '\t' << p.left << '\t' << p.right << '\t'
              << p.dx << '\t' << p.dy << '\t' << p.kern << '\n';
}

int main(int argc, char** argv) {
  bool paired = argc > 1 && std::string_view(argv[argc-1]) == "--paired-marks";
  if (paired) --argc;
  const char* outlineFile = nullptr;
  if (argc >= 8 && std::string_view(argv[argc-2]) == "--outlines") {
    outlineFile = argv[argc-1];
    argc -= 2;
  }
  if ((argc != 6 && argc != 7) || (paired && argc != 7)) {
    std::cerr << "Usage: mark_contacts FONT WEIGHT LINE_COUNT GLYPHS MIN_GAP [SUGGESTED_GAP] [--outlines FILE] [--paired-marks] < shaped-records\n";
    return 2;
  }
  try {
    hb_blob_t* blob = hb_blob_create_from_file_or_fail(argv[1]);
    if (!blob) throw std::runtime_error("Missing font");
    auto* face = hb_face_create(blob, 0);
    auto* font = hb_font_create(face);
    hb_ot_font_set_funcs(font);
    int em = hb_face_get_upem(face);
    hb_font_set_scale(font, em, em);
    hb_variation_t weight{HB_TAG('w', 'g', 'h', 't'), float(std::stod(argv[2]))};
    hb_font_set_variations(font, &weight, 1);
    auto* draw = hb_draw_funcs_create();
    hb_draw_funcs_set_move_to_func(draw, moveTo, nullptr, nullptr);
    hb_draw_funcs_set_line_to_func(draw, lineTo, nullptr, nullptr);
    hb_draw_funcs_set_quadratic_to_func(draw, quadraticTo, nullptr, nullptr);
    hb_draw_funcs_set_cubic_to_func(draw, cubicTo, nullptr, nullptr);
    hb_draw_funcs_set_close_path_func(draw, closePath, nullptr, nullptr);
    const int expectedLines = std::stoi(argv[3]);
    std::ifstream metadata(argv[4]);
    if (expectedLines < 1 || !metadata) throw std::runtime_error("Missing corpus lines or glyph metadata");
    struct Glyph {
      std::string name;
      bool mark;
      std::optional<GeometrySet> ink;
    };
    std::vector<Glyph> glyphs;
    KernTargets kernTargets;
    const bool suggestKerns = argc == 7 && !paired;
    std::string name;
    bool mark;
    while (metadata >> name >> mark) {
      std::string rest;
      std::getline(metadata, rest);
      std::istringstream fields(rest);
      bool cursiveAnchor;
      if (suggestKerns && !mark && (fields >> cursiveAnchor) &&
          hb_ot_layout_get_glyph_class(face, glyphs.size()) == HB_OT_LAYOUT_GLYPH_CLASS_BASE_GLYPH) {
        bool entryAnchor;
        // The older three-column format cannot distinguish an exit from an entry.
        if (!(fields >> entryAnchor)) entryAnchor = cursiveAnchor;
        if (auto kind = kernTarget(name, cursiveAnchor, entryAnchor)) kernTargets.emplace(name, *kind);
      }
      glyphs.push_back({name, mark, {}});
    }
    if (glyphs.size() != hb_face_get_glyph_count(face)) throw std::runtime_error("Wrong glyph list");
    std::map<int, GlyphCubic> externalOutlines;
    if (outlineFile) externalOutlines = readOutlines(outlineFile, glyphs.size());
    double gap = std::stod(argv[5]);
    std::string line;
    int number = 0;
    std::cout << std::setprecision(std::numeric_limits<double>::max_digits10);
    while (std::getline(std::cin, line)) {
      ++number;
      auto run = parseRun(line);
      std::vector<GeometrySet> world;
      world.reserve(run.size());
      std::vector<CollisionGlyph> scene;
      int word = 0;
      double x = 0, y = 0;
      for (const auto& pos : run) {
        auto& glyph = glyphs.at(pos.gid);
        if (!glyph.ink) {
          GlyphCubic path;
          if (outlineFile) path = std::move(externalOutlines.at(pos.gid));
          else hb_font_draw_glyph(font, pos.gid, draw, &path);
          std::erase_if(path.paths, [](auto& p) { return p.segs.empty(); });
          try {
            glyph.ink = buildConvexPartsFromCubics(path, CUBIC_FLATNESS_TOLERANCE);
          } catch (const std::exception& e) {
            throw std::runtime_error(glyph.name + ": " + e.what());
          }
        }
        const bool space = isCollisionSpace(glyph.name);
        x -= pos.advance;
        y -= pos.dyAdvance;
        world.push_back(glyph.ink->translate(x + pos.dx, y + pos.dy));
        scene.push_back({&world.back(), glyph.name, number, word, glyph.mark, 1});
        if (space) ++word;
      }
      auto hits = findCollisionContacts(scene, gap);
      if (argc == 7) {
        if (paired) suggestPair(scene, hits, number, std::stod(argv[6]));
        else suggest(scene, hits, kernTargets, 1, number, std::stod(argv[6]));
        continue;
      }
      for (const auto& c : hits) {
        const auto& a = run[c.first];
        const auto& b = run[c.second];
        std::cout << number << '\t' << c.first << '\t' << c.second << '\t' << a.cluster << '\t'
                  << b.cluster << '\t' << scene[c.first].name << '\t' << scene[c.second].name << '\t'
                  << scene[c.first].isMark << '\t' << scene[c.second].isMark << '\t'
                  << c.contact.depth_or_gap << '\t' << c.contact.normal.x << '\t' << c.contact.normal.y << '\n';
      }
    }
    if (number != expectedLines) throw std::runtime_error("Incomplete shaping records");
    hb_draw_funcs_destroy(draw);
    hb_font_destroy(font);
    hb_face_destroy(face);
    hb_blob_destroy(blob);
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
