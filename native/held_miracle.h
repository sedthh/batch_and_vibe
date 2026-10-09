// Read-only identification of the native one-off spell held by a creature.
#pragma once
#include <cstdint>
namespace bwv_probe {
struct HeldMiracle {
    uint32_t spell=0,bubble_spell=0,seed=0,power=0,categories=0;
    bool verified=false;
};
template<class Type,class Word,class Read>
HeldMiracle inspect_held_miracle(uintptr_t object,Type type,Word word,Read read) {
    HeldMiracle result;
    if(!object || !type(object,".?AVOneOffSpellSeed@@")
            || word(word(object)+0xAC)!=0x55D130
            || word(0xBE4798)!=0x507B90 || word(0xBE4828)!=0x507BE0) return result;
    uint32_t seed=0,power=0;
    if(!read(object+0x68,seed) || seed>=30 || !read(object+0x78,power)
            || (power!=UINT32_MAX && power>2)) return result;
    const uintptr_t info=0xD9D678+seed*0x190;
    if(!type(info,".?AVGSpellSeedInfo@@"))return result;
    const auto spell=word(info+0x124);
    if(!spell || spell>41)return result;
    const auto variant=power==UINT32_MAX?spell:word(info+0x128+power*4);
    if(variant>41)return result;
    const auto magic=word(0xD37D10+spell*4);
    uint32_t flags[4]{};
    for(unsigned i=0;i<4;++i) {
        const unsigned offsets[]={0x48,0x4C,0x44,0x50};
        if(!magic || !read(magic+offsets[i],flags[i]) || flags[i]>1)return result;
        if(flags[i])result.categories|=1u<<i;
    }
    result.spell=spell;result.bubble_spell=variant?variant:spell;
    result.seed=seed;result.power=power;result.verified=true;
    return result;
}
}
