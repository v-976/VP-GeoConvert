cmake_minimum_required(VERSION 3.21)

project(pdfium_probe
        VERSION 0.1.0
        DESCRIPTION "VP GeoConvert PDFium vector extraction proof of concept"
        LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# PDFium is consumed as an external, prebuilt, permissively licensed package.
# It is deliberately NOT vendored into this repository: AGENTS.md forbids
# committing third-party binary packages.
#
# Pass the location of the package folder, i.e. the directory that contains
# PDFiumConfig.cmake:
#
#   cmake -S . -B build -DPDFium_DIR=C:/Sandbox/pdfium-deps/pdfium
#
# Exact version, provenance and licence audit: see README.md.
find_package(PDFium REQUIRED)

add_executable(pdfium_probe src/main.cpp)

target_link_libraries(pdfium_probe PRIVATE pdfium)

# Keep the probe runnable straight from the build tree. pdfium.dll links against
# only Windows system DLLs (kernel32, user32, gdi32, advapi32) because its CRT is
# statically linked, so no redistributable is required.
add_custom_command(TARGET pdfium_probe POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
            $<TARGET_FILE:pdfium>
            $<TARGET_FILE_DIR:pdfium_probe>
    COMMENT "Copying pdfium.dll next to pdfium_probe.exe")