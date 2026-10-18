// Authored held-object command recipes and identity tokens.
#pragma once
#include <cstdint>
#include <random>
#include "movement_guard.h"
namespace bwv_probe {
struct HeldRecipe { uint32_t action,plan; const char* name; const char* runtime_name; uintptr_t constructor; };
inline constexpr HeldRecipe held_recipes[]={
    {12,67,"put_down","PutDown",0x4A3720},
    {14,276,"eat_held","EatObjectInHand",0x4BE4E0},
    // Same native release subaction as PutDown, with its discard animation.
    // Wire 13 remains retired; never reinterpret an old examination request.
    {21,67,"discard_held","PutDown",0x4A3720},
    {23,275,"examine_held","ExamineObjectInHand",0x4BE460},
    {24,275,"look_at_held","ExamineObjectInHand",0x4BE460},
    {25,275,"shake_held","ExamineObjectInHand",0x4BE460},
    {26,275,"smell_held","ExamineObjectInHand",0x4BE460},
    {27,275,"stroke_held","ExamineObjectInHand",0x4BE460},
};
// All three constructors use the creature's native home position. The food
// variant checks the held object's engine edibility; toys have their own
// placement behavior. Keep this choice behind one held-object command.
inline constexpr HeldRecipe home_recipes[]={
    {22,84,"bring_home","BringHome",0x4A5740},
    {22,164,"bring_home","BringFoodHome",0x4AEBD0},
    {22,322,"bring_home","TakeToyHome",0x4C2BB0},
};
inline const HeldRecipe& held_home_recipe(uint32_t kind,bool can_eat) {
    return home_recipes[kind==8?2:can_eat?1:0];
}
template<class Read> const char* held_home_blocker(uintptr_t creature,uintptr_t game,Read read) {
    uint32_t width=0,depth=0;
    int32_t x=0,z=0;float altitude=0;
    // Delivery constructors use these coordinates directly. The separate
    // HasNoHome flag can be zero while a valid native destination exists.
    if(!read(creature+0x1200,x) || !read(creature+0x1204,z) || !read(creature+0x1208,altitude)
            || !read(game+0x59C8,width) || !read(game+0x59C4,depth))return "home_read_unavailable";
    if(width>1024 || depth>1024 || !std::isfinite(altitude)
            || !world_point(x*(10./65536.),z*(10./65536.),width*10.,depth*10.))return "home_position_invalid";
    return nullptr;
}
template<class Read> bool held_home_available(uintptr_t creature,uintptr_t game,Read read) {
    return !held_home_blocker(creature,game,read);
}
inline const HeldRecipe* held_recipe(uint32_t action) {
    if(action==22)return &home_recipes[0];
    for (const auto& r:held_recipes) if (r.action==action) return &r;
    return nullptr;
}
inline const char* held_kind_name(uint32_t kind) {
    const char* names[]={"unknown","food_pile","tree","rock","object","villager",
        "animal","miracle_bubble","toy","wood_pile","mushroom"};
    return kind<11?names[kind]:"unknown";
}
inline uint32_t held_action_mask(uint32_t kind,bool can_eat=false,bool can_bring_home=false) {
    // PutDown operates on the physical hand, regardless of the object's class.
    return kind>=1 && kind<=10 ? (1u<<11)|(1u<<20)|(31u<<22)|(can_eat?1u<<13:0u)
        |(can_bring_home?1u<<21:0u) : 0;
}
struct HeldExaminationVariation {
    std::minstd_rand random;
    explicit HeldExaminationVariation(uint32_t seed=1):random(seed) {}
    template<class Apply> bool configure(uint32_t action,uint32_t count,uint32_t cursor,
            uint32_t subaction,uint32_t argument,Apply apply) {
        // ExamineObjectInHand builds one animation entry fixed at 103.
        // Keep its held-only sequence. Random inspection uses shake, smell or
        // examine; stroke is available only through its explicit command.
        if(action<23 || action>27 || count!=1 || cursor!=0 || subaction!=3 || argument!=103)return false;
        const int explicit_slots[]={103,101,102,100};
        apply(action==23 ? std::uniform_int_distribution<int>(101,103)(random)
                         : explicit_slots[action-24]);
        return true;
    }
};
template<class Apply> bool configure_held_discard(uint32_t count,uint32_t cursor,
        uint32_t subaction,uint32_t argument,Apply apply) {
    // PutDown builds one release entry (1) with animation 97. Animation 95
    // selects DoDiscardAction's animated velocity, scaled/rotated by the engine;
    // 97 places with zero velocity. Neither takes a destination belief.
    if(count!=1 || cursor!=0 || subaction!=1 || argument!=97)return false;
    apply(95);return true;
}
struct HeldTarget {
    uint64_t epoch=0;
    uintptr_t creature=0,object=0,table=0;
    uint32_t kind=0,generation=0;
    bool can_eat=false,can_bring_home=false;
    const char* home_blocker="empty_hand";
    void observe(uint64_t e,uintptr_t c,uintptr_t o,uintptr_t t,uint32_t k,bool edible=false,bool home=false,
            const char* home_reason="home_not_checked") {
        if (!o || !t || !held_action_mask(k)) o=t=k=0;
        edible=o && edible;
        // The token identifies a continuous hold, not every changing property.
        // Eating still requires the current engine edibility at dispatch.
        if (epoch!=e || creature!=c || object!=o || table!=t || kind!=k) {
            ++generation; if (!generation) ++generation;
        }
        epoch=e;creature=c;object=o;table=t;kind=k;can_eat=edible;can_bring_home=o && home;
        home_blocker=!o?"empty_hand":home?nullptr:home_reason;
    }
    uint32_t token() const { return object?generation:0; }
};
}
