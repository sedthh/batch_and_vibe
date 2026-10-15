// Bounded, read-only belief lookup. Called on the game thread only.
// Requires the exact-build read/type helpers in action_probe.h.
#pragma once
#include "object_movement_guard.h"

namespace bwv_probe {
bool movement_belief(uintptr_t p) {
    constexpr const char* names[]={".?AVCreatureBeliefSmall@@",".?AVCreatureBelief@@",".?AVCreatureBeliefAboutAbode@@",
        ".?AVCreatureBeliefAboutCitadel@@",".?AVCreatureBeliefAboutCreature@@",".?AVCreatureBeliefAboutFlock@@",
        ".?AVCreatureBeliefAboutForest@@",".?AVCreatureBeliefAboutMobileObject@@",".?AVCreatureBeliefAboutTown@@",
        ".?AVCreatureBeliefAboutVillager@@"};
    const auto check=object_type_view(p,word,type_table);
    for (const char* name:names) {
        if (check(p,name)) return true;
    }
    return false;
}
bool object_movement_contract() {
    const uintptr_t info=0xC6C490+122*0x110;
    return type(info,".?AVCreatureActionInfo@@") && range(info+0x24,12)
        && !std::memcmp(reinterpret_cast<void*>(info+0x24),"RunToObject",12)
        && word(0x9D3CB8)==0x4A6EA0 && word(0x9D3CBC)==0
        && word(0xBE2E38)==0x5056F0 && word(0xBE2E48)==0x5057B0
        && word(0xBE2E68)==0 && word(0xBE2E6C)==0;
}
bool object_body_radius(uintptr_t creature, double& radius) {
    if (!type(creature,".?AVCreature@@") || word(word(creature)+0x64)!=0x477F40) return false;
    const uintptr_t physical=word(creature+0x160), controller=word(physical+0x58);
    float value=0;
    if (!type(physical,".?AVCreaturePhysical@@") || word(controller+0x4834)!=creature
            || !read(controller+0x5228,value) || !std::isfinite(value) || value<=0 || value>100) return false;
    radius=value;return true;
}
bool tree_arrival_contract(const ObjectPoint& target) {
    float scale=0;
    return target.kind==1 && type(target.object,".?AVTree@@")
        && word(word(target.object)+0x7C4)==0x74A140
        && word(word(target.object)+0x64)==0x638180
        && word(word(target.object)+0x120)==0x402520
        && word(0x8AB230)==0x3F8CCCCD && word(0x8AB22C)==0x3DCCCCCD
        && word(0x8AB3D4)==0x3E800000
        && read(target.object+0x50,scale) && std::isfinite(scale) && scale>0 && scale<=100;
}
bool tree_approach_space(uintptr_t creature, const ObjectPoint& target) {
    double radius=0;float scale=0;
    const uintptr_t controller=word(word(creature+0x160)+0x58);
    if (!tree_arrival_contract(target) || !object_body_radius(creature,radius)
            || word(word(creature)+0x42C)!=0x477F50 || word(0x8C2C40)!=0x41700000
            || word(0x8AB24C)!=0x3FC00000 || !read(controller+0x90,scale)) return false;
    // Forecast only for eligibility; completion uses the actual subaction value.
    return tree_has_approach_space(target.distance,radius,double(scale)*22.5);
}
// Sample the allowance the engine actually put into this action's subagenda.
// Neither mesh getters nor game functions are invoked for completion sensing.
bool object_approach_allowance(uintptr_t mental, const ObjectPoint& target, double& allowance) {
    const uintptr_t sequence=mental+0xFA8;
    uint32_t cursor=0,count=0;
    if (!object_movement_contract() || word(mental+0xF60)!=122
            || !type(sequence,".?AVCreatureSubActionAgenda@@")
            || !read(sequence+0xC,cursor) || !read(sequence+0x14,count)
            || !count || count>32 || cursor>=count) return false;
    const uintptr_t entry=sequence+0x28+cursor*0x60;
    float value=0;
    if (!type(entry,".?AVCreatureSubAction@@") || word(entry+8)!=61
            || word(entry+0xC)!=target.belief || word(target.belief+0x30)!=target.object
            || !read(entry+0x20,value) || !std::isfinite(value) || value<=0 || value>500) return false;
    allowance=value;return true;
}
// preferred pins the original object while it remains eligible; a closer tree
// cannot silently replace it. A missing/moved target revokes its lease.
ObjectPoint inspect_object_target(uintptr_t creature, uintptr_t mental, uintptr_t preferred=0) {
    ObjectPoint best, pinned;
    object_target_validation="native_object_plan_unverified";
    if (!object_movement_contract()) return {};
    object_target_validation="object_beliefs_or_world_unverified";
    const uintptr_t game=word(0xD0195C);
    uint32_t width=0,depth=0;
    int32_t cx=0,cz=0;
    if (!type(mental+0x2208,".?AVCreatureBeliefs@@")
            || !read(creature+0x14,cx) || !read(creature+0x18,cz)
            || !read(game+0x59C8,width) || !read(game+0x59C4,depth)
            || !width || !depth || width>1024 || depth>1024) return {};
    const double scale=10./65536.;
    if (!world_point(cx*scale,cz*scale,width*10.,depth*10.)) return {};
    for (uintptr_t offset : {0x2210u,0x2224u}) {
        object_target_validation="belief_list_unverified";
        const uintptr_t list=mental+offset;
        uint32_t count=0;
        // Large explored islands legitimately exceed the old 512-belief cutoff.
        // Validate the actual linked list instead of dropping every target.
        if (!type(list,".?AVCreatureBeliefList@@") || !read(list+16,count)) return {};
        uintptr_t node=word(list+8), previous=0;
        for (uint32_t i=0;i<count;++i) {
            if (!node || !movement_belief(node) || word(node+12)!=previous) return {};
            const uintptr_t obj=word(node+0x30);
            const auto check=object_type_view(obj,word,type_table);
            const unsigned kind=check(obj,".?AVTree@@")?1:check(obj,".?AVRock@@")?2:0;
            if (kind) {
                int32_t x=0,z=0; float altitude=0;
                if (!read(obj+0x14,x) || !read(obj+0x18,z) || !read(obj+0x1C,altitude)
                        || !std::isfinite(altitude)) return {};
                ObjectPoint p{obj,node,word(obj),kind,x*scale,z*scale,
                    std::hypot(double(x)-cx,double(z)-cz)*scale};
                if (world_point(p.x,p.z,width*10.,depth*10.) && object_point_valid(p)
                        && (p.kind!=1 || tree_approach_space(creature,p))) {
                    if (obj==preferred) pinned=p;
                    if (!best.object || p.distance<best.distance
                            || (p.distance==best.distance && p.object<best.object)) best=p;
                }
            }
            previous=node;node=word(node+8);
        }
        if (node || previous!=word(list+12)) return {};
    }
    object_target_validation=(pinned.object || best.object)?"known_static_target":"no_target_with_verified_approach_space";
    return pinned.object?pinned:best;
}
}
