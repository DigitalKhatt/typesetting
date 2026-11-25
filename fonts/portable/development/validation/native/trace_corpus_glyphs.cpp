// Read one final native feature plan per line. Shape the same UTF-16 text in
// stock HarfBuzz, preserving the cluster coordinate system of ranged features.
#include <hb-ot.h>
#include <hb.h>

#include <algorithm>
#include <charconv>
#include <cmath>
#include <cstdint>
#include <fstream>
#include <iostream>
#include <sstream>
#include <stdexcept>
#include <string>
#include <string_view>
#include <vector>

struct Usage {
  uint64_t occurrences = 0;
  unsigned firstLine = 0;
  bool atGsubStart = false;
  bool atGsubBoundary = false;
};

struct Trace {
  std::vector<Usage> glyphs;
  bool inGsub = false;
};

static hb_bool_t message(hb_buffer_t* buffer, hb_font_t*, const char* raw, void* data) {
  auto& trace = *static_cast<Trace*>(data);
  const std::string_view msg(raw);
  const bool start = msg.starts_with("start table GSUB");
  if (start) trace.inGsub = true;
  if (!trace.inGsub) return true;
  const bool end = msg.starts_with("end table GSUB");
  if (start || end || msg.starts_with("end lookup")) {
    unsigned count;
    auto* info = hb_buffer_get_glyph_infos(buffer, &count);
    for (unsigned i = 0; i < count; ++i) {
      auto& glyph = trace.glyphs.at(info[i].codepoint);
      glyph.atGsubBoundary = true;
      if (start) glyph.atGsubStart = true;
    }
  }
  if (end) trace.inGsub = false;
  return true;
}

static hb_feature_t feature(const std::string& text) {
  hb_feature_t result;
  if (!hb_feature_from_string(text.c_str(), int(text.size()), &result))
    throw std::runtime_error("Invalid HarfBuzz feature " + text);
  return result;
}

int main(int argc, char** argv) {
  const std::string_view option = argc > 5 ? argv[5] : "";
  const bool records = (argc == 6 || argc == 7) &&
                       (option == "--records" || option.starts_with("--records="));
  if (argc != 5 && !records) {
    std::cerr << "Usage: trace_corpus_glyphs FONT ENGINE default|layout|FEATURE SIZE [--records[=WEIGHT] [GID-MAP]] < layout-features.tsv\n";
    return 2;
  }
  try {
    std::string mode = argv[3];
    hb_blob_t* blob = hb_blob_create_from_file_or_fail(argv[1]);
    if (!blob) throw std::runtime_error("Cannot open input TTF");
    hb_face_t* face = hb_face_create(blob, 0);
    hb_blob_destroy(blob);
    unsigned glyphCount = hb_face_get_glyph_count(face);
    std::vector<unsigned> ids;
    if (argc == 7) {
      std::ifstream map(argv[6]);
      unsigned id;
      while (map >> id) ids.push_back(id);
      if (ids.size() != glyphCount) throw std::runtime_error("Incomplete canonical glyph ID map");
    }
    Trace trace{std::vector<Usage>(glyphCount)};
    const char* shapers[] = {argv[2], nullptr};
    const bool full = mode == "layout" || mode == "default";
    float recordWeight = 400;
    if (option.starts_with("--records=")) {
      const auto value = option.substr(std::string_view("--records=").size());
      size_t consumed = 0;
      recordWeight = std::stof(std::string(value), &consumed);
      if (consumed != value.size() || !std::isfinite(recordWeight))
        throw std::runtime_error("Invalid record weight");
    }
    const std::vector<float> weights = full && !records ? std::vector<float>{400, 550, 700} : std::vector<float>{float(recordWeight)};
    std::vector<hb_font_t*> fonts;
    for (float weight : weights) {
      auto* font = hb_font_create(face);
      hb_ot_font_set_funcs(font);
      unsigned size = std::stoul(argv[4]);
      hb_font_set_scale(font, size, size);
      hb_variation_t variation{HB_TAG('w','g','h','t'), weight};
      hb_font_set_variations(font, &variation, 1);
      fonts.push_back(font);
    }
    auto* buffer = hb_buffer_create();
    std::string line;
    unsigned lines = 0;
    while (std::getline(std::cin, line)) {
      ++lines;
      const auto split = line.find('\t');
      const auto next = line.find('\t', split + 1);
      if (split == std::string::npos || next == std::string::npos || split % 4)
        throw std::runtime_error("Malformed line in feature plan");
      std::vector<uint16_t> text;
      for (size_t j = 0; j < split; j += 4) {
        uint16_t code = 0;
        auto read = std::from_chars(line.data() + j, line.data() + j + 4, code, 16);
        if (read.ec != std::errc() || read.ptr != line.data() + j + 4)
          throw std::runtime_error("Malformed UTF-16 code unit");
        text.push_back(code);
      }
      std::vector<hb_feature_t> features;
      if (mode == "layout") {
        std::istringstream source(line.substr(split + 1, next - split - 1));
        std::string item;
        while (std::getline(source, item, ',')) features.push_back(feature(item));
      } else if (!full) {
        features.push_back(feature(mode));
      }
      std::vector<hb_codepoint_t> regular;
      for (size_t w = 0; w < fonts.size(); ++w) {
        hb_buffer_reset(buffer);
        hb_buffer_set_direction(buffer, HB_DIRECTION_RTL);
        hb_buffer_set_script(buffer, HB_SCRIPT_ARABIC);
        hb_buffer_set_language(buffer, hb_language_from_string("ar", -1));
        hb_buffer_set_cluster_level(buffer, HB_BUFFER_CLUSTER_LEVEL_MONOTONE_CHARACTERS);
        hb_buffer_add_utf16(buffer, text.data(), int(text.size()), 0, int(text.size()));
        bool observe = !records && w == 0 && full && std::string_view(shapers[0]) == "ot";
        hb_buffer_set_message_func(buffer, observe ? message : nullptr, &trace, nullptr);
        if (!hb_shape_full(fonts[w], buffer, features.data(), unsigned(features.size()), shapers))
          throw std::runtime_error("Shaper unavailable");
        unsigned n;
        const auto* infos = hb_buffer_get_glyph_infos(buffer, &n);
        if (records) {
          const auto* pos = hb_buffer_get_glyph_positions(buffer, nullptr);
          for (unsigned i = 0; i < n; ++i)
            std::cout << (i ? "|" : "") << (ids.empty() ? infos[i].codepoint : ids.at(infos[i].codepoint)) << ',' << infos[i].cluster << ','
                      << pos[i].x_advance << ',' << pos[i].y_advance << ','
                      << pos[i].x_offset << ',' << pos[i].y_offset;
          std::cout << '\n';
          continue;
        }
        if (w && n != regular.size()) throw std::runtime_error("Glyph count changed with weight");
        for (unsigned i = 0; i < n; ++i) {
          auto gid = infos[i].codepoint;
          if (w == 0) {
            auto& row = trace.glyphs.at(gid);
            ++row.occurrences;
            if (!row.firstLine) row.firstLine = lines;
            regular.push_back(gid);
          } else if (gid != regular[i]) {
            throw std::runtime_error("Glyph ID changed with weight on line " + std::to_string(lines));
          }
        }
      }
    }
    for (unsigned i = 0; !records && i < glyphCount; ++i) {
      const auto& row = trace.glyphs[i];
      if (row.occurrences || row.atGsubBoundary)
        std::cout << i << '\t' << row.occurrences << '\t' << row.firstLine << '\t'
                  << row.atGsubStart << '\t' << row.atGsubBoundary << '\n';
    }
    if (!records) std::cout << "lines\t" << lines << '\n';
    hb_buffer_destroy(buffer);
    for (auto* font : fonts) hb_font_destroy(font);
    hb_face_destroy(face);
  } catch (const std::exception& error) {
    std::cerr << error.what() << '\n';
    return 1;
  }
}
