#pragma once
#include <cstdint>

namespace bwv_probe {
struct AgendaPriorityReading {
    uintptr_t creature=0, mental=0;
    uint32_t agenda[5]{}; // desire, belief, target, secondary target, plan
    uint32_t forced=0;
    bool valid=false, active=false;
};

// A lease on the native forced-agenda flag, not a repeated command or AI patch.
// SetAgenda's native cleanup already clears the flag on replacement/completion.
struct CommandPriority {
    static constexpr uint64_t duration_ms=7000;
    AgendaPriorityReading owned;
    uint64_t epoch=0, deadline=0;
    bool active=false;
    const char* status="not_requested";

    bool same_agenda(uint64_t session,const AgendaPriorityReading& now) const {
        if(session!=epoch || !now.valid || now.creature!=owned.creature || now.mental!=owned.mental)return false;
        for(unsigned i=0;i<5;++i)if(now.agenda[i]!=owned.agenda[i])return false;
        return true;
    }
    void begin(uint64_t session,uint64_t now,const AgendaPriorityReading& reading) {
        owned=reading;epoch=session;deadline=now+duration_ms;
        active=reading.valid && reading.forced==1;
        status=active?"active":"native_priority_not_observed";
    }
    template<class Clear>
    void release(uint64_t session,const AgendaPriorityReading& reading,const char* reason,Clear clear) {
        // Never clear a different save's flag or a newer native agenda's flag.
        if(active && same_agenda(session,reading) && reading.forced==1)clear(reading.mental);
        active=false;status=reason;
    }
    template<class Clear>
    void update(uint64_t session,uint64_t now,const AgendaPriorityReading& reading,bool control_blocked,Clear clear) {
        if(!active)return;
        if(!same_agenda(session,reading)) {active=false;status="agenda_or_identity_changed";return;}
        if(reading.forced!=1) {active=false;status="native_priority_released";return;}
        if(control_blocked)release(session,reading,"control_takeover",clear);
        else if(!reading.active)release(session,reading,"agenda_ended",clear);
        else if(now>=deadline)release(session,reading,"window_elapsed",clear);
        // Never renew or reassert the flag. Native leash handling remains active.
    }
    uint64_t remaining(uint64_t now) const {return active && now<deadline?deadline-now:0;}
};
}
