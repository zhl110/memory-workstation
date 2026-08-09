cmake_minimum_required(VERSION 3.15)
project(mw_core LANGUAGES C CXX)

set(CMAKE_BUILD_TYPE Release)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# UTF-8 源码编码
add_compile_options(/utf-8)

# pybind11 — skip internal find_package Python by using our vars
find_package(pybind11 CONFIG REQUIRED)

# Force release-only Python lib (prevent python313_d.lib lookup)
# 注意：使用 D:\py313 junction 别名（指向真实 Python 安装目录），
# 避免 link.exe 读取含中文路径(C:\Users\周海龙\...)的响应文件乱码导致 Unknown system error -1。
# 此 set 必须在 find_package(pybind11) 之后，否则会被 FindPython 覆盖。
set(Python_LIBRARY_RELEASE "D:/py313/libs/python313.lib")
set(Python_LIBRARY_DEBUG "D:/py313/libs/python313.lib")
set(Python_LIBRARIES "D:/py313/libs/python313.lib")

message(STATUS "Python_LIBRARIES=${Python_LIBRARIES}")
message(STATUS "Python_LIBRARY_RELEASE=${Python_LIBRARY_RELEASE}")
message(STATUS "Python_LIBRARY_DEBUG=${Python_LIBRARY_DEBUG}")

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
