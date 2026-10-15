#pragma once
#include <cstdint>
#include <cstddef>
namespace bwv_probe {
// MSVC x86 RTTI: only primary, non-virtual bases establish that the current
// pointer supports these layouts. A class name alone is not a virtual-call ABI.
template<class Word,class Match>
unsigned world_object_bases(uintptr_t table,Word word,Match match) {
    const auto locator=word(table-4);
    if(!locator || word(locator+4)!=0)return 0;
    const auto hierarchy=word(locator+16);
    if(!hierarchy)return 0;
    const auto count=word(hierarchy+8),array=word(hierarchy+12);
    if(!count || count>256 || !array)return 0;
    unsigned result=0;
    for(unsigned i=0;i<count;++i) {
        const auto base=word(array+i*4);
        if(!base || word(base+8)!=0 || word(base+12)!=UINT32_MAX)continue;
        const auto descriptor=word(base);
        if(!descriptor)continue;
        if(match(descriptor+8,".?AVGameThingWithPos@@"))result|=1;
        if(match(descriptor+8,".?AVObject@@"))result|=3;
    }
    return result;
}
struct ObjectBaseCache {
    struct Entry {uintptr_t table=0;unsigned bases=0;};
    Entry entries[96]{};size_t count=0;
    template<class Load> unsigned get(uintptr_t table,Load load) {
        for(size_t i=0;i<count;++i)if(entries[i].table==table)return entries[i].bases;
        const auto result=load(table);
        if(count<96)entries[count++]={table,result};
        return result;
    }
};
}
