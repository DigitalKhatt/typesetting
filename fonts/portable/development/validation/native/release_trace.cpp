// The repository's HarfBuzz emits detailed GPOS messages, including the two
// actual buffer indices. Observe them without altering lookup execution.
#include <hb-ot.h>
#include <hb.h>
#include <algorithm>
#include <cstdio>
#include <iostream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

struct Link { int parent = -1, entry = -1, lookup = -1; };
struct Trace {
  bool gpos = false;
  std::vector<Link> links;
  std::vector<int> lookups;
};

static hb_bool_t message(hb_buffer_t* buffer, hb_font_t*, const char* text, void* data) {
  auto& trace = *static_cast<Trace*>(data);
  std::string_view s(text);
  if (s.starts_with("start table GPOS")) {
    trace.gpos = true;
    trace.links.assign(hb_buffer_get_length(buffer), {});
  }
  if (!trace.gpos) return true;
  int a, b;
  if (s.starts_with("end table GPOS")) trace.gpos = false;
  else if (s.starts_with("start lookup ") && std::sscanf(text, "start lookup %d", &a) == 1)
    trace.lookups.assign(1, a);
  else if (s.starts_with("recursing to lookup ") && std::sscanf(text, "recursing to lookup %d", &a) == 1)
    trace.lookups.push_back(a);
  else if (s.starts_with("recursed to lookup ") && trace.lookups.size() > 1) trace.lookups.pop_back();
  else if (s.starts_with("attached mark ") && std::sscanf(text, "attached mark glyph at %d to glyph at %d", &a, &b) == 2)
    trace.links.at(a).parent = b;
  else if (s.starts_with("cursive attached ") && std::sscanf(text, "cursive attached glyph at %d to glyph at %d", &a, &b) == 2) {
    auto& link = trace.links.at(a);
    link.entry = b;
    link.lookup = trace.lookups.back();
  }
  return true;
}

int main(int argc, char** argv) {
  if (argc != 4) return 2;  // FONT WEIGHT TAJWEED; original UTF-8 lines on stdin
  try {
    auto* blob = hb_blob_create_from_file_or_fail(argv[1]);
    if (!blob) throw std::runtime_error("Cannot open trace font");
    auto* face = hb_face_create(blob, 0);
    auto* font = hb_font_create(face);
    hb_ot_font_set_funcs(font);
    hb_font_set_scale(font, hb_face_get_upem(face), hb_face_get_upem(face));
    hb_variation_t weight = {HB_TAG('w','g','h','t'), std::stof(argv[2])};
    hb_font_set_variations(font, &weight, 1);
    hb_feature_t tjwd = {HB_TAG('t','j','w','d'), unsigned(std::stoi(argv[3])), 0, HB_FEATURE_GLOBAL_END};
    const char* shapers[] = {"ot", nullptr};
    auto* buffer = hb_buffer_create();
    std::string line;
    while (std::getline(std::cin, line)) {
      Trace trace;
      hb_buffer_reset(buffer);
      hb_buffer_set_direction(buffer, HB_DIRECTION_RTL);
      hb_buffer_set_script(buffer, HB_SCRIPT_ARABIC);
      hb_buffer_set_language(buffer, hb_language_from_string("ar", -1));
      hb_buffer_set_cluster_level(buffer, HB_BUFFER_CLUSTER_LEVEL_MONOTONE_CHARACTERS);
      hb_buffer_add_utf8(buffer, line.data(), int(line.size()), 0, int(line.size()));
      hb_buffer_set_message_func(buffer, message, &trace, nullptr);
      if (!hb_shape_full(font, buffer, &tjwd, 1, shapers)) throw std::runtime_error("OpenType shaping failed");
      unsigned size;
      const auto* glyph = hb_buffer_get_glyph_infos(buffer, &size);
      const auto* pos = hb_buffer_get_glyph_positions(buffer, nullptr);
      if (trace.links.size() != size) throw std::runtime_error("GPOS and final glyph counts differ");
      int x = 0, y = 0;
      for (unsigned i = 0; i < size; ++i) {
        unsigned v = size-1-i;
        x -= pos[v].x_advance;
        y -= pos[v].y_advance;
        const auto& link = trace.links[i];
        std::cout << (i ? "|" : "") << glyph[v].codepoint << ',' << glyph[v].cluster << ',' << pos[v].x_advance
                  << ',' << x + pos[v].x_offset << ',' << y + pos[v].y_offset
                  << ',' << link.parent << ',' << link.entry << ',' << link.lookup;
      }
      std::cout << '\n';
    }
    hb_buffer_destroy(buffer);
    hb_font_destroy(font);
    hb_face_destroy(face);
    hb_blob_destroy(blob);
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 2;
  }
}
