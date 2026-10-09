#pragma once
#include <cstdint>
namespace bwv_probe {
// Holding can become observable shortly after the pickup agenda exits.
struct PickupCompletion {
    uint64_t exited_at=0;
    bool exited=false;
    void reset() { exited_at=0; exited=false; }
    bool waiting(uint64_t now, bool plan_ended, bool holding_target) {
        if (holding_target) return false;
        if (plan_ended && !exited) { exited=true; exited_at=now; }
        return exited && now>=exited_at && now-exited_at<2000;
    }
};
}
