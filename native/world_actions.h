// Named agenda recipes and request-bound targets; no arbitrary native calls.
#pragma once
#include "throw_targets.h"
#include "individual_gesture.h"
namespace bwv_probe {
struct WorldRecipe {
    const char* name; uint32_t plan,spell; const char* label; uintptr_t constructor;
    uint32_t kinds; bool self;
    // Optional fixed argument for a native single body-animation sequence.
    int32_t body_animation=-1;
    uint32_t held_category=0;
    bool fish_to_hand=false;
};
constexpr uint32_t location_targets=all_object_kinds;
constexpr uint32_t living_targets=(1u<<3)|(1u<<5)|(1u<<10);
constexpr uint32_t any_targets=all_object_kinds;
inline constexpr WorldRecipe world_recipes[]={
    // FishAndEat finds the closest FishFarm to the creature itself; it does not
    // read either agenda target. Never present a fishing-area ID as binding it.
    {"fish",155,0,"FishAndEat",0x4AB350,0,true},
    {"fish_to_hand",259,0,"TakeFishHome",0x4BA4B0,0,true,-1,0,true},
    {"fish_home",259,0,"TakeFishHome",0x4BA4B0,0,true},
    {"fish_to_storage",29,0,"GiveFishToStoragePit",0x4AB610,1u<<16,false},
    {"fish_to_worship",300,0,"PutFishByWorshipSite",0x4B8510,1u<<24,false},
    // DrinkFromTheSea finds its own natural-water point; neither agenda target
    // selects a body of water. The native path handles movement and drinking.
    {"drink",55,0,"DrinkFromTheSea",0x4A15F0,0,true},
    // Target is the EXIT. Dispatch resolves an entrance in the same network,
    // registers the onward destination and builds the native travel agenda.
    {"use_teleport",134,0,"GoToTeleport",0x4A9120,1u<<30,false},
    {"attack",236,0,"Kick",0x4B6C80,any_targets,false},
    {"poo_on",19,0,"Poo",0x49ABB0,location_targets,false},
    {"sit_down",81,0,"SitDown",0x4A4AB0,0,true},
    {"look_at_player",82,0,"LookAtCamera",0x4A4ED0,0,true},
    {"howl",75,0,"Scratch",0x4A4280,0,true,body_howl},
    {"laugh",75,0,"Scratch",0x4A4280,0,true,body_laugh},
    {"cry",75,0,"Scratch",0x4A4280,0,true,body_cry},
    {"show_realization",75,0,"Scratch",0x4A4280,0,true,body_realization},
    {"pray",75,0,"Scratch",0x4A4280,0,true,body_pray},
    {"cast_fireball",46,1,"CastFireball",0x49F7D0,any_targets,false},
    {"cast_fireball_pu1",96,2,"CastFireballPU1",0x49F7D0,any_targets,false},
    {"cast_fireball_pu2",97,3,"CastFireballPU2",0x49F7D0,any_targets,false},
    {"cast_lightning",45,4,"CastLightningBolt",0x4A63B0,any_targets,false},
    {"cast_lightning_pu1",99,5,"CastLightningBoltPU1",0x4A63B0,any_targets,false},
    {"cast_lightning_pu2",100,6,"CastLightningBoltPU2",0x4A63B0,any_targets,false},
    {"cast_megablast",47,7,"CastExplosion",0x49FB30,any_targets,false},
    {"cast_megablast_pu1",185,8,"CastExplosionPU1",0x4A6650,any_targets,false},
    {"cast_megablast_pu2",186,9,"CastExplosionPU2",0x4A6650,any_targets,false},
    {"cast_heal",101,10,"CastHealSpell",0x4A6910,living_targets,true},
    {"cast_heal_pu1",102,11,"CastHealSpellPU1",0x4A6910,living_targets,true},
    {"cast_teleport",182,12,"CastTeleport",0x4A6910,location_targets,false},
    {"cast_forest",49,13,"CastMagicForest",0x4A0010,location_targets,false},
    {"cast_food",48,14,"CastMagicFood",0x49FDC0,location_targets,false},
    {"cast_food_pu1",98,15,"CastMagicFoodPU1",0x4A6910,location_targets,false},
    {"cast_lightning_storm",95,17,"CastLightningStorm",0x4B0AA0,any_targets,false},
    {"cast_tornado",103,18,"CastTornado",0x4A6650,any_targets,false},
    {"cast_spiritual_shield",54,19,"CastShield",0x4A1540,any_targets,true},
    {"cast_physical_shield",184,20,"CastPhysicalShield",0x4A6910,any_targets,true},
    {"cast_wood",104,21,"CastMagicWood",0x4A6910,location_targets,false},
    {"cast_water_field",305,22,"WaterField",0x4B6EA0,1u<<12,false},
    {"cast_water_object",238,22,"PutOutFireWithMagicWater",0x4B6EA0,any_targets,false},
    {"cast_creature_freeze",105,26,"CastMakeCreatureFreeze",0x4A6BE0,1u<<5,true},
    {"cast_creature_small",106,27,"CastMakeCreatureSmall",0x4A6BE0,1u<<5,true},
    {"cast_creature_big",107,28,"CastMakeCreatureBig",0x4A6BE0,1u<<5,true},
    {"cast_creature_weak",108,29,"CastMakeCreatureWeak",0x4A6BE0,1u<<5,true},
    {"cast_creature_strong",109,30,"CastMakeCreatureStrong",0x4A6BE0,1u<<5,true},
    {"cast_creature_fat",110,31,"CastMakeCreatureFat",0x4A6BE0,1u<<5,true},
    {"cast_creature_thin",111,32,"CastMakeCreatureThin",0x4A6BE0,1u<<5,true},
    {"cast_creature_invisible",112,33,"CastMakeCreatureInvisible",0x4A6BE0,1u<<5,true},
    {"cast_creature_nice",113,34,"CastMakeCreatureNice",0x4A6BE0,1u<<5,true},
    {"cast_creature_angry",114,35,"CastMakeCreatureAngry",0x4A6BE0,1u<<5,true},
    {"cast_creature_hungry",115,36,"CastMakeCreatureHungry",0x4A6BE0,1u<<5,true},
    {"cast_creature_frightened",116,37,"CastMakeCreatureFrightened",0x4A6BE0,1u<<5,true},
    {"cast_creature_tired",117,38,"CastMakeCreatureTired",0x4A6BE0,1u<<5,true},
    {"cast_creature_ill",118,39,"CastMakeCreatureIll",0x4A6BE0,1u<<5,true},
    {"cast_creature_thirsty",119,40,"CastMakeCreatureThirsty",0x4A6BE0,1u<<5,true},
    {"cast_creature_itchy",120,41,"CastMakeCreatureItchy",0x4A6BE0,1u<<5,true},
    {"move_to",122,0,"RunToObject",0x4A6EA0,any_targets,false},
    {"eat_from_storage",65,0,"EatFromStoragePit",0x4A2E10,1u<<16,false},
    {"eat_from_field",66,0,"EatFromContainer",0x4A30E0,1u<<12,false},
    {"eat_from_tree",94,0,"EatFromTree",0x4A6140,1u<<1,false},
    {"eat_from_food_pile",158,0,"EatFromFoodPile",0x4AE450,1u<<8,false},
    {"use_held_miracle_aggressive",228,0,"CastOneOffSpellInHandAggressive",0x4AD090,any_targets,false,-1,1},
    {"use_held_miracle_compassionate",229,0,"CastOneOffSpellInHandCompassionate",0x4B5BB0,any_targets,false,-1,2},
    {"use_held_miracle_playful",230,0,"CastOneOffSpellInHandPlayful",0x4B5D80,any_targets,false,-1,4},
    {"use_held_miracle_self",231,0,"CastOneOffSpellInHandToRestoreHealth",0x4B5FE0,0,true,-1,8},
    // Constructor reads the primary Villager belief, finds a clear area and
    // recruits the group through native subactions. No secondary target.
    {"dance_with_villagers",309,0,"DanceAmorouslyWithVillagers",0x49C9A0,1u<<3,false},
    {"vomit",50,0,"Puke",0x4A0280,0,true},
};
inline constexpr size_t world_recipe_count=sizeof(world_recipes)/sizeof(WorldRecipe);
// These two recognized containers inherit GameThingWithPos, not Object.
// CitadelHeart, despite sharing the public temple kind, IS a physical Object.
template<class Type>
bool location_only_target(const ThrowPoint& point,Type type) {
    return point.position_only || type(point.object,".?AVCitadel@@") || type(point.object,".?AVFlock@@");
}
template<class Type>
bool world_target_supported(const WorldRecipe& recipe,const ThrowPoint& point,Type type) {
    if(!point.object || !point.kind || point.kind>target_kind_count
            || !(recipe.kinds&(1u<<point.kind)))return false;
    if(point.kind==30)return recipe.plan==134 || recipe.plan==122;
    // RunToObject's approach subaction only moves toward Object instances.
    // The dispatcher supplies point movement for containers. Forest casts at
    // the selected position; other recipes need a physical recipient/approach.
    return !location_only_target(point,type) || recipe.plan==122 || recipe.plan==49;
}
struct WorldEntry {
    uint32_t token=0,recipe=0; ThrowPoint target; bool self=false;
    uint32_t held_generation=0,held_spell=0,held_seed=0,held_power=0;
};
struct WorldOffers {
    static constexpr size_t capacity=world_target_capacity;
    WorldEntry entries[capacity]{}; size_t count=0; uint32_t serial=0;
    uint64_t epoch=0; uintptr_t creature=0;
    void observe(const WorldEntry* proposed,size_t length,uint64_t session,uintptr_t owner) {
        WorldEntry next[capacity]{};size_t n=0;
        // Offers are normally grouped by recipe. Limit identity matching to its
        // previous span instead of comparing every object against every spell.
        size_t first[world_recipe_count],last[world_recipe_count]{};
        for(size_t i=0;i<world_recipe_count;++i)first[i]=count;
        if(epoch==session && creature==owner)for(size_t i=0;i<count;++i) {
            const auto recipe=entries[i].recipe;
            if(recipe<world_recipe_count) {
                if(first[recipe]==count)first[recipe]=i;
                last[recipe]=i+1;
            }
        }
        for(size_t i=0;i<length && n<capacity && owner;++i) {
            auto e=proposed[i];
            if(e.recipe>=world_recipe_count || !e.target.object || !e.target.table) continue;
            if(!e.self && !e.target.belief && !(e.target.kind==30
                    && (world_recipes[e.recipe].plan==134 || world_recipes[e.recipe].plan==122)))continue;
            e.token=0;
            for(size_t j=first[e.recipe];j<last[e.recipe];++j) {
                const auto& old=entries[j];
                if(old.recipe==e.recipe && old.self==e.self && old.target.object==e.target.object
                    && old.held_generation==e.held_generation && old.held_spell==e.held_spell
                    && old.held_seed==e.held_seed && old.held_power==e.held_power
                    && old.target.belief==e.target.belief && old.target.table==e.target.table
                    && old.target.kind==e.target.kind
                    && old.target.player_temple==e.target.player_temple) {e.token=old.token;break;}
            }
            if(!e.token && serial!=UINT32_MAX) e.token=++serial;
            if(e.token) next[n++]=e;
        }
        for(size_t i=0;i<capacity;++i) entries[i]=next[i];
        count=n;epoch=session;creature=owner;
    }
    const WorldEntry* find(uint32_t token) const {
        if(token) for(size_t i=0;i<count;++i) if(entries[i].token==token)return &entries[i];
        return nullptr;
    }
};
}
