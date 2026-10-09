#pragma once
#include <cstdint>
namespace bwv_probe {
// Semantic object categories from verified type metadata, not edibility claims.
template<class Type, class Word>
unsigned object_kind(uintptr_t object, Type type, Word word) {
    if(!object)return 0;
    if(type(object,".?AVBonfire@@"))return 27;
    if(type(object,".?AVScaffold@@"))return 28;
    if(type(object,".?AVBall@@") || type(object,".?AVFootball@@"))return 7;
    if(type(object,".?AVMobileObject@@")) {
        const auto info=word(object+0x28);
        if(type(info,".?AVGMobileObjectInfo@@")) {
            const auto subtype=word(info+0x104);
            if(subtype==18 || subtype==19)return 26;
        }
    } else if(type(object,".?AVMobileStatic@@")) {
        const auto info=word(object+0x28);
        if(type(info,".?AVGMobileStaticInfo@@")) {
            const auto subtype=word(info+0x120);
            if(subtype>=399 && subtype<=403)return 7;
        }
    } else if(type(object,".?AVFeature@@")) {
        const auto info=word(object+0x28);
        if(info==0xCC99A0+54*0x124 && type(info,".?AVGFeatureInfo@@"))return 26;
    }
    return 0;
}
}
