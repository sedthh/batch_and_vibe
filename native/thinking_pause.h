#pragma once
#include <cstdint>

namespace bwv_probe {
struct ThinkingLeash {
    bool valid=false;
    uint32_t active=0, variant=0;
    uintptr_t target=0;

    bool changed_from(const ThinkingLeash& before) const {
        return valid && before.valid && (active!=before.active ||
            (active && (variant!=before.variant || target!=before.target)));
    }
};
// Host-owned, one-turn lease. It gates new autonomous decisions, never the
// simulation, animation processing, damage, or direct native commands.
struct ThinkingPause {
    static constexpr uint32_t protocol=14, max_duration_ms=130000;
    uint64_t epoch=0, token=0, deadline=0, skipped=0, control_revision=0;
    uintptr_t creature=0, mental=0;
    bool active=false, open=false, control_taken=false;
    ThinkingLeash leash;
    const char* status="not_requested";

    bool begin(uint64_t session,uint64_t id,uint64_t now,uint32_t duration,
               uintptr_t owner,uintptr_t mind,ThinkingLeash initial_leash={}) {
        if(!id || !owner || !mind || !duration || duration>max_duration_ms)return false;
        // A retry is idempotent; it cannot extend a completed or expired turn.
        if(epoch==session && token==id)return active;
        if(open)return false;
        epoch=session;token=id;deadline=now+duration;creature=owner;mental=mind;
        leash=initial_leash;control_taken=false;
        open=active=true;status="thinking";return true;
    }
    void release(const char* reason) {open=active=false;status=reason;}
    void takeover(const char* reason) {
        if(!control_taken)++control_revision;
        control_taken=true;release(reason);
    }
    bool end(uint64_t session,uint64_t id,uintptr_t owner) {
        if(session!=epoch || id!=token || owner!=creature)return false;
        // Preserve why a turn lost control, including after the leash is removed.
        if(active)status="turn_finished";
        open=active=false;return true;
    }
    bool suppress(uint64_t session,uint64_t now,uintptr_t owner,uintptr_t mind,bool control_allowed,
                  ThinkingLeash current_leash={},bool forced_agenda_changed=false) {
        if(!open || owner!=creature)return false;
        if(session!=epoch || mind!=mental)release("identity_changed");
        else if(now>=deadline)release("turn_timeout");
        else if(!control_allowed)takeover("control_takeover");
        else if(current_leash.changed_from(leash) ||
                (forced_agenda_changed && current_leash.valid && current_leash.active))takeover("leash_control");
        else if(forced_agenda_changed) {
            // A running native plan may continue into another forced agenda.
            // Yield planning, but keep watching for actual player input until end.
            active=false;status="native_agenda_changed";
        }
        if(current_leash.valid)leash=current_leash;
        return active;
    }
};
}
