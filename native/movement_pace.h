#pragma once
#include <cmath>
#include <cstdint>
namespace bwv_probe {
enum class Pace : uint32_t { automatic=0, walk=1, run=2 };
inline bool valid_paces(float walk, float run) {
    return std::isfinite(walk) && std::isfinite(run) && walk>0 && walk<run && run<=1;
}
inline Pace effective_pace(Pace pace, uint32_t action) {
    return pace==Pace::automatic && action==3 ? Pace::run : pace;
}
inline bool pace_action(uint32_t action) {return action==3 || action==5 || action==19 || action==20;}
// Only movement subactions belonging to our movement command retain its pace.
inline bool pace_subaction(uint32_t plan,uint32_t subaction) {
    return (plan==89 && subaction==67) || (plan==122 && subaction==61)
        || (plan==1 && subaction==8);
}
struct PaceReading {uintptr_t body=0;float walk=0,run=0,current=0;};
struct PaceLease {
    uintptr_t creature=0,body=0;uint64_t epoch=0;uint32_t plan=0;
    Pace pace=Pace::automatic;float previous=0,applied=0;bool written=false;
    template<class Reader,class Writer>
    void release(uint64_t session,uintptr_t identity,Reader read_pace,Writer set_pace) {
        PaceReading current;
        // Never write into a different save/creature, or undo a newer engine choice.
        if(written && epoch==session && creature==identity && read_pace(identity,current)
                && body==current.body && current.current==applied)set_pace(body,previous);
        *this={};
    }
    void begin(uint64_t session,uintptr_t identity,uint32_t native_plan,Pace selected,const PaceReading& before) {
        epoch=session;creature=identity;plan=native_plan;pace=selected;
        body=before.body;previous=before.current;
    }
    template<class Reader,class Writer>
    void update(uint64_t session,uintptr_t identity,uint32_t native_plan,uint32_t subaction,bool scripted,Reader read_pace,Writer set_pace) {
        if(!creature)return;
        if(epoch!=session || creature!=identity || native_plan!=plan || scripted) {release(session,identity,read_pace,set_pace);return;}
        if(!pace_subaction(plan,subaction))return; // Wait for native wake/transition cleanup.
        PaceReading current;
        if(!read_pace(identity,current) || current.body!=body){release(session,identity,read_pace,set_pace);return;}
        applied=pace==Pace::run?current.run:current.walk;
        if(current.current!=applied)set_pace(body,applied);
        written=true;
    }
};
}
