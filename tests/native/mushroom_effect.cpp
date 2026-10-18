#include "../../native/mushroom_effect.h"
#include "../../native/world_actions.h"
#include <cassert>
#include <cstring>
#include <cstdio>
using namespace bwv_probe;

int main() {
    MushroomConsumption c;
    auto sample=[&](uint32_t count,uint32_t tick,uint64_t now) {
        c.observe(1,42,43,tick,count,now,true);
    };
    auto next=[&](uint64_t now) {return c.next(now,false,true,false,false,0);};
    sample(12,100,0); // loading a save with past consumption is not eating now
    assert(!c.pending && next(0)==MushroomEffectDecision::none);
    for(unsigned i=0;i<10000;++i) {
        sample(12,100+i,i*250);
        assert(!c.pending && next(i*250)==MushroomEffectDecision::none);
    }
    sample(13,10100,2500000);
    assert(c.pending);
    // A statistic delta is required; hand changes alone never enter this state.
    assert(c.next(2500001,false,false,false,false,0)==MushroomEffectDecision::none);
    assert(c.next(2500002,false,true,true,false,0)==MushroomEffectDecision::none);
    assert(c.next(2500003,false,true,false,true,0)==MushroomEffectDecision::none);
    assert(c.next(2500004,false,true,false,false,123)==MushroomEffectDecision::none);
    assert(c.next(2500005,false,true,false,false,mushroom_effect_callback)==MushroomEffectDecision::invoke);
    assert(!c.pending && next(2500006)==MushroomEffectDecision::none);
    sample(13,10101,2500010);
    assert(next(2500010)==MushroomEffectDecision::none); // never retry same consumption

    unsigned clears=0,calls=0;
    assert(dispatch_mushroom_effect(mushroom_effect_callback,[&](){++clears;},[&](){++calls;}));
    assert(clears==1 && calls==1);
    assert(dispatch_mushroom_effect(0,[&](){++clears;},[&](){++calls;}));
    assert(clears==1 && calls==2);
    assert(!dispatch_mushroom_effect(123,[&](){++clears;},[&](){++calls;}));
    assert(clears==1 && calls==2); // unrelated callback never overwritten or invoked

    sample(14,10102,2500020);
    assert(c.next(2500020,true,true,false,false,0)==MushroomEffectDecision::already_started);
    assert(next(2500021)==MushroomEffectDecision::none); // native playback is adopted, not repeated
    sample(15,10103,2500030);
    assert(c.next(2500030,true,false,false,false,0)==MushroomEffectDecision::already_started);
    assert(!c.pending); // do not replay an effect that began under scripted control
    sample(18,10104,2500040); // coalesce, don't enqueue three more sequences
    assert(next(2500040)==MushroomEffectDecision::invoke);
    assert(next(2500041)==MushroomEffectDecision::none);
    sample(19,10105,2500050);
    c.cancel("replaced_by_command");
    assert(next(2500051)==MushroomEffectDecision::none);
    sample(20,10106,2500060);
    assert(c.next(2620060,false,true,false,false,0)==MushroomEffectDecision::none);
    assert(!c.pending); // no late surprise after a long blocked scene

    // Save/load, profile, object reuse, lost readings and mod reconnect baseline.
    for(unsigned boundary=0;boundary<6;++boundary) {
        c.reset();sample(3,100,0);sample(4,101,10);assert(c.pending);
        if(boundary==0)c.observe(2,42,43,102,9,20,true);
        if(boundary==1)c.observe(1,44,43,102,9,20,true);
        if(boundary==2)c.observe(1,42,44,102,9,20,true);
        if(boundary==3)sample(9,1,20);
        if(boundary==4)sample(1,102,20);
        if(boundary==5) {c.observe(1,42,43,102,0,20,false);sample(9,103,30);}
        assert(!c.pending && next(31)==MushroomEffectDecision::none);
    }
    c.reset();sample(99,1000,0);assert(!c.pending);

    assert(mushroom_effect_sequence(181,12,66,137));
    assert(!mushroom_effect_sequence(181,2,0,2)); // ordinary GetHigh pickup/eating
    assert(!mushroom_effect_sequence(75,12,66,137));
    assert(!mushroom_effect_sequence(181,33,66,137));
    unsigned recovered=0;
    for(const auto& r:world_recipes) {
        assert(std::strcmp(r.name,"qa_mushroom_effect")); // no manual model capability
        if(recovered_body_gesture(r.body_animation))++recovered;
    }
    assert(recovered==5);

    AgendaPriorityReading r{42,43,{34,0,42,0,181},1,true,true};
    CommandPriority priority;
    auto clear=[&](uintptr_t mental){assert(mental==43);++clears;};
    priority.begin(1,100,r);priority.window_ms=mushroom_effect_window_ms;
    priority.deadline=100+mushroom_effect_window_ms;
    priority.update(1,60100,r,false,clear);assert(priority.active);
    priority.update(1,120100,r,false,clear);assert(!priority.active && clears==2);
    priority.begin(1,200000,r);priority.deadline=320000;
    priority.update(1,201000,r,true,clear);assert(!priority.active && clears==3);
    priority.begin(1,200000,r);priority.deadline=320000;
    auto leash=r;leash.agenda[4]=89;
    priority.update(1,201000,leash,false,clear);
    assert(!priority.active && clears==3); // no writes to replacement leash agenda
    std::puts("Mushrooms: consumption, deduplication, boundaries, callbacks and priority passed");
}
