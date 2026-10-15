#pragma once
#include <cstdint>

namespace bwv_probe {
// Match the Python detailed-sensor cadence. Fast control/held readings and
// outcome monitoring still run on every hook. Lookup/command demand bypasses
// this interval, so execution never relies on a three-second-old object check.
struct CatalogRefresh {
    static constexpr uint64_t interval_ms=3000;
    uint64_t sampled_at=0, epoch=0;
    uintptr_t creature=0, mental=0, held=0, focus=0;
    uint32_t simulation_tick=0;
    bool initialized=false, eligible=false;
    bool due(uint64_t now, uint64_t next_epoch, uintptr_t next_creature,
             uintptr_t next_mental, uintptr_t next_held, uintptr_t next_focus,
             uint32_t tick, bool next_eligible, bool command_pending) {
        const bool refresh=!initialized || command_pending || now<sampled_at
            || now-sampled_at>=interval_ms || epoch!=next_epoch || creature!=next_creature
            || mental!=next_mental || held!=next_held || focus!=next_focus
            || tick<simulation_tick || (!eligible && next_eligible);
        simulation_tick=tick;eligible=next_eligible;
        if (!refresh) return false;
        initialized=true;sampled_at=now;epoch=next_epoch;creature=next_creature;
        mental=next_mental;held=next_held;focus=next_focus;eligible=next_eligible;
        return true;
    }
};
}
