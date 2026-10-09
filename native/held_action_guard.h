// Authored held-object command recipes and identity tokens.
#pragma once
#include <cstdint>
namespace bwv_probe {
struct HeldRecipe { uint32_t action,plan; const char* name; const char* runtime_name; uintptr_t constructor; };
inline constexpr HeldRecipe held_recipes[]={
    {12,67,"put_down","PutDown",0x4A3720},
    {14,276,"eat_held","EatObjectInHand",0x4BE4E0},
    // Same native release subaction as PutDown, with its discard animation.
    // Wire 13 remains retired; never reinterpret an old examination request.
    {21,67,"discard_held","PutDown",0x4A3720},
};
inline const HeldRecipe* held_recipe(uint32_t action) {
    for (const auto& r:held_recipes) if (r.action==action) return &r;
    return nullptr;
}
inline const char* held_kind_name(uint32_t kind) {
    const char* names[]={"unknown","food_pile","tree","rock","object","villager",
        "animal","miracle_bubble","toy","wood_pile","mushroom"};
    return kind<11?names[kind]:"unknown";
}
inline uint32_t held_action_mask(uint32_t kind,bool can_eat=false) {
    // PutDown operates on the physical hand, regardless of the object's class.
    return kind>=1 && kind<=10 ? (1u<<11)|(1u<<20)|(can_eat?1u<<13:0u) : 0;
}
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
    bool can_eat=false;
    void observe(uint64_t e,uintptr_t c,uintptr_t o,uintptr_t t,uint32_t k,bool edible=false) {
        if (!o || !t || !held_action_mask(k)) o=t=k=0;
        edible=o && edible;
        if (epoch!=e || creature!=c || object!=o || table!=t || kind!=k || can_eat!=edible) {
            ++generation; if (!generation) ++generation;
        }
        epoch=e;creature=c;object=o;table=t;kind=k;can_eat=edible;
    }
    uint32_t token() const { return object?generation:0; }
};
}
