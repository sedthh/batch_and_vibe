// Authored native agenda recipes. Learning counters are read, never changed.
#pragma once
#include <cstdint>
namespace bwv_probe {
struct MiracleRecipe { uint32_t action,plan,spell; const char* name; const char* runtime_name; uintptr_t constructor; };
inline constexpr MiracleRecipe miracle_recipes[]={
    {17,58,10,"cast_heal_self","HealHimself",0x4A1F90},
    {18,304,22,"cast_water_tree","WaterTree",0x4B6EA0},
};
inline const MiracleRecipe* miracle_recipe(uint32_t action) {
    for (const auto& r:miracle_recipes) if (r.action==action) return &r;
    return nullptr;
}
inline bool miracle_learning_observed(uint32_t counter) {
    // Partial learning can permit native attempts. This is not a mastery test.
    return counter>0 && counter<0x80000000u;
}
// Separate QA build only: one zero-knowledge Mega Blast Extreme attempt.
// This grants permission to request the native plan, not spell knowledge.
struct UnlearnedMiracleTest {
    bool enabled=false,used=false;
    bool permits(uint32_t plan,uint32_t spell,uint32_t counter) const {
        return enabled && !used && plan==186 && spell==9 && counter==0;
    }
    bool consume(uint32_t plan,uint32_t spell,uint32_t counter) {
        if(!permits(plan,spell,counter))return false;
        used=true;return true;
    }
};
}
