cmake_minimum_required(VERSION 3.15)
project(mw_core LANGUAGES C CXX)

set(CMAKE_BUILD_TYPE Release)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# UTF-8 源码编码
add_compile_options(/utf-8)

# pybind11 — skip internal find_package Python by using our vars
find_package(pybind11 CONFIG REQUIRED)
find_package(Python COMPONENTS Development REQUIRED)

# Force release-only Python lib (prevent python313_d.lib lookup)
# Uses FindPython resolution instead of hardcoded install paths.
# Python_EXECUTABLE is passed from build.bat/build.ps1.
set(Python_LIBRARY_RELEASE "${Python_LIBRARY_RELEASE}")
set(Python_LIBRARY_DEBUG "${Python_LIBRARY_RELEASE}")
set(Python_LIBRARIES "${Python_LIBRARY_RELEASE}")

message(STATUS "Python_EXECUTABLE=${Python_EXECUTABLE}")
message(STATUS "Python_LIBRARY_RELEASE=${Python_LIBRARY_RELEASE}")

# SQLite3 amalgamation (直接编译源码，无需系统安装)
add_library(sqlite3 STATIC
    third_party/sqlite3/sqlite3.c
)
target_compile_options(sqlite3 PRIVATE /utf-8)
target_include_directories(sqlite3 PUBLIC third_party/sqlite3)
target_compile_definitions(sqlite3 PRIVATE
    SQLITE_DQS=0
    SQLITE_THREADSAFE=1
    SQLITE_ENABLE_FTS5
)

# MW Core 模块
pybind11_add_module(mw_core
    src/binding.cpp
    src/storage.cpp
    src/storage_search.cpp
    src/storage_ingest.cpp
    src/storage_evolution.cpp
    src/search_engine.cpp
    src/graph_engine.cpp
    src/rules.cpp
    src/hnsw_index.cpp
    src/embedding_engine.cpp
)

target_include_directories(mw_core PRIVATE include)
target_include_directories(mw_core PRIVATE third_party/onnxruntime/include)

# ONNX Runtime 编译时链接 .lib，运行时找同目录的 .dll
target_link_libraries(mw_core PRIVATE sqlite3)
target_link_libraries(mw_core PRIVATE "${CMAKE_CURRENT_SOURCE_DIR}/third_party/onnxruntime/lib/onnxruntime.lib")

# 把 onnxruntime.dll 拷贝到 .pyd 同目录
add_custom_command(TARGET mw_core POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E copy_if_different
        "${CMAKE_CURRENT_SOURCE_DIR}/third_party/onnxruntime/lib/onnxruntime.dll"
        "$<TARGET_FILE_DIR:mw_core>/onnxruntime.dll"
)
