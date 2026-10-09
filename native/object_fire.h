#pragma once
#include <cstdint>
#include <cmath>
namespace bwv_probe {
// -1 is unknown. Read-only counterpart of the standard Object fire predicate.
template<class Read>
int object_fire(uintptr_t obj, Read read) {
    uint32_t table=0,method=0,fire=0,owner=0,info=0;
    if(!obj || !read(obj,table) || !read(table+0x298,method) || method!=0x402AA0
            || !read(obj+0x44,fire))return -1;
    int result=0;
    if(fire) {
        float value=0,threshold=0;
        if(!read(fire+0x1C,owner) || owner!=obj || !read(obj+0x28,info) || !info
                || !read(fire+0x14,value) || !read(info+0xB4,threshold)
                || !std::isfinite(value) || !std::isfinite(threshold)
                || value<0 || threshold<0 || value>1e9 || threshold>1e9)return -1;
        result=value>=(threshold<40.f?40.f:threshold)?1:0;
        float current_value=0,current_threshold=0;uint32_t current_owner=0,current_info=0;
        if(!read(fire+0x14,current_value) || current_value!=value
                || !read(info+0xB4,current_threshold) || current_threshold!=threshold
                || !read(fire+0x1C,current_owner) || current_owner!=owner
                || !read(obj+0x28,current_info) || current_info!=info)return -1;
    }
    uint32_t current_table=0,current_fire=0;
    if(!read(obj,current_table) || current_table!=table
            || !read(obj+0x44,current_fire) || current_fire!=fire)return -1;
    return result;
}
}
