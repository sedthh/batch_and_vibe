#pragma once
#include "throw_targets.h"
#include "animal_types.h"
#include "building_types.h"
#include "object_types.h"
#include "object_fire.h"
#include "object_type_view.h"
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
            const auto check=object_type_view(obj,word,type_table);
            unsigned kind=check(obj,".?AVTree@@")?1:check(obj,".?AVRock@@")?2:
                (check(obj,".?AVVillager@@") || check(obj,".?AVSpecialVillager@@"))?3:
                check(obj,".?AVAbode@@")?4:check(obj,".?AVCreature@@")?5:
                check(obj,".?AVCitadel@@")?6:(check(obj,".?AVBall@@") || check(obj,".?AVFootball@@"))?7:
                check(obj,".?AVPileFood@@")?8:check(obj,".?AVPileWood@@")?9:
                animal_object(obj,check)?10:
                (check(obj,".?AVOneOffSpellSeed@@") || check(obj,".?AVSpellSeed@@"))?11:
                check(obj,".?AVField@@")?12:check(obj,".?AVSpellDispenser@@")?13:
                check(obj,".?AVFishFarm@@")?14:check(obj,".?AVFlock@@")?15:0;
            if(!kind)kind=object_kind(obj,check,word);
            if(!kind)kind=building_kind(obj,check,word);
            // Retain unfamiliar world types using their actual base layout.
            // Debris remains count-only by product choice, never an action ID.
            const auto bases=!kind && obj && !check(obj,".?AVFragment@@")?world_bases(obj):0;
            if(bases&1)kind=29;
            int32_t x=0,z=0;
            if(kind && obj!=held && obj!=creature && read(obj+0x14,x) && read(obj+0x18,z)) {
                const double distance=std::hypot(double(x)-cx,double(z)-cz)*(10./65536.);
                ThrowPoint point{obj,node,check.table,kind,distance,kind==6 && is_player_temple(obj,creature),
                    object_fire(obj,[](uintptr_t address,auto& value){return read(address,value);}),
                    hand_verified?std::hypot(x*(10./65536.)-hand_x,z*(10./65536.)-hand_z):-1.,x*(10./65536.),z*(10./65536.),
                    -1,nullptr,0,kind==29 && !(bases&2)};
                if(kind==3) {
                    // Dead people retain their Villager type. The current
                    // state byte, not the continuation state, distinguishes them.
                    read(obj+0x8C,point.person_state);
                    uint8_t flags=0;read(obj+0x25,flags);
                    point.important_person=(flags&4) || point.person_state==4 || point.person_state==204;
                }
                pool.add(point);
            }
            previous=node;node=word(node+8);
        }
        if(node || previous!=word(list+12))return false;
    }
    result=pool;return true;
}
}
