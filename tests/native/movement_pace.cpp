#include "../../native/movement_pace.h"
#include "../../native/action_probe_policy.h"
#include "check.h"
#include <initializer_list>
using namespace bwv_probe;
int main() {
    assert(valid_paces(.6f,1.f));assert(!valid_paces(1.f,.6f));
    assert(!valid_paces(.6f,2.f));assert(!valid_paces(NAN,1.f));
    assert(effective_pace(Pace::automatic,3)==Pace::run);
    assert(effective_pace(Pace::walk,3)==Pace::walk);
    assert(effective_pace(Pace::automatic,20)==Pace::automatic);
    PaceReading reading{77,.6f,1.f,.6f};int writes=0;
    auto read=[&](uintptr_t id,PaceReading& out){out=reading;return id==42;};
    auto write=[&](uintptr_t body,float speed){assert(body==77);reading.current=speed;++writes;};
    PaceLease lease;lease.begin(1,42,89,Pace::run,reading);
    lease.update(1,42,89,132,false,read,write);assert(writes==0); // Wake transition.
    lease.update(1,42,89,67,false,read,write);assert(writes==1 && reading.current==1.f);
    lease.update(1,42,89,67,false,read,write);assert(writes==1); // No redundant write.
    lease.update(1,42,0,0,false,read,write);assert(writes==2 && reading.current==.6f && !lease.creature);
    lease.begin(1,42,122,Pace::run,reading);lease.update(1,42,122,61,false,read,write);
    reading.current=.7f;lease.release(1,42,read,write);assert(reading.current==.7f); // New native choice wins.
    lease.begin(1,42,1,Pace::walk,reading);lease.update(1,42,1,8,false,read,write);
    int before=writes;lease.release(2,42,read,write);assert(writes==before); // New save: no stale body write.
    reading.current=.6f;lease.begin(1,42,89,Pace::run,reading);lease.update(1,42,89,67,false,read,write);
    lease.update(1,42,89,67,true,read,write);assert(!lease.creature && reading.current==.6f);
    for(auto pace:{Pace::walk,Pace::run}) {
        Policy p(true,true,true);p.actions(4);p.observe(1,42,true,"",100);p.target_generation=8;
        Command c;c.action=3;c.reserved=8;c.epoch=1;c.generation=p.generation;c.offered_at=100;
        c.version=2|(uint32_t(pace)<<28)|Command::interrupt_flag;
        assert(c.protocol()==2 && c.pace()==pace && c.interrupts());
        auto bad=c;bad.version|=Command::pace_mask;assert(!p.queue(bad,100));
        bad=c;bad.action=1;assert(!p.queue(bad,100));
        assert(p.queue(c,100));assert(p.dispatch(c,100));
    }
}
