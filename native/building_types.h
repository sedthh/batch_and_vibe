#pragma once
#include <cstdint>
namespace bwv_probe {
// Append-only target kinds. Exact class checks keep generic scenery separate.
template<class Type, class Word>
unsigned building_kind(uintptr_t object, Type type, Word word) {
    if(!object)return 0;
    struct Mapping { const char* name; unsigned kind; };
    constexpr Mapping mappings[]={
        {".?AVAbode@@",4},{".?AVCitadel@@",6},{".?AVCitadelHeart@@",6},
        {".?AVStoragePit@@",16},{".?AVCreche@@",17},{".?AVGraveyard@@",18},
        {".?AVWorkshop@@",19},{".?AVWonder@@",20},{".?AVTownCentre@@",21},
        {".?AVTotem@@",22},{".?AVWindmill@@",23},{".?AVWorshipSite@@",24}};
    for(const auto& m:mappings)if(type(object,m.name))return m.kind;
    if(type(object,".?AVMobileStatic@@")) {
        const auto info=word(object+0x28);
        if(type(info,".?AVGMobileStaticInfo@@")) {
            const auto subtype=word(info+0x120);
            if(subtype==56 || subtype==81 || subtype==82)return 25;
        }
    }
    return 0;
}
}
