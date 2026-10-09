#include "../../native/throw_targets.h"
#include "../../native/action_probe_policy.h"
#include "../../native/animal_types.h"
#include "../../native/building_types.h"
#include "../../native/object_types.h"
#include "../../native/object_fire.h"
#include <map>
#include "check.h"
#include <cstring>
using namespace bwv_probe;
int main() {
    ThrowPoint place;place.kind=4;place.x=100;place.z=200;place.distance=120;
    assert(context_position(place));
    place.kind=1;assert(!context_position(place));
    place.kind=5;place.distance=400;assert(!context_position(place));
    place.distance_to_god_hand=120;assert(context_position(place));
    place.x=0;assert(!context_position(place));
    std::map<uintptr_t,uint32_t> memory;
    auto put_float=[&](uintptr_t p,float f){uint32_t bits;std::memcpy(&bits,&f,4);memory[p]=bits;};
    auto read_fire=[&](uintptr_t p,auto& value){auto it=memory.find(p);if(it==memory.end())return false;
        std::memcpy(&value,&it->second,4);return true;};
    memory[1000]=2000;memory[2000+0x298]=0x402AA0;memory[1000+0x44]=3000;
    memory[3000+0x1C]=1000;memory[1000+0x28]=4000;
    put_float(3000+0x14,60);put_float(4000+0xB4,60);
    assert(object_fire(1000,read_fire)==1);
    put_float(3000+0x14,59);assert(object_fire(1000,read_fire)==0);
    put_float(4000+0xB4,0);put_float(3000+0x14,39);assert(object_fire(1000,read_fire)==0);
    put_float(3000+0x14,40);assert(object_fire(1000,read_fire)==1);
    memory[3000+0x1C]=999;assert(object_fire(1000,read_fire)==-1);
    memory[1000+0x44]=0;assert(object_fire(1000,read_fire)==0);
    memory[2000+0x298]=0;assert(object_fire(1000,read_fire)==-1);
    // A distant home must survive crowded target buckets, without duplicating
    // it or supplying it to recipes that do not accept temple targets.
    KnownTargets homes;
    for(uintptr_t i=1;i<=12;++i)homes.add({i,i+100,i+200,6,200.+i});
    homes.add({99,199,299,6,1500,true});
    ThrowPoint destinations[known_target_capacity]{};
    auto home_count=homes.select(1u<<6,destinations);
    assert(home_count==9);
    assert(destinations[home_count-1].object==99 && destinations[home_count-1].player_temple);
    assert(homes.select(1u<<1,destinations)==0);
    KnownTargets close_home;close_home.add({99,199,299,6,20,true});
    assert(close_home.select(1u<<6,destinations)==1);
    assert(destinations[0].player_temple);
    auto storage=[](uintptr_t p,const char* name){return p==123 && !std::strcmp(name,".?AVStoragePit@@");};
    auto fence=[](uintptr_t p,const char* name){return (p==123 && !std::strcmp(name,".?AVMobileStatic@@"))
        || (p==456 && !std::strcmp(name,".?AVGMobileStaticInfo@@"));};
    unsigned subtype=56;
    auto data=[&](uintptr_t p)->uintptr_t{return p==123+0x28?456:p==456+0x120?subtype:0;};
    assert(building_kind(123,storage,data)==16);
    for(unsigned value:{56u,81u,82u}){subtype=value;assert(building_kind(123,fence,data)==25);}
    subtype=80;assert(building_kind(123,fence,data)==0);
    for(unsigned value:{398u,399u,400u,401u,402u,403u,404u}) {
        subtype=value;assert(object_kind(123,fence,data)==(value>=399 && value<=403?7u:0u));
    }
    auto mushroom=[](uintptr_t p,const char* name){return (p==123 && !std::strcmp(name,".?AVMobileObject@@"))
        || (p==456 && !std::strcmp(name,".?AVGMobileObjectInfo@@"));};
    auto mushroom_data=[&](uintptr_t p)->uintptr_t{return p==123+0x28?456:p==456+0x104?subtype:0;};
    for(unsigned value:{17u,18u,19u,20u}) {
        subtype=value;assert(object_kind(123,mushroom,mushroom_data)==(value==18 || value==19?26u:0u));
    }
    assert((all_object_kinds&(1u<<26)) && (portable_object_kinds&(1u<<26)));
    assert(!std::strcmp(throw_kind_name(26),"mushroom"));
    auto bonfire=[](uintptr_t p,const char* name){return p==123 && !std::strcmp(name,".?AVBonfire@@");};
    assert(object_kind(123,bonfire,data)==27 && object_kind(0,bonfire,data)==0);
    assert((all_object_kinds&(1u<<27)) && !(portable_object_kinds&(1u<<27)));
    assert(!std::strcmp(throw_kind_name(27),"bonfire"));
    KnownTargets fires;fires.add({123,456,789,27,20});
    assert(fires.select(all_object_kinds,destinations)==1 && destinations[0].kind==27);
    auto scaffold=[](uintptr_t p,const char* name){return p==123 && !std::strcmp(name,".?AVScaffold@@");};
    auto debris=[](uintptr_t p,const char* name){return p==123 && !std::strcmp(name,".?AVFragment@@");};
    assert(object_kind(123,scaffold,data)==28 && object_kind(123,debris,data)==0);
    assert((all_object_kinds&(1u<<28)) && !std::strcmp(throw_kind_name(28),"scaffolding"));
    KnownTargets hand_targets;
    for(uintptr_t i=1;i<=20;++i) {
        hand_targets.add({i,100+i,200+i,1,double(i),false,-1,2000.});
        hand_targets.add({1000+i,2000+i,3000+i,1,200.+i,false,-1,1000.});
    }
    hand_targets.add({999,1999,2999,1,1500,false,1,5});
    auto hand_count=hand_targets.select(all_object_kinds,destinations);
    bool hand_found=false;
    for(size_t i=0;i<hand_count;++i)if(destinations[i].object==999) {
        hand_found=true;assert(destinations[i].distance==1500 && destinations[i].distance_to_god_hand==5);
    }
    assert(hand_found && hand_count<=known_target_capacity);
    for(unsigned kind=16;kind<=25;++kind) {
        assert(all_object_kinds&(1u<<kind));
        assert(!(portable_object_kinds&(1u<<kind)));
        assert(std::strcmp(throw_kind_name(kind),"unknown"));
    }
    auto pig=[](uintptr_t p,const char* name){return p==123 && !std::strcmp(name,".?AVPig@@");};
    auto puzzle=[](uintptr_t,const char* name){return !std::strcmp(name,".?AVPuzzleCow@@");};
    assert(animal_object(123,pig) && !animal_object(0,pig) && !animal_object(123,puzzle));
    assert(!(portable_object_kinds & (1u<<14)) && !(portable_object_kinds & (1u<<15)));
    assert(!std::strcmp(throw_kind_name(14),"fishing_area"));
    assert(!std::strcmp(throw_kind_name(15),"animal_group"));
    ThrowTargets targets;
    ThrowPoint points[]={{100,200,300,1,10},{101,201,301,2,20}};
    targets.observe(points,2,7,1,400);
    const auto first=targets.entries[0].token, second=targets.entries[1].token;
    assert(first && second && first!=second);
    points[0].distance=25;
    ThrowPoint reordered[]={points[1],points[0],points[0]};
    targets.observe(reordered,3,7,1,400);
    assert(targets.count==2 && targets.find(first)->point.distance==25);
    assert(targets.entries[0].token==second);
    targets.observe(points,1,7,1,400);
    assert(!targets.find(second));
    targets.observe(points,2,7,1,400);
    assert(targets.entries[1].token!=second);
    targets.observe(points,2,8,1,400);assert(!targets.find(first));
    auto old=targets.entries[0].token;
    targets.observe(points,2,8,2,400);assert(!targets.find(old));
    old=targets.entries[0].token;
    points[0].belief=202;targets.observe(points,2,8,2,400);assert(!targets.find(old));
    targets.observe(points,2,0,2,400);assert(targets.count==0);
    targets.serial=UINT32_MAX;targets.observe(points,2,9,2,400);assert(targets.count==0);

    Policy p(true,true,true);p.held_target_generation=7;p.throw_tokens[0]=55;
    p.actions(1u<<14);p.observe(1,400,true,"",100);
    Command c;c.version=7;c.epoch=p.epoch;c.generation=p.generation;c.offered_at=100;c.action=15;
    c.reserved=56;assert(!p.queue(c,100));
    c.reserved=55;assert(p.queue(c,100));
    p.throw_tokens[0]=57;assert(!p.dispatch(c,100));assert(p.attempts==0);
    p.observe(1,400,true,"",101);c.generation=p.generation;c.offered_at=101;c.reserved=57;
    assert(p.queue(c,101));assert(p.dispatch(c,101));assert(p.attempts==1);
    assert(!p.queue(c,101));assert(!p.dispatch(c,101));assert(p.attempts==1);

    // Pickup leases live only while the hand remains empty, including a
    // pickup-and-release cycle occurring during model inference.
    ThrowTargets pickup;
    pickup.observe(points,2,1,2,400);const auto pickup_token=pickup.entries[0].token;
    pickup.observe(points,0,0,2,400);assert(!pickup.find(pickup_token));
    pickup.observe(points,2,1,2,400);assert(!pickup.find(pickup_token));
    Policy q(true,true,true);q.pickup_tokens[0]=81;q.actions(1u<<15);
    q.observe(2,400,true,"",100);Command d;
    d.version=8;d.action=16;d.epoch=q.epoch;d.generation=q.generation;d.offered_at=100;d.reserved=81;
    q.held_target_generation=7;assert(!q.queue(d,100));
    q.held_target_generation=0;d.version=7;assert(!q.queue(d,100));
    d.version=8;d.reserved=82;assert(!q.queue(d,100));
    d.reserved=81;assert(q.queue(d,100));q.pickup_tokens[0]=82;
    assert(!q.dispatch(d,100));assert(q.attempts==0);
    q.observe(2,400,true,"",101);d.generation=q.generation;d.offered_at=101;d.reserved=82;
    assert(q.queue(d,101));assert(q.dispatch(d,101));assert(q.attempts==1);

    // Keep observing the same target during a temporary execution lock.
    // Dispatch still requires a fresh command after the lock is released.
    ThrowTargets tracked;
    tracked.observe(points,2,1,2,400);
    const auto stable=tracked.entries[0].token;
    Policy paused(true,true,true);paused.pickup_tokens[0]=stable;
    paused.actions(1u<<15);paused.observe(2,400,true,"",100);
    Command fresh;fresh.version=8;fresh.action=16;fresh.reserved=stable;
    fresh.epoch=2;fresh.generation=paused.generation;fresh.offered_at=100;
    paused.actions(0);paused.observe(2,400,false,"native_animation_busy",110);
    tracked.observe(points,2,1,2,400);
    assert(tracked.find(stable));assert(!paused.queue(fresh,110));
    paused.actions(1u<<15);paused.observe(2,400,true,"",120);
    fresh.generation=paused.generation;fresh.offered_at=120;
    assert(paused.queue(fresh,120));assert(paused.dispatch(fresh,120));
}
