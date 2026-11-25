cmake_minimum_required(VERSION 3.24)
project(DigitalKhattRegularExport LANGUAGES C CXX)

find_package(FLEX REQUIRED)
file(REAL_PATH "${FLEX_EXECUTABLE}" flex_executable)
get_filename_component(flex_bin "${flex_executable}" DIRECTORY)
find_path(FLEX_INCLUDE_DIR FlexLexer.h HINTS "${flex_bin}/../include" REQUIRED)

set(DIGITALKHATT_BUILD_GUI OFF)
set(BUILD_TESTING OFF)
set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "$<1:${CMAKE_BINARY_DIR}/bin>")
add_subdirectory("${DIGITALKHATT_SOURCES}/visualmetafont" visualmetafont)

foreach(family IN ITEMS madina oldmadina indopak)
  set(project_dir "${DIGITALKHATT_SOURCES}/${family}font")
  # The exporter loads each font plugin beside its MetaPost project.
  set(CMAKE_LIBRARY_OUTPUT_DIRECTORY "$<1:${project_dir}>")
  set(CMAKE_RUNTIME_OUTPUT_DIRECTORY "$<1:${project_dir}>")
  add_subdirectory("${project_dir}" "${family}font")
endforeach()
