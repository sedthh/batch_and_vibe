#include "../../native/fishing_delivery.h"
#include <cassert>
#include <cstring>
#include <initializer_list>
#include <limits>
int main() {
    // Layout fixture from the native TakeFishHome constructor, independent of
    // the adapter's expected sequence. Only the fourth entry may be retargeted.
    alignas(4) unsigned char memory[0xC30]{};
    constexpr uintptr_t base=0x10000,owner=0x20000;
    auto put=[&](size_t offset,auto value){std::memcpy(memory+offset,&value,sizeof(value));};
    put(0xC28,uint32_t(owner));put(0x14,uint32_t(5));
    const uint32_t actions[]={8,92,55,8,1};
    for(size_t i=0;i<5;++i)put(0x30+i*0x60,actions[i]);
    put(0x38+4*0x60,uint32_t(97));put(0x48+3*0x60,12.5f);
    auto read=[&](uintptr_t p,auto& value) {
        if(p<base || p+sizeof(value)>base+sizeof(memory))return false;
        std::memcpy(&value,memory+p-base,sizeof(value));return true;
    };
    auto type=[](uintptr_t p,const char* name) {
        return p==base ? !std::strcmp(name,".?AVCreatureSubActionAgenda@@")
            : p>=base+0x28 && (p-base-0x28)%0x60==0 && !std::strcmp(name,".?AVCreatureSubAction@@");
    };
    int applied=0;
    auto apply=[&](uint32_t index,float radius) {
        ++applied;assert(index==3 && radius==12.5f);return true;
    };
    assert(bwv_probe::configure_fish_delivery(base,owner,read,type,apply) && applied==1);
    // Already-running, truncated, eating, wrong-object and malformed sequences
    // cannot be edited. This prevents silently retaining the home destination.
    applied=0;
    for(const auto count:{0u,2u,4u,6u}) {
        put(0x14,count);assert(!bwv_probe::configure_fish_delivery(base,owner,read,type,apply));
    }
    put(0x14,5u);put(0xC,1u);
    assert(!bwv_probe::configure_fish_delivery(base,owner,read,type,apply));put(0xC,0u);
    for(size_t i=0;i<5;++i) {
        put(0x30+i*0x60,128u);assert(!bwv_probe::configure_fish_delivery(base,owner,read,type,apply));
        put(0x30+i*0x60,actions[i]);
    }
    assert(!bwv_probe::configure_fish_delivery(base,owner+4,read,type,apply));
    put(0x38+4*0x60,95u);assert(!bwv_probe::configure_fish_delivery(base,owner,read,type,apply));
    put(0x38+4*0x60,97u);put(0x48+3*0x60,std::numeric_limits<float>::quiet_NaN());
    assert(!bwv_probe::configure_fish_delivery(base,owner,read,type,apply));
    assert(applied==0);
    put(0x48+3*0x60,12.5f);
    assert(!bwv_probe::configure_fish_delivery(base,owner,read,type,[](auto,auto){return false;}));

    using bwv_probe::FishingPoint;
    const FishingPoint water{100,0,200};put(0x3C,water);put(0x48,12.5f);
    bool adjusted=false;int repairs=0,changes=0;
    auto reachable=[](const FishingPoint& p){return p.x==110;};
    auto repair=[&](const FishingPoint& from,FishingPoint& to){++repairs;to=from;to.x=110;};
    auto change=[&](uint32_t index,const FishingPoint& point,float radius){
        ++changes;assert(index==0 && point.x==110 && point.y==0 && point.z==200 && radius==12.5f);return true;
    };
    assert(bwv_probe::prepare_fishing_approach(base,owner,read,type,reachable,repair,change,adjusted));
    assert(adjusted && repairs==1 && changes==1);
    // Reachable ground is left alone. An eating leg or bad correction cannot
    // be reinterpreted as a valid catch; the delivery point is never edited here.
    assert(bwv_probe::prepare_fishing_approach(base,owner,read,type,[](auto){return true;},repair,change,adjusted));
    assert(!adjusted && repairs==1 && changes==1);
    for(const float x:{130.f,200.f,std::numeric_limits<float>::quiet_NaN()}) {
        assert(!bwv_probe::prepare_fishing_approach(base,owner,read,type,
            [](const auto& p){return p.x!=100;},[&](const auto& from,auto& to){to=from;to.x=x;},change,adjusted));
        assert(!adjusted && changes==1);
    }
    assert(!bwv_probe::prepare_fishing_approach(base,owner,read,type,[](auto){return false;},repair,change,adjusted));
    put(0x30+2*0x60,128u);
    assert(!bwv_probe::prepare_fishing_approach(base,owner,read,type,reachable,repair,change,adjusted));
    put(0x30+2*0x60,55u);
    assert(!bwv_probe::prepare_fishing_approach(base,owner+4,read,type,reachable,repair,change,adjusted));
    assert(changes==1);
    bwv_probe::FishingTrace trace;
    for(uint32_t i=0;i<10000;++i)trace.observe(i,0,8,false);
    assert(trace.count==1);
    trace.observe(10000,1,92,false);trace.observe(10001,2,55,false);trace.observe(10002,2,55,true);
    assert(trace.count==4 && trace.steps[3].holding);
    for(uint32_t i=0;i<10000;++i)trace.observe(i,i,i,false);
    assert(trace.count==8); // bounded even if native states oscillate
}
