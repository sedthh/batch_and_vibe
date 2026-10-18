// Restore the native post-consumption sequence from an observed statistic delta.
#pragma once
#include <cstdint>
#include "command_priority.h"

namespace bwv_probe {
inline constexpr uintptr_t mushroom_effect_callback=0x509530;
inline constexpr uint64_t mushroom_effect_window_ms=120000;
inline bool recovered_body_gesture(int32_t animation) {
    return animation>=217 && animation<=221;
}

// GetHigh also names the ordinary acquire/eat plan. Its name alone is not
// evidence of the effect; the post-consumption callback has this distinct prefix.
inline bool mushroom_effect_sequence(uint32_t plan,uint32_t count,
        uint32_t first,uint32_t second) {
    return plan==181 && count>=2 && count<=32 && first==66 && second==137;
}

enum class MushroomEffectDecision { none, invoke, already_started };
template<class Clear,class Invoke>
bool dispatch_mushroom_effect(uintptr_t callback,Clear clear,Invoke invoke) {
    if(callback && callback!=mushroom_effect_callback)return false;
    // Removing only the exact pending mushroom callback prevents the engine
    // from dispatching it a second time after this sequence finishes.
    if(callback==mushroom_effect_callback)clear();
    invoke();return true;
}
struct MushroomConsumption {
    uint64_t epoch=0, pending_until=0;
    uintptr_t creature=0,mental=0;
    uint32_t counter=0,tick=0;
    bool initialized=false,pending=false;
    const char* status="waiting_for_reading";

    void reset() { *this=MushroomConsumption{}; }
    void cancel(const char* reason) { pending=false;status=reason; }
    void observe(uint64_t session,uintptr_t body,uintptr_t mind,uint32_t game_tick,
            uint32_t eaten,uint64_t now,bool valid) {
        if(!valid || !session || !body || !mind) { reset();return; }
        const bool baseline=!initialized || epoch!=session || creature!=body
            || mental!=mind || game_tick<tick || eaten<counter;
        if(baseline) { pending=false;status="baseline"; }
        else if(eaten>counter) {
            // Coalesce a burst; never accumulate a queue of animation replays.
            pending=true;pending_until=now+mushroom_effect_window_ms;
            status="consumption_observed";
        }
        epoch=session;creature=body;mental=mind;counter=eaten;tick=game_tick;
        initialized=true;
    }
    MushroomEffectDecision next(uint64_t now,bool native_sequence,bool eligible,
            bool holding,bool eating,uintptr_t callback) {
        if(!pending)return MushroomEffectDecision::none;
        if(native_sequence) {
            cancel("native_effect_observed");
            return MushroomEffectDecision::already_started;
        }
        if(now>=pending_until) {
            cancel("effect_deferred_too_long");return MushroomEffectDecision::none;
        }
        if(!eligible || holding || eating || (callback && callback!=mushroom_effect_callback))
            return MushroomEffectDecision::none;
        // Consume before dispatch: a failed or interrupted effect is not replayed.
        cancel("effect_requested");return MushroomEffectDecision::invoke;
    }
};
}
