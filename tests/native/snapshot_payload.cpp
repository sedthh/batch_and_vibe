#include "../../native/snapshot_payload.h"
#include "check.h"

int main() {
    // A growing catalog must keep refreshing beyond the former 64 KB limit.
    std::string data(100000, 'x');
    bwv_snapshot::bound(data);
    assert(data.size() == 100000);
    data.assign(bwv_snapshot::max_bytes, 'x');
    bwv_snapshot::bound(data);
    assert(data.size() == bwv_snapshot::max_bytes);
    data.push_back('x');
    bwv_snapshot::bound(data);
    assert(data.find("snapshot_size_limit") != std::string::npos);
    assert(data.find("\"ready\":false") != std::string::npos);
    assert(data.back() == '\n');
    // A later small sample can recover normally.
    data = "{\"ready\":true}\n";
    bwv_snapshot::bound(data);
    assert(data == "{\"ready\":true}\n");
}
