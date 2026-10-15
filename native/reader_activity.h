#pragma once
#include <atomic>
#include <cstdint>

namespace bwv_observer {
// A snapshot reader renews interest; the loaded DLL alone is not a reader.
// Use one atomic value across the IPC worker and game thread.
struct ReaderActivity {
    static constexpr uint64_t timeout_ms=3000;
    std::atomic<uint64_t> last_read_plus_one{0};
    bool active(uint64_t now) const {
        const auto value=last_read_plus_one.load(std::memory_order_relaxed);
        return value && now>=value-1 && now-(value-1)<timeout_ms;
    }
    bool touch(uint64_t now) {
        const bool was_active=active(now);
        last_read_plus_one.store(now+1,std::memory_order_relaxed);
        return !was_active;
    }
};
}
