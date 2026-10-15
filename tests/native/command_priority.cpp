#include "../../native/command_priority.h"
#include "../../native/action_probe_policy.h"
#include <cassert>
#include <cstdio>
#include <initializer_list>
using namespace bwv_probe;

int main() {
    AgendaPriorityReading r{42,43,{24,77,88,0,89},1,true,true};
    CommandPriority p;unsigned writes=0;
    auto clear=[&](uintptr_t mental){assert(mental==43);++writes;};
    p.begin(1,100,r);
    for(uint64_t now=100;now<7100;now+=100)p.update(1,now,r,false,clear);
    assert(p.active && writes==0 && p.remaining(7099)==1);
    p.update(1,7100,r,false,clear);
    assert(!p.active && writes==1 && p.remaining(7100)==0);
    p.update(1,7200,r,false,clear);assert(writes==1); // no renewal or repeated clear

    for(bool blocked:{false,true}) {
        p.begin(1,100,r);auto ended=r;ended.active=blocked;
        p.update(1,200,ended,blocked,clear);
        assert(!p.active);
    }
    assert(writes==3); // completion or camera/script/temple control releases early
    p.begin(1,100,r);auto native_release=r;native_release.forced=0;
    p.update(1,200,native_release,false,clear);assert(!p.active && writes==3);

    // Native leash input is free to replace the agenda, even with another forced
    // agenda. Never clear its flag, nor one belonging to another load/creature.
    for(unsigned field=0;field<5;++field) {
        p.begin(1,100,r);auto leash_command=r;++leash_command.agenda[field];
        p.update(1,200,leash_command,false,clear);assert(!p.active && writes==3);
    }
    p.begin(1,100,r);p.update(2,200,r,false,clear);assert(!p.active && writes==3);
    p.begin(1,100,r);auto changed=r;++changed.creature;
    p.update(1,200,changed,false,clear);assert(!p.active && writes==3);
    p.begin(1,100,r);changed=r;++changed.mental;
    p.update(1,200,changed,false,clear);assert(!p.active && writes==3);

    // Origin and pace flags survive protocol decoding and the normal scene gate.
    Command c;c.version=11|Command::interrupt_flag|Command::player_command_flag|Command::night_priority_flag|0x20000000u;
    assert(c.protocol()==11 && c.player_command() && c.night_priority() && c.interrupts() && c.pace()==Pace::run);
    Policy policy(true);policy.observe(1,42,true,"",100);
    c={};c.version=1|Command::player_command_flag;
    c.epoch=policy.epoch;c.generation=policy.generation;c.offered_at=100;
    assert(policy.queue(c,101));assert(policy.dispatch(c,102));
    Policy blocked(true);blocked.observe(1,42,false,"script_owns_creature",100);
    c.generation=blocked.generation;assert(!blocked.queue(c,101));
    Policy legacy;legacy.observe(1,42,true,"",100);
    c.generation=legacy.generation;assert(!legacy.queue(c,101));
    Policy night_policy(true);night_policy.observe(1,42,true,"",100);
    c.generation=night_policy.generation;c.version=1|Command::night_priority_flag;
    assert(!night_policy.queue(c,101)); // night cannot opt automatic ticks into player priority
    c.version|=Command::player_command_flag;
    assert(night_policy.queue(c,101));assert(night_policy.dispatch(c,102));
    // A second player command supersedes the first; it gets its own seven
    // seconds from dispatch, not the old deadline plus seven more seconds.
    CommandPriority replacement;unsigned replacements=0;
    auto release=[&](uintptr_t){++replacements;};
    replacement.begin(1,100,r);
    replacement.release(1,r,"replaced",release);
    auto second=r;second.agenda[4]=122;
    replacement.begin(1,1100,second);
    replacement.update(1,7100,second,false,release);
    assert(replacement.active && replacements==1 && replacement.remaining(7100)==1000);
    replacement.update(1,8100,second,false,release);
    assert(!replacement.active && replacements==2);
    replacement.begin(1,100,r,true);
    replacement.update(1,7100,r,false,release);
    assert(replacement.active && replacement.window_ms==10000 && replacement.remaining(7100)==3000);
    replacement.update(1,10100,r,false,release);
    assert(!replacement.active && replacements==3);
    replacement.begin(1,100,r,true);
    replacement.update(1,200,r,true,release);
    assert(!replacement.active && replacements==4); // night still yields to control takeover
    replacement.begin(1,100,r,true);
    replacement.release(1,r,"replaced",release);
    replacement.begin(1,1100,second); // next daytime request gets seven seconds, not ten
    assert(replacement.window_ms==7000 && replacement.remaining(1100)==7000);
    std::puts("Command priority: day/night expiry, replacement, native release, control takeover, identity and protocol passed");
}
