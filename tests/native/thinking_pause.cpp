#include "../../native/thinking_pause.h"
#include <cassert>
#include <cstdio>
#include <cstring>
using namespace bwv_probe;
int main() {
    ThinkingPause p;
    assert(!p.begin(1,1,100,0,42,43));
    assert(!p.begin(1,1,100,ThinkingPause::max_duration_ms+1,42,43));
    assert(!p.begin(1,0,100,30000,42,43));
    assert(p.begin(1,1,100,30000,42,43));
    assert(p.suppress(1,101,42,43,true));
    assert(!p.suppress(1,102,99,100,true) && p.active); // other creatures run
    assert(!p.end(1,2,42) && !p.end(2,1,42) && !p.end(1,1,99));
    assert(!p.begin(1,2,103,30000,42,43)); // no overlapping owner
    assert(p.begin(1,1,104,30000,42,43) && p.deadline==30100);
    assert(!p.suppress(1,30100,42,43,true)); // provider/process failure escape
    assert(!p.begin(1,1,30200,30000,42,43)); // cannot resurrect expired turn
    assert(p.begin(1,2,31000,30000,42,43));
    assert(!p.end(1,1,42) && p.active); // delayed old cleanup cannot release new turn
    assert(p.end(1,2,42) && !p.active);
    assert(p.end(1,2,42)); // cleanup is idempotent
    assert(p.begin(1,3,40000,30000,42,43));
    assert(!p.suppress(1,40001,42,43,false)); // scene/leash/training/combat control
    assert(!p.begin(1,3,40002,30000,42,43)); // no reacquire after player takeover
    assert(p.begin(1,4,50000,30000,42,43));
    assert(!p.suppress(2,50001,42,43,true));
    assert(p.begin(2,5,60000,30000,42,43));
    assert(!p.suppress(2,60001,42,44,true));
    assert(p.begin(2,6,70000,30000,42,43));
    p.release("observer_disconnected");assert(!p.active);
    ThinkingLeash loose{true,0,2,0},hand{true,1,2,0},object{true,1,2,99};
    const auto before=p.control_revision;
    assert(p.begin(2,7,80000,30000,42,43,loose));
    assert(!p.suppress(2,80001,42,43,true,loose,true));
    assert(p.open && !p.control_taken); // native continuation is not player input
    assert(!std::strcmp(p.status,"native_agenda_changed"));
    assert(!p.suppress(2,80002,42,43,true,hand)); // attach AFTER the planning pause yielded
    assert(p.control_taken && p.control_revision==before+1 && !p.open);
    assert(!p.suppress(2,80003,42,43,true,loose)); // detaching does not undo cancellation
    assert(p.end(2,7,42));
    assert(!std::strcmp(p.status,"leash_control") && p.control_taken);
    assert(p.begin(2,8,90000,30000,42,43,hand));
    assert(!p.control_taken && p.suppress(2,90001,42,43,true,hand)); // already leashed is allowed
    assert(!p.suppress(2,90002,42,43,true,object)); // change attachment
    assert(p.control_revision==before+2);
    assert(p.begin(2,9,100000,30000,42,43,hand));
    assert(!p.suppress(2,100001,42,43,true,hand,true)); // fresh forced leash direction
    assert(p.control_taken && p.control_revision==before+3);
    assert(p.begin(2,10,110000,30000,42,43,loose));
    assert(p.suppress(2,110001,42,43,true,ThinkingLeash{true,0,3,123})); // inactive retained data
    assert(p.end(2,10,42) && !p.open && !p.active);
    std::puts("Thinking pause: turn ownership, expiry, control takeover and stale cleanup passed");
}
