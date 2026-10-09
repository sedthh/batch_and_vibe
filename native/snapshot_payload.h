#pragma once
#include <string>

namespace bwv_snapshot {
// Matches the Python pipe client's bounded response size.
constexpr size_t max_bytes = 512 * 1024;

inline void bound(std::string& data) {
    if (data.size() <= max_bytes) return;
    // Publish an explicit unavailable frame instead of leaving old world data
    // cached indefinitely when the current action catalog is too large.
    data = "{\"protocol\":1,\"mode\":\"observer\",\"ready\":false,"
           "\"capabilities\":[],\"error\":\"snapshot_size_limit\"}\n";
}
}
