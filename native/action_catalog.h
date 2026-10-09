// Authored, named recipes for the supported executable; no arbitrary plan IDs.
#pragma once
#include <cstdint>
#include "activity_guard.h"

namespace bwv_probe {
struct SelfActionRecipe {
    uint32_t action, plan;
    const char* name;
    const char* runtime_name;
    uintptr_t constructor;
    uint32_t timeout_ms;
};
inline constexpr SelfActionRecipe self_recipes[] = {
    {2,313,"sleep_here","SleepOnTheSpot",0x49B420,120000},
    {6,90,"wave","WaveAtPlayer",0x4A5E00,30000},
    {7,71,"sneeze","Sneeze",0x4A40B0,30000},
    {8,72,"shiver","Shiver",0x4A4140,30000},
    {9,75,"scratch","Scratch",0x4A4280,30000},
    {10,74,"show_hotness","ShowHotness",0x4A41F0,30000},
    {11,87,"dance","PracticeDance",0x4A5C20,45000},
};
inline const SelfActionRecipe* self_recipe(uint32_t action) {
    for (const auto& recipe:self_recipes) if (recipe.action==action) return &recipe;
    return nullptr;
}
inline uint32_t self_recipe_mask() {
    uint32_t mask=0;
    for (const auto& recipe:self_recipes) mask|=1u<<(recipe.action-1);
    return mask;
}
inline uint32_t catalog_activity_actions(const Activity& activity,uint32_t plan,uint32_t desire) {
    // The goal's name is not an interruption restriction. SetAgenda performs
    // native cleanup. Paired state and direct player control are checked outside.
    if (!activity.verified || activity.controller_mode>37 || plan>=328 || desire>=40) return 0;
    const auto current=activity_actions(activity,plan,true,desire);
    if (current==8) return 8; // Wake entry/loop first; never stomp sleep animation.
    if (activity.sleep_in_sequence) return 0;
    // Replace standalone animation goals through SetAgenda's native cleanup,
    // just as the held-object and casting commands already do.
    return 7;
}
}
