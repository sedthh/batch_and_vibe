// Native fishing approach repair, delivery retargeting and bounded diagnostics.
#pragma once
#include <cstdint>
#include <cmath>
namespace bwv_probe {
struct FishingPoint { float x=0,y=0,z=0; };
template<class Read,class Type,class Valid,class Repair,class Apply>
bool prepare_fishing_approach(uintptr_t sequence,uintptr_t creature,Read read,Type type,
        Valid valid,Repair repair,Apply apply,bool& adjusted) {
    adjusted=false;uint32_t count=0,cursor=0,owner=0;
    if(!type(sequence,".?AVCreatureSubActionAgenda@@") || !read(sequence+0xC28,owner)
            || owner!=creature || !read(sequence+0x14,count) || count<3 || count>32
            || !read(sequence+0xC,cursor) || cursor!=0)return false;
    const uint32_t expected[]={8,92,55}; // Approach, create the catch, pick it up.
    for(uint32_t i=0;i<3;++i) {
        uint32_t action=0;const auto entry=sequence+0x28+i*0x60;
        if(!type(entry,".?AVCreatureSubAction@@") || !read(entry+8,action) || action!=expected[i])return false;
    }
    FishingPoint point;float radius=0;
    if(!read(sequence+0x3C,point) || !read(sequence+0x48,radius) || !std::isfinite(radius)
            || radius<0 || !std::isfinite(point.x) || !std::isfinite(point.y) || !std::isfinite(point.z))return false;
    if(valid(point))return true;
    FishingPoint reachable=point;repair(point,reachable);
    const double dx=double(reachable.x)-point.x,dy=double(reachable.y)-point.y,dz=double(reachable.z)-point.z;
    // Match FishAndEat's own native shoreline correction, including its 30-unit
    // displacement check. Carry-fish constructors omit that correction.
    if(!std::isfinite(dx) || !std::isfinite(dy) || !std::isfinite(dz)
            || !valid(reachable) || std::sqrt(dx*dx+dy*dy+dz*dz)>=30)return false;
    adjusted=apply(0,reachable,radius);return adjusted;
}
struct FishingTrace {
    struct Step { uint32_t elapsed_ms=0,index=0,action=0; bool holding=false; };
    Step steps[8]{};uint32_t count=0;const char* approach="not_checked";
    void observe(uint32_t elapsed_ms,uint32_t index,uint32_t action,bool holding) {
        if(count && steps[count-1].index==index && steps[count-1].action==action
                && steps[count-1].holding==holding)return;
        if(count<8)steps[count++]={elapsed_ms,index,action,holding};
    }
};
template<class Read,class Type,class Apply>
bool configure_fish_delivery(uintptr_t sequence,uintptr_t creature,Read read,Type type,Apply apply) {
    uint32_t count=0,cursor=0,owner=0;
    if(!type(sequence,".?AVCreatureSubActionAgenda@@") || !read(sequence+0xC28,owner)
            || owner!=creature || !read(sequence+0x14,count) || count!=5
            || !read(sequence+0xC,cursor) || cursor!=0)return false;
    // TakeFishHome: approach fish, create the catch, pick up, carry, put down.
    // No eating subaction is present. An occupied-hand shortcut has only two
    // entries and must not be mistaken for a newly caught fish.
    const uint32_t expected[]={8,92,55,8,1};
    for(uint32_t i=0;i<5;++i) {
        const auto entry=sequence+0x28+i*0x60;uint32_t action=0;
        if(!type(entry,".?AVCreatureSubAction@@") || !read(entry+8,action)
                || action!=expected[i])return false;
    }
    uint32_t release=0;float radius=0;
    if(!read(sequence+0x38+4*0x60,release) || release!=97
            || !read(sequence+0x48+3*0x60,radius) || !std::isfinite(radius) || radius<0)return false;
    return apply(3,radius);
}
}
