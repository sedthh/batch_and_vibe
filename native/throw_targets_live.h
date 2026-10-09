#pragma once
#include "throw_targets.h"
#include "animal_types.h"
#include "building_types.h"
#include "object_types.h"
#include "object_fire.h"
namespace bwv_probe {
bool throw_contract() {
    const uintptr_t info=0xC6C490+throw_plan*0x110;
    const auto length=std::strlen(throw_runtime_name)+1;
    return type(info,".?AVCreatureActionInfo@@") && range(info+0x24,length)
        && !std::memcmp(reinterpret_cast<void*>(info+0x24),throw_runtime_name,length)
        && word(0x9D1698+throw_plan*80)==throw_constructor;
}
inline constexpr uint32_t pickup_plan=10;
bool is_player_temple(uintptr_t object,uintptr_t creature) {
    const auto player=word(creature+0x1070);
    // GPlayer::ZoomToCitadel reads +A48; Citadel's owner getter reads +2C.
    return type(player,".?AVGPlayer@@") && type(object,".?AVCitadel@@")
        && word(player+0xA48)==object && word(object+0x2C)==player;
}
bool pickup_contract() {
    const uintptr_t info=0xC6C490+pickup_plan*0x110;
    const char* label="ExamineByPickingUp";
    const auto length=std::strlen(label)+1;
    return type(info,".?AVCreatureActionInfo@@") && range(info+0x24,length)
        && !std::memcmp(reinterpret_cast<void*>(info+0x24),label,length)
        && word(0x9D1698+pickup_plan*80)==0x49A7D0;
}
bool inspect_target_pool(uintptr_t creature,uintptr_t mental,uintptr_t held,KnownTargets& result,
        bool hand_verified=false,double hand_x=0,double hand_z=0) {
    if(!type(mental+0x2208,".?AVCreatureBeliefs@@"))return false;
    int32_t cx=0,cz=0;
    if(!read(creature+0x14,cx) || !read(creature+0x18,cz))return false;
    KnownTargets pool;
    pool.focus_object=result.focus_object;
    for(uintptr_t offset:{0x2210u,0x2224u}) {
        const auto list=mental+offset;uint32_t length=0;
        // Beliefs grow as the creature explores. 512 is not an engine capacity.
        // The count, backlinks and final tail bound and validate the traversal;
        // a malformed cycle cannot pass the backlink chain anchored at zero.
        if(!type(list,".?AVCreatureBeliefList@@") || !read(list+16,length))return false;
        auto node=word(list+8);uintptr_t previous=0;
        for(uint32_t i=0;i<length;++i) {
            if(!node || !movement_belief(node) || word(node+12)!=previous)return false;
            const auto obj=word(node+0x30);
            unsigned kind=type(obj,".?AVTree@@")?1:type(obj,".?AVRock@@")?2:
                (type(obj,".?AVVillager@@") || type(obj,".?AVSpecialVillager@@"))?3:
                type(obj,".?AVAbode@@")?4:type(obj,".?AVCreature@@")?5:
                type(obj,".?AVCitadel@@")?6:(type(obj,".?AVBall@@") || type(obj,".?AVFootball@@"))?7:
                type(obj,".?AVPileFood@@")?8:type(obj,".?AVPileWood@@")?9:
                animal_object(obj,type)?10:
                (type(obj,".?AVOneOffSpellSeed@@") || type(obj,".?AVSpellSeed@@"))?11:
                type(obj,".?AVField@@")?12:type(obj,".?AVSpellDispenser@@")?13:
                type(obj,".?AVFishFarm@@")?14:type(obj,".?AVFlock@@")?15:0;
            if(!kind)kind=object_kind(obj,type,word);
            if(!kind)kind=building_kind(obj,type,word);
            int32_t x=0,z=0;
            if(kind && obj!=held && obj!=creature && read(obj+0x14,x) && read(obj+0x18,z)) {
                const double distance=std::hypot(double(x)-cx,double(z)-cz)*(10./65536.);
                pool.add({obj,node,word(obj),kind,distance,kind==6 && is_player_temple(obj,creature),
                    object_fire(obj,[](uintptr_t address,auto& value){return read(address,value);}),
                    hand_verified?std::hypot(x*(10./65536.)-hand_x,z*(10./65536.)-hand_z):-1.,x*(10./65536.),z*(10./65536.)});
            }
            previous=node;node=word(node+8);
        }
        if(node || previous!=word(list+12))return false;
    }
    result=pool;return true;
}
}
