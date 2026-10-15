#include "check.h"
#include "../../native/object_hierarchy.h"
#include <unordered_map>
#include <string>
int main() {
    using namespace bwv_probe;
    std::unordered_map<uintptr_t,uint32_t> memory{
        {996,2000},{2004,0},{2016,3000},{3008,2},{3012,4000},
        {4000,5000},{4004,6000},{5000,7000},{5008,0},{5012,UINT32_MAX},
        {6000,8000},{6008,0},{6012,UINT32_MAX}};
    std::unordered_map<uintptr_t,std::string> names{
        {7008,".?AVUnfamiliar@@"},{8008,".?AVObject@@"}};
    auto word=[&](uintptr_t p){return memory[p];};
    auto match=[&](uintptr_t p,const char* name){return names[p]==name;};
    auto read=[&](uintptr_t p){return world_object_bases(p,word,match);};
    assert(read(1000)==3);
    names[8008]=".?AVGameThingWithPos@@";assert(read(1000)==1);
    memory[6008]=4;assert(read(1000)==0); // A non-primary base needs pointer adjustment.
    memory[6008]=0;memory[6012]=12;assert(read(1000)==0); // Virtual inheritance.
    memory[6012]=UINT32_MAX;memory[2004]=4;assert(read(1000)==0);
    memory[2004]=0;memory[3008]=UINT32_MAX;assert(read(1000)==0);
    memory[3008]=2;
    ObjectBaseCache cache;unsigned calls=0;
    auto counted=[&](uintptr_t p){++calls;return read(p);};
    for(unsigned i=0;i<100;++i)assert(cache.get(1000,counted)==1);
    assert(calls==1);
    for(uintptr_t i=2000;i<2200;++i)cache.get(i,counted);
    assert(cache.count==96); // Overflow affects caching, not capability checks.
}
