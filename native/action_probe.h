// Opt-in, exact-build native-agenda experiment. Never enabled in normal builds.
// The model cannot submit addresses, action IDs, arbitrary arguments or scripts.
#pragma once
#include "action_probe_policy.h"
#include "command_priority.h"
#include "thinking_pause.h"
#include "activity_guard.h"
#include "movement_guard.h"
#include "position_movement.h"
#include "object_movement_guard.h"
#include "action_catalog.h"
#include "held_action_guard.h"
#include "held_miracle.h"
#include "animal_types.h"
#include "miracle_actions.h"
#include "throw_targets.h"
#include "world_actions.h"
#include "fishing_delivery.h"
#include "teleport_travel.h"
#include "individual_gesture.h"
#ifdef BWV_HELD_ACTIONS
#include "mushroom_effect.h"
#endif
#include "pickup_completion.h"
#include "pickup_eligibility.h"
#include "catalog_refresh.h"
#include "type_name_cache.h"
#include "object_type_view.h"
#include "object_hierarchy.h"
#include "pe_signature_windows.h"
#include "work_timing.h"
#include <wincrypt.h>
#include <cmath>
#include <atomic>

namespace bwv_probe {
SRWLOCK lock = SRWLOCK_INIT;
#ifdef BWV_HELD_ACTIONS
Policy policy{true,true,true};
#elif defined(BWV_ACTION_CATALOG)
Policy policy{true,true};
#elif defined(BWV_REPEAT_ACTIONS)
Policy policy{true};
#else
Policy policy;
#endif
Command pending;
bool has_pending = false;
std::atomic<bool> supported{false}, transport_ready{false};
bool observed_plan = false, script_after = false;
uint32_t current_plan = 0, initial_plan = 0, calls = 0, belief_calls = 0;
uint32_t current_hand_mode = UINT32_MAX;
uint32_t current_desire = UINT32_MAX;
Activity current_activity;
Activity initial_activity;
Movement current_movement;
PositionDestination pending_position,executed_position;
MovementTarget movement_target;
ObjectTarget object_target;
ObjectPoint executed_object;
ObjectArrival object_arrival;
HeldTarget held_target;
HeldExaminationVariation held_examination_variation{static_cast<uint32_t>(GetTickCount64())};
HeldMiracle held_miracle;
ThrowTargets throw_targets;
// Same identity lease mechanism; binding 1 means an empty hand. Becoming
// occupied clears all pickup leases, including across a later release.
ThrowTargets pickup_targets;
uint64_t pickup_checked_at=0;
CatalogRefresh catalog_refresh;
bool held_cast_offered=false;
WorldOffers world_offers;
// Read-only lookup focus. It reserves one offer slot; it never queues an action.
uintptr_t lookup_object=0,lookup_creature=0;
uintptr_t lookup_observed_object=0;
std::string supported_world_recipes="[]";
uint64_t lookup_epoch=0,lookup_until=0;
int32_t requested_body_animation=-1;
bool body_animation_observed=false;
uint32_t body_animation_duration=0;
FishingTrace fishing_trace;
#ifdef BWV_HELD_ACTIONS
MushroomConsumption mushroom_consumption;
CommandPriority mushroom_priority;
bool cut_content_enabled=false; // configured by the launcher, not a model tool
uint64_t mushroom_sample_at=0;
uint32_t mushroom_effect_calls=0,mushroom_eye_mode=UINT32_MAX;
bool mushroom_reading_valid=false,mushroom_sequence_active=false;
#endif
ThrowPoint executed_pickup;
bool pickup_observed=false;
PickupCompletion pickup_completion;
std::atomic<bool> pickup_verified{false};
std::atomic<bool> throw_verified{false};
std::atomic<bool> held_catalog_verified{false};
bool miracle_verified=false;
uint32_t learned_miracle_mask=0;
#ifdef BWV_QA_UNLEARNED_MEGABLAST
UnlearnedMiracleTest unlearned_miracle_test{true};
#else
UnlearnedMiracleTest unlearned_miracle_test;
#endif
uint32_t unlearned_counter_after=0;
bool unlearned_counter_after_valid=false;
double distance_to_object=0;
bool object_unchanged=false;
const char* object_target_validation="not_sampled";
double movement_start_x = 0, movement_start_z = 0, movement_distance = 0;
double requested_hand_x = 0, requested_hand_z = 0, distance_to_requested_hand = 0;
bool hand_changed_after_dispatch = false;
const char* completion = "unconfirmed";
const char* result_phase = nullptr;
bool movement_observed = false;
bool monitoring_finished = false;
bool agenda_dispatched = false;
struct InterruptedAction { const char* action=nullptr; uint64_t request=0, replacement=0; };
InterruptedAction interrupted_actions[8]{}, replacing_action{};
size_t interrupted_count=0;
uint64_t interrupted_epoch=0;
uint32_t requested_plan = 92;
const char* requested_action = nullptr;
const char* requested_target_kind = nullptr;
DWORD game_thread = 0;
uintptr_t last_creature = 0, last_mental = 0;
uintptr_t executed_creature = 0, executed_mental = 0;
uint64_t executed_epoch = 0;
uint64_t request_at = 0;
uint32_t last_tick = 0;
uint64_t last_advanced = 0, stable_since = 0;
const char* outcome = "not_executed";
std::atomic<bool> self_catalog_verified{false};
uint32_t action_timeout_ms=10000;
CommandPriority command_priority;
ThinkingPause thinking_pause;
AgendaPriorityReading thinking_agenda, latest_agenda;
ThinkingLeash latest_thinking_leash;
bool thinking_hook_installed=false;
std::atomic<uintptr_t> thinking_owner{0};
thread_local bool inside_probe_tick=false;
bool observation_active=false;
bwv_observer::WorkTiming inspection_work,catalog_work;
bwv_observer::WorkTiming target_pool_work,pickup_check_work,recipe_work,legacy_target_work;

bool range(uintptr_t address, size_t size) {
    if (address < 0x10000 || !size || address+size < address || address+size > 0x80000000) return false;
    MEMORY_BASIC_INFORMATION info{};
    if (!VirtualQuery(reinterpret_cast<void*>(address), &info, sizeof(info))
            || info.State != MEM_COMMIT || (info.Protect & (PAGE_GUARD | PAGE_NOACCESS))) return false;
    return address+size <= reinterpret_cast<uintptr_t>(info.BaseAddress)+info.RegionSize;
}
template<class T> bool read(uintptr_t address, T& value) {
    if (!range(address,sizeof(T))) return false;
    std::memcpy(&value,reinterpret_cast<void*>(address),sizeof(T)); return true;
}
uintptr_t word(uintptr_t address) { uint32_t value = 0; read(address,value); return value; }
thread_local TypeNameCache* type_names=nullptr;
thread_local ObjectBaseCache* object_bases=nullptr;
struct ScopedTypeNames {
    TypeNameCache cache;
    ObjectBaseCache base_cache;
    TypeNameCache* previous=type_names;
    ObjectBaseCache* previous_bases=object_bases;
    ScopedTypeNames(){type_names=&cache;object_bases=&base_cache;}
    ~ScopedTypeNames(){type_names=previous;object_bases=previous_bases;}
};
bool type_table(uintptr_t table, const char* expected) {
    const auto fallback=[](uintptr_t vtable,const char* name) {
        const uintptr_t descriptor=word(word(vtable-4)+12);
        const size_t length=std::strlen(name)+1;
        return range(descriptor+8,length) && !std::memcmp(reinterpret_cast<void*>(descriptor+8),name,length);
    };
    if(!type_names)return fallback(table,expected);
    return type_names->matches(table,expected,[](uintptr_t vtable,char* name,size_t size) {
        const uintptr_t descriptor=word(word(vtable-4)+12);
        if(!range(descriptor+8,size))return false;
        std::memcpy(name,reinterpret_cast<void*>(descriptor+8),size);return true;
    },fallback);
}
bool type(uintptr_t object,const char* expected) {return type_table(word(object),expected);}
unsigned world_bases(uintptr_t object) {
    const auto load=[](uintptr_t table) {
        return world_object_bases(table,word,[](uintptr_t address,const char* name) {
            const auto length=std::strlen(name)+1;
            return range(address,length) && !std::memcmp(reinterpret_cast<void*>(address),name,length);
        });
    };
    return object_bases?object_bases->get(word(object),load):load(word(object));
}

}
#ifdef BWV_OBJECT_MOVEMENT
#include "object_movement_live.h"
#include "position_movement_live.h"
#include "movement_pace_live.h"
#ifdef BWV_HELD_ACTIONS
#include "throw_targets_live.h"
namespace bwv_probe {
bool miracle_contract(const MiracleRecipe& r) {
    const uintptr_t info=0xC6C490+r.plan*0x110;
    const auto length=std::strlen(r.runtime_name)+1;
    return type(info,".?AVCreatureActionInfo@@") && range(info+0x24,length)
        && !std::memcmp(reinterpret_cast<void*>(info+0x24),r.runtime_name,length)
        && word(0x9D1698+r.plan*80)==r.constructor;
}
bool miracle_counter(uintptr_t mental,const MiracleRecipe& r,uint32_t& counter) {
    return type(mental,".?AVCreatureMental@@")
        && type(mental+0x1A9FC,".?AVCreatureActionsKnownAbout@@")
        && type(0xCA9C70+r.spell*0x70,".?AVCreatureMagicActionKnownAboutEntry@@")
        && read(mental+0x17D38+r.spell*4,counter);
}
bool miracle_learned(uintptr_t mental,const MiracleRecipe& r) {
    uint32_t counter=0;
    return miracle_counter(mental,r,counter) && miracle_learning_observed(counter);
}
bool world_miracle_allowed(uintptr_t mental,const MiracleRecipe& r) {
    uint32_t counter=0;
    return miracle_counter(mental,r,counter) && (miracle_learning_observed(counter)
        || unlearned_miracle_test.permits(r.plan,r.spell,counter));
}
}
#endif
#endif
namespace bwv_probe {
const char* compatibility_method="unsupported";
bool fingerprint() {
    wchar_t path[32768]{};
    const DWORD length = GetModuleFileNameW(nullptr,path,32768);
    if (!length || length>=32768 || reinterpret_cast<uintptr_t>(GetModuleHandleW(nullptr))!=0x400000) return false;
    compatibility_method="unsupported";
    std::vector<uint8_t> data;
    if(!bwv_compat::read_image(path,data))return false;
    if(bwv_compat::digest_image(data.data(),data.size(),false)==bwv_compat::exe_file_sha256) {
        compatibility_method="exact_file";return true;
    }
    if(bwv_compat::digest_image(data.data(),data.size(),true)==bwv_compat::exe_layout_sha256) {
        compatibility_method="matching_layout";return true;
    }
    return false;
}

AgendaPriorityReading inspect_command_priority(uintptr_t creature) {
    AgendaPriorityReading result;
    if(!type(creature,".?AVCreature@@"))return result;
    result.creature=creature;result.mental=word(creature+0x164);
    if(!type(result.mental,".?AVCreatureMental@@")
            || !type(result.mental+0xF48,".?AVCreaturePlan@@")
            || !read(result.mental+0xF50,result.agenda)
            || !read(result.mental+0x1C14,result.forced))return result;
    uint32_t cursor=0,count=0,state=0;
    if(!read(result.mental+0xFB4,cursor) || !read(result.mental+0xFBC,count)
            || !read(result.mental+0xFB8,state))return result;
    result.valid=true;
    result.active=count>0 && cursor<count && state<3;
    return result;
}
void clear_command_priority(uintptr_t mental) {
    // Only called on the game thread for the lease's still-matching agenda.
    const uint32_t cleared=0;
    if(type(mental,".?AVCreatureMental@@") && range(mental+0x1C14,sizeof(cleared)))
        std::memcpy(reinterpret_cast<void*>(mental+0x1C14),&cleared,sizeof(cleared));
}

ThinkingLeash inspect_thinking_leash(uintptr_t interface_status) {
    ThinkingLeash result;
    const auto leash=word(interface_status+0x12C);
    if(type(interface_status,".?AVGInterfaceStatus@@") && type(leash,".?AVGLeashStatus@@")
            && read(leash+0x14,result.active) && result.active<=1
            && read(leash+0x1C,result.variant) && read(leash+0x24,result.target))result.valid=true;
    return result;
}

bool thinking_control_allowed(uintptr_t creature,ThinkingLeash& leash) {
    // A small control read at the decision boundary, not another world scan.
    // Direct hand/leash commands use their own native path and stay runnable.
    const auto game=word(0xD0195C),player=word(creature+0x1070);
    uint8_t game_flags=0,player_index=255,hand_index=255;
    uint16_t flags=0;
    float health=0;
    if(!read(creature+0x24,flags) || (flags&0x400) || word(creature+0x10C0)
            || !read(creature+0x48,health) || !(health>0 && health<=1)
            || !read(game+0x14,game_flags) || (game_flags&4)
            || !read(game+0x205A59,player_index) || player_index>=8
            || player!=game+0x18+player_index*0xA60 || word(player+0xA4C)!=creature
            || !read(game+0x205A5A,hand_index) || hand_index>=2)return false;
    const auto script=word(game+0x250090),interface=word(player+0x14+hand_index*4);
    const auto hand=word(interface+0x3A0),body=word(word(creature+0x160)+0x58);
    leash=inspect_thinking_leash(word(interface+0x39C));
    return script && word(script+0x78)==1 && word(script+0x80)==1 && word(script+0xA8)==0
        && hand && word(hand+0x4878)==1 && word(interface+0x3AC)!=19
        && body && word(body+0x528C)==0;
}

void update_thinking_pause(uint64_t epoch,uint64_t now,uintptr_t creature,uintptr_t mental,bool allowed,
                           ThinkingLeash leash) {
    if(!thinking_pause.open)return;
    if(epoch!=thinking_pause.epoch || creature!=thinking_pause.creature || mental!=thinking_pause.mental) {
        thinking_pause.release("identity_changed");thinking_owner=0;return;
    }
    auto current=inspect_command_priority(creature);
    bool same=current.valid;
    for(unsigned i=0;i<5 && same;++i)same=current.agenda[i]==thinking_agenda.agenda[i];
    const bool changed=current.valid && current.forced && (!same || !thinking_agenda.forced);
    thinking_pause.suppress(epoch,now,creature,mental,allowed && current.valid,leash,changed);
    if(!thinking_pause.open)thinking_owner=0;
}

uint32_t __fastcall thinking_decide(void* object,void*) {
    const auto creature=reinterpret_cast<uintptr_t>(object);
    bool paused=false;
    if(thinking_owner.load()==creature && !inside_probe_tick) {
        AcquireSRWLockExclusive(&lock);
        if(thinking_pause.open && thinking_pause.creature==creature) {
            ThinkingLeash leash;
            const bool allowed=observation_active && thinking_control_allowed(creature,leash);
            update_thinking_pause(policy.epoch,GetTickCount64(),creature,word(creature+0x164),
                                  allowed,leash);
            paused=thinking_pause.active;
            if(paused)++thinking_pause.skipped;
        }
        ReleaseSRWLockExclusive(&lock);
    }
    // DecideWhatToDo returns 1 on every normal exit. Only its incremental
    // autonomous planning is skipped; ProcessSubAction and direct commands
    // continue through their original paths, including animation cleanup.
    if(paused)return 1;
    using Decide=uint32_t(__thiscall*)(void*);
    return reinterpret_cast<Decide>(0x4F0560)(object);
}

bool install_thinking_hook() {
    // One verified virtual slot in this loaded image; no executable file edits,
    // inline instructions, copied vtables, or per-frame patching.
    constexpr uintptr_t slot=0x8CC810+0x8C8;
    if(!type_table(0x8CC810,".?AVCreature@@") || word(slot)!=0x4F0560)return false;
    DWORD protection=0,ignored=0;
    if(!VirtualProtect(reinterpret_cast<void*>(slot),sizeof(void*),PAGE_READWRITE,&protection))return false;
    auto* address=reinterpret_cast<PVOID volatile*>(slot);
    const auto previous=InterlockedCompareExchangePointer(address,reinterpret_cast<void*>(&thinking_decide),
                                                          reinterpret_cast<void*>(0x4F0560));
    const bool restored=VirtualProtect(reinterpret_cast<void*>(slot),sizeof(void*),protection,&ignored)!=FALSE;
    if(!restored && previous==reinterpret_cast<void*>(0x4F0560))
        InterlockedCompareExchangePointer(address,reinterpret_cast<void*>(0x4F0560),reinterpret_cast<void*>(&thinking_decide));
    return restored && previous==reinterpret_cast<void*>(0x4F0560);
}

void end_thinking_for_lifecycle() {
    AcquireSRWLockExclusive(&lock);
    thinking_pause.release("session_changed");thinking_owner=0;
    latest_agenda={}; // reject late begin packets until the new session is sampled
    latest_thinking_leash={};
    ReleaseSRWLockExclusive(&lock);
}

struct Observation {
    uintptr_t creature = 0, mental = 0;
    uintptr_t held = 0, held_table = 0;
    uint32_t held_kind = 0;
    bool held_can_eat = false;
    bool held_can_bring_home = false;
    const char* held_home_blocker = "home_not_checked";
    uint32_t tick = 0, plan = 0;
    uint32_t desire = UINT32_MAX;
    uint32_t hand_mode = UINT32_MAX;
    Activity activity;
    Movement movement;
    ThinkingLeash leash;
    bool scripted = false;
    bool plan_verified = false;
    uint32_t actions = 0;
    const char* blocked = "unverified_state";
};

Activity inspect_activity(uintptr_t creature, uintptr_t mental, uintptr_t physical) {
    Activity activity;
    const uintptr_t controller=word(physical+0x58);
    if (!type(controller,".?AVLH3DCreature@@")
            || !read(controller+0x4994,activity.controller_mode)
            || !read(controller+0x49A0,activity.animation)) return activity;
    activity.validation="sequence_unverified";
    const uintptr_t sequence=mental+0xFA8;
    uint32_t count=0, cursor=0;
    if (!type(sequence,".?AVCreatureSubActionAgenda@@") || word(sequence+0xC28)!=creature
            || !read(sequence+0x14,count) || !read(sequence+0xC,cursor)
            || count>32 || (count ? cursor>=count : cursor!=0)) return activity;
    activity.validation="subaction_unverified";
    // Include queued sleep, not just the current subaction: its animation can
    // start after a plan has already become Undefined. Retained sleep entries
    // may conservatively block until the engine replaces/resets the sequence.
    for (uint32_t index=cursor; index<count; ++index) {
        const uintptr_t entry=sequence+0x28+index*0x60;
        uint32_t action=0;
        if (!type(entry,".?AVCreatureSubAction@@") || !read(entry+8,action)) return activity;
        if (index==cursor) activity.current_subaction=action;
        if (action==132) activity.sleep_in_sequence=true;
    }
    // Corroborate the subaction meaning with its runtime label and callback.
    activity.validation="sleep_mapping_unverified";
    if (!range(0xBE55D8,6) || std::memcmp(reinterpret_cast<void*>(0xBE55D8),"Sleep",6)
            || word(0xBE5628)!=0x5086B0) return activity;
    activity.verified=true;
    activity.validation="layout_verified";
    return activity;
}

Movement inspect_movement(uintptr_t game, uintptr_t player, uintptr_t creature, uintptr_t status) {
    Movement result;
    result.validation="player_interfaces";
    // Native GoToHand chooses among this player's interface statuses. Require
    // exactly one interface so the observed target is the one native AI uses.
    for (unsigned index=0; index<18; ++index) {
        const uintptr_t interface=word(player+0x14+index*4);
        if (!interface) continue;
        if (!type(interface,".?AVGInterface@@") || word(interface+0x39C)!=status) return result;
        ++result.interfaces;
    }
    if (result.interfaces!=1 || !type(status,".?AVGInterfaceStatus@@")) return result;
    result.validation="native_movement_contract";
    // The creature's native owner accessor and GoToHand subaction callbacks
    // corroborate the owner/target layout before its coordinates are offered.
    if (word(word(creature)+0x1C)!=0x473F20 || word(0x9D3268)!=0x4A5D30
            || word(0xBE3198)!=0x505F60 || word(0xBE31A8)!=0x505FA0) return result;
    const uintptr_t info=0xC6C490+89*0x110;
    if (!type(info,".?AVCreatureActionInfo@@") || !range(info+0x24,9)
            || std::memcmp(reinterpret_cast<void*>(info+0x24),"GoToHand",9)) return result;
    result.validation="world_coordinates";
    int32_t cx=0, cz=0;
    float hand[3]{}, altitude=0;
    uint32_t width=0, depth=0;
    if (!read(creature+0x14,cx) || !read(creature+0x18,cz) || !read(creature+0x1C,altitude)
            || !read(status+0xC8,hand) || !read(game+0x59C8,width) || !read(game+0x59C4,depth)
            || !width || !depth || width>1024 || depth>1024
            || !std::isfinite(hand[1]) || !std::isfinite(altitude)) return result;
    result.creature_x=cx*(10.0/65536.0); result.creature_z=cz*(10.0/65536.0);
    result.hand_x=hand[0]; result.hand_z=hand[2];
    if (!world_point(result.creature_x,result.creature_z,width*10.,depth*10.)
            || !world_point(result.hand_x,result.hand_z,width*10.,depth*10.)) return result;
    result.width=width*10.; result.depth=depth*10.;
    result.distance=std::hypot(result.hand_x-result.creature_x,result.hand_z-result.creature_z);
    result.verified=true;
    result.validation="layout_verified";
    return result;
}

void inspect_pickup(uintptr_t creature,ThrowPoint& point) {
    const auto table=word(point.object),query=word(table+0x258);
    if(table!=point.table || query<0x400000 || query>=0x8A0000 || !range(query,1))return;
    // GameThingWithPos::CanBePickedUpByCreature. Query the actual override,
    // including trees, animals, villagers, toys and spell seeds, on game thread.
    using CanPickUp=int(__thiscall*)(void*,void*);
    point.pickup_possible=reinterpret_cast<CanPickUp>(query)(
        reinterpret_cast<void*>(point.object),reinterpret_cast<void*>(creature))!=0;
    if(point.pickup_possible)return;
    point.pickup_reason="engine_pickup_not_allowed";
    // Only the verified Object/Tree path uses these exact physical comparisons.
    if(query!=0x4E3A20 && query!=0x4E4A80)return;
    const auto body=word(word(creature+0x160)+0x58);
    const auto own_table=word(creature);
    const uintptr_t functions[]={word(own_table+0x64),word(table+0x64),
        word(table+0x638),word(own_table+0x638)};
    if(!body || !range(body,0x522C))return;
    for(auto fn:functions)if(fn<0x400000 || fn>=0x8A0000 || !range(fn,1))return;
    using Scalar=float(__thiscall*)(void*);
    using Reach=float(__thiscall*)(void*,int);
    const double radius=reinterpret_cast<Scalar>(functions[0])(reinterpret_cast<void*>(creature));
    const double object_radius=reinterpret_cast<Scalar>(functions[1])(reinterpret_cast<void*>(point.object));
    const double reach=reinterpret_cast<Reach>(0x485DF0)(reinterpret_cast<void*>(body),14);
    const double weight=reinterpret_cast<Scalar>(functions[2])(reinterpret_cast<void*>(point.object));
    const double own_weight=reinterpret_cast<Scalar>(functions[3])(reinterpret_cast<void*>(creature));
    float multiplier=0;
    if(read(0x8C4A04,multiplier))
        if(auto reason=pickup_limit_reason(radius,object_radius,reach,weight,own_weight*multiplier))
            point.pickup_reason=reason;
}

std::string pickup_metadata(const ThrowPoint& p) {
    std::string result;
    if(p.pickup_possible>=0)result+=p.pickup_possible?",\"pickup_possible\":true":",\"pickup_possible\":false";
    if(p.pickup_reason)result+=",\"pickup_reason\":\""+std::string(p.pickup_reason)+"\"";
    if(p.bubble_spell)result+=",\"bubble_spell\":"+std::to_string(p.bubble_spell);
    return result;
}

Observation inspect() {
    Observation obs;
    const uintptr_t game=word(0xD0195C);
    if (!type(game,".?AVGGame@@")) return obs;
    uint8_t player_index=255, hand_index=255;
    if (!read(game+0x205A59,player_index) || !read(game+0x205A5A,hand_index)
            || player_index>=8 || hand_index>=2 || !read(game+0x205A40,obs.tick)) return obs;
    const uintptr_t player=game+0x18+player_index*0xA60;
    if (!type(player,".?AVGPlayer@@")) return obs;
    obs.creature=word(player+0xA4C);
    if (!type(obs.creature,".?AVCreature@@") || word(obs.creature+0x1070)!=player) {
        obs.creature=0; obs.blocked="no_owned_creature"; return obs;
    }
    obs.mental=word(obs.creature+0x164);
    if (!type(obs.mental,".?AVCreatureMental@@") || !type(obs.mental+0xF48,".?AVCreaturePlan@@")
            || !read(obs.mental+0xF60,obs.plan)) return obs;
    read(obs.mental+0xF50,obs.desire);
    const uintptr_t physical=word(obs.creature+0x160);
    if (!type(physical,".?AVCreaturePhysical@@")) return obs;
#ifdef BWV_HELD_ACTIONS
    // Observe the physical hand independently of command permission. A hand
    // mode or scene lock must not masquerade as releasing/replacing the item.
    // The control checks below still block every action during those states.
    uint32_t held=0;
    if (!read(physical+0x28,held)) return obs;
    if (held) {
        obs.held=held;obs.held_table=word(held);
        if (!range(held,4) || !range(obs.held_table,4)) return obs;
        const auto semantic_kind=object_kind(held,type,word);
        obs.held_kind=semantic_kind==26?10:semantic_kind==7?8:type(held,".?AVPileFood@@")?1:type(held,".?AVTree@@")?2:type(held,".?AVRock@@")?3:
            (type(held,".?AVVillager@@") || type(held,".?AVSpecialVillager@@"))?5:
            animal_object(held,type)?6:
            (type(held,".?AVOneOffSpellSeed@@") || type(held,".?AVSpellSeed@@"))?7:
            (type(held,".?AVBall@@") || type(held,".?AVFootball@@"))?8:type(held,".?AVPileWood@@")?9:4;
        // GameThingWithPos::CanBeEatenByCreature is virtual slot 0x22C.
        // Query on the game thread, for this object and this creature. This
        // includes edible objects without requiring our classifier to name them.
        const auto edible_query=word(obs.held_table+0x22C);
        if(edible_query>=0x400000 && edible_query<0x8A0000 && range(edible_query,1)) {
            using CanEat=int(__thiscall*)(void*,void*);
            obs.held_can_eat=reinterpret_cast<CanEat>(edible_query)(
                reinterpret_cast<void*>(held),reinterpret_cast<void*>(obs.creature))!=0;
        }
        const auto& home=held_home_recipe(obs.held_kind,obs.held_can_eat);
        const auto metadata=0xC6C490+home.plan*0x110;
        const auto length=std::strlen(home.runtime_name)+1;
        const bool home_recipe_verified=type(metadata,".?AVCreatureActionInfo@@") && range(metadata+0x24,length)
            && !std::memcmp(reinterpret_cast<void*>(metadata+0x24),home.runtime_name,length)
            && word(0x9D1698+home.plan*80)==home.constructor;
        obs.held_home_blocker=home_recipe_verified
            ?held_home_blocker(obs.creature,game,[](uintptr_t p,auto& value){return read(p,value);})
            :"native_home_recipe_unverified";
        obs.held_can_bring_home=!obs.held_home_blocker;
    }
#endif
    obs.activity=inspect_activity(obs.creature,obs.mental,physical);
    uint16_t flags=0; uint32_t dev_script=0;
    if (!read(obs.creature+0x24,flags) || !read(obs.creature+0x10C0,dev_script)) return obs;
    obs.scripted=(flags&0x400)!=0 || dev_script!=0;
    obs.plan_verified=true;
    if (obs.scripted) { obs.blocked="script_owns_creature"; return obs; }
    uint8_t game_flags=0;
    if (!read(game+0x14,game_flags)) return obs;
    if (game_flags&4) { obs.blocked="paused"; return obs; }
    const uintptr_t script=word(game+0x250090);
    if (!type(script,".?AVGScript@@")) return obs;
    // The read-only camera sensor established this exact released tuple.
    uint32_t released=0, enabled=0, owner=0;
    if (!read(script+0x78,released) || !read(script+0x80,enabled) || !read(script+0xA8,owner)) return obs;
    if (released!=1 || enabled!=1 || owner!=0) { obs.blocked="script_camera_or_unknown_scene"; return obs; }
    const uintptr_t interface=word(player+0x14+hand_index*4);
    if (!type(interface,".?AVGInterface@@")) return obs;
    const uintptr_t hand=word(interface+0x3A0);
    if (!type(hand,".?AVCHand@@")) return obs;
    uint32_t mode=0, hand_state=0;
    if (!read(hand+0x4878,mode) || !read(interface+0x3AC,hand_state)) return obs;
    obs.hand_mode=mode;
    if (mode==10) { obs.blocked="temple"; return obs; }
    // CHand registers HandStateInvisible in slot 0 and HandStateNormal in slot 1.
    // Check the live handler and back-pointer as well as its numeric mode.
    if (mode!=1) { obs.blocked="hand_mode_not_normal"; return obs; }
    const uintptr_t normal_hand=word(hand+0x487C+4);
    if (!type(normal_hand,".?AVHandStateNormal@@") || word(normal_hand+4)!=hand) {
        obs.blocked="hand_handler_unverified"; return obs;
    }
    if (hand_state==19) { obs.blocked="training_attention"; return obs; }
    const uintptr_t interface_status=word(interface+0x39C);
    obs.movement=inspect_movement(game,player,obs.creature,interface_status);
    obs.leash=inspect_thinking_leash(interface_status);
    if(!obs.leash.valid)return obs;
    // A leash influences native behavior; it does not exclusively own the
    // creature. Keep it attached while allowing explicit agenda commands,
    // including waking. Script, menu and training controls remain checked.
#ifndef BWV_HELD_ACTIONS
    uint32_t held=0;
    if (!read(physical+0x28,held)) return obs;
    if (held) { obs.blocked="holding_object"; return obs; }
#endif
    float health=0, energy=0;
    if (!read(obs.creature+0x48,health) || !read(physical+0x1C,energy)
            || !std::isfinite(health) || !std::isfinite(energy)) return obs;
#ifdef BWV_ACTION_CATALOG
    if (health<=0 || health>1.f || energy<0 || energy>1.f) { obs.blocked="invalid_or_dead_creature"; return obs; }
    obs.actions=catalog_activity_actions(obs.activity,obs.plan,obs.desire);
#ifdef BWV_HELD_ACTIONS
    // SetAgenda terminates the old subaction through its native exit callback.
    // Holding an object is sufficient: no idle, sleep or animation prerequisite.
    if (obs.held) obs.actions=held_action_mask(obs.held_kind,obs.held_can_eat);
#endif
#else
    if (health<.8f || health>1.f || energy<.25f || energy>1.f) { obs.blocked="creature_needs_care"; return obs; }
#ifdef BWV_REPEAT_ACTIONS
    obs.actions=activity_actions(obs.activity,obs.plan,true,obs.desire);
#else
    obs.actions=activity_actions(obs.activity,obs.plan);
#endif
#endif
    if (!obs.actions
#ifdef BWV_HELD_ACTIONS
            && obs.held
#endif
            ) {
        const char* reason=activity_block(obs.activity);
        obs.blocked=reason?reason:"native_creature_busy"; return obs;
    }
    // Paired creature interactions need their own termination research.
    const uintptr_t controller=word(physical+0x58);
    uint32_t partner=0;
    if (!read(controller+0x528C,partner) || partner) {
        obs.blocked="paired_activity_or_unknown"; return obs;
    }
    if (obs.actions==5) {
        const bool travelling=travelling_can_yield(obs.activity,obs.plan,obs.desire);
        const uintptr_t info=0xC6C490+obs.plan*0x110;
        const char* name=routine_plan_name(obs.plan);
        if (!type(info,".?AVCreatureActionInfo@@") || (!travelling && (!name
                || !range(info+0x24,std::strlen(name)+1)
                || std::memcmp(reinterpret_cast<void*>(info+0x24),name,std::strlen(name)+1)))) {
            obs.blocked="routine_plan_unverified"; return obs;
        }
        if (travelling) {
            const bool first=obs.activity.current_subaction==0;
            const uintptr_t table=0xBE0BE8+obs.activity.current_subaction*0x90;
            if (word(table)!=(first?0x4FF9C0u:0x5056F0u)
                    || word(table+0x10)!=(first?0x4FFDA0u:0x5057B0u)
                    || word(table+4)!=0 || word(table+0x14)!=0
                    || word(table+0x30)!=0 || word(table+0x34)!=0) {
                obs.blocked="travel_cleanup_unverified"; return obs;
            }
        }
    }
    if (obs.actions==8) {
        const uintptr_t idle_info=0xC6C490+161*0x110;
        if (word(0xBE5658)!=0x508B30 || word(0x9D48E8)!=0x4AE8E0
                || !type(idle_info,".?AVCreatureActionInfo@@") || !range(idle_info+0x24,7)
                || std::memcmp(reinterpret_cast<void*>(idle_info+0x24),"BeIdle",7)) {
            obs.blocked="wake_cleanup_unverified"; return obs;
        }
    }
    if (obs.actions==4) {
        const uintptr_t story_info=0xC6C490+295*0x110;
        if (word(0x9D72C8)!=0x49C960 || !type(story_info,".?AVCreatureActionInfo@@")
                || !range(story_info+0x24,19)
                || std::memcmp(reinterpret_cast<void*>(story_info+0x24),"TellVillagersAStory",19)) {
            obs.blocked="social_plan_unverified"; return obs;
        }
    }
    // Runtime label is an additional semantic guard, not a source of action IDs.
    const uintptr_t info=0xC6C490+92*0x110;
    if (!type(info,".?AVCreatureActionInfo@@") || word(0x9D3358)!=0x4A6020 || !range(info+0x24,13)
            || std::memcmp(reinterpret_cast<void*>(info+0x24),"LookConfused",13)) return obs;
    const uintptr_t sleep_info=0xC6C490+313*0x110;
    if (!type(sleep_info,".?AVCreatureActionInfo@@") || word(0x9D7868)!=0x49B420
            || !range(sleep_info+0x24,15)
            || std::memcmp(reinterpret_cast<void*>(sleep_info+0x24),"SleepOnTheSpot",15)) return obs;
#ifdef BWV_ACTION_CATALOG
    self_catalog_verified=true;
    for (const auto& recipe:self_recipes) {
        const uintptr_t metadata=0xC6C490+recipe.plan*0x110;
        const auto length=std::strlen(recipe.runtime_name)+1;
        if (!type(metadata,".?AVCreatureActionInfo@@") || !range(metadata+0x24,length)
                || std::memcmp(reinterpret_cast<void*>(metadata+0x24),recipe.runtime_name,length)
                || word(0x9D1698+recipe.plan*80)!=recipe.constructor) self_catalog_verified=false;
    }
    if (self_catalog_verified && (obs.actions&1)) obs.actions|=self_recipe_mask();
#endif
#ifdef BWV_HELD_ACTIONS
    held_catalog_verified=true;
    for (const auto& recipe:held_recipes) {
        const uintptr_t metadata=0xC6C490+recipe.plan*0x110;
        const auto length=std::strlen(recipe.runtime_name)+1;
        if (!type(metadata,".?AVCreatureActionInfo@@") || !range(metadata+0x24,length)
                || std::memcmp(reinterpret_cast<void*>(metadata+0x24),recipe.runtime_name,length)
                || word(0x9D1698+recipe.plan*80)!=recipe.constructor) held_catalog_verified=false;
    }
    if (obs.held) {
        // Holding grants only held-object commands, never an unrelated gesture.
        obs.actions=held_catalog_verified?held_action_mask(obs.held_kind,obs.held_can_eat,obs.held_can_bring_home):0;
        if (!obs.actions) { obs.blocked="held_action_unavailable";return obs; }
    }
    // Pickup uses the same agenda cleanup as held commands, independently of
    // routine activity. Scene, ownership and paired checks ran above.
    else if (pickup_contract()) obs.actions|=1u<<15;
#endif
    obs.blocked=nullptr;
    return obs;
}

#ifdef BWV_HELD_ACTIONS
bool has_mushroom_sequence(uintptr_t mental) {
    const auto sequence=mental+0xFA8;
    return type(sequence,".?AVCreatureSubActionAgenda@@")
        && type(sequence+0x28,".?AVCreatureSubAction@@")
        && type(sequence+0x88,".?AVCreatureSubAction@@")
        && mushroom_effect_sequence(word(mental+0xF60),word(sequence+0x14),
                                    word(sequence+0x30),word(sequence+0x90));
}

bool update_mushroom_effect(uint64_t epoch,uint64_t now,const Observation& obs,const char* blocked) {
    // A handful of scalar reads, at most four times per second. No world scan,
    // recording, growing history, provider call or external polling is needed.
    if(now<mushroom_sample_at && !has_pending)return false;
    mushroom_sample_at=now+250;
    if(!cut_content_enabled) {
        mushroom_priority.release(epoch,inspect_command_priority(obs.creature),"disabled",clear_command_priority);
        mushroom_consumption.reset();mushroom_consumption.status="disabled";
        mushroom_reading_valid=false;mushroom_sequence_active=false;
        return false;
    }
    uint32_t counter=0,callback=0;
    const auto body=word(word(obs.creature+0x160)+0x58);
    mushroom_eye_mode=UINT32_MAX;
    mushroom_reading_valid=type(obs.creature,".?AVCreature@@")
        && type(obs.mental,".?AVCreatureMental@@") && type(body,".?AVLH3DCreature@@")
        && read(obs.creature+0x184,counter) && read(obs.mental+0x21D0,callback)
        && read(body+0x5460,mushroom_eye_mode);
    auto reading=inspect_command_priority(obs.creature);
    // Identity/load boundaries establish a baseline, never a consumption event.
    const bool baseline=!mushroom_reading_valid || !mushroom_consumption.initialized
        || epoch!=mushroom_consumption.epoch || obs.creature!=mushroom_consumption.creature
        || obs.mental!=mushroom_consumption.mental || obs.tick<mushroom_consumption.tick
        || counter<mushroom_consumption.counter;
    if(baseline) {
        if(!mushroom_reading_valid)
            mushroom_priority.release(epoch,reading,"reading_unavailable",clear_command_priority);
        mushroom_priority.active=false;mushroom_priority.status="baseline";
    }
    else mushroom_priority.update(epoch,now,reading,obs.blocked!=nullptr,clear_command_priority);
    mushroom_consumption.observe(epoch,obs.creature,obs.mental,obs.tick,counter,now,mushroom_reading_valid);
    mushroom_sequence_active=mushroom_reading_valid && reading.valid && reading.active
        && has_mushroom_sequence(obs.mental);
    if(has_pending)return false; // the accepted command below decides replacement
    const bool eating=obs.activity.animation==96 && obs.activity.controller_mode>=2;
    const auto decision=mushroom_consumption.next(now,mushroom_sequence_active,!blocked,
                                                obs.held!=0,eating,callback);
    if(decision==MushroomEffectDecision::none || blocked)return false;
    const uintptr_t info=0xC6C490+181*0x110;
    const bool contract=type(info,".?AVCreatureActionInfo@@") && range(info+0x24,8)
        && !std::memcmp(reinterpret_cast<void*>(info+0x24),"GetHigh",8)
        && word(0x9D1698+181*80)==0x4B07D0 && word(mushroom_effect_callback)==0x5324EC83
        && range(obs.mental+0x1C14,4) && range(obs.mental+0x21D0,16);
    if(!contract) { mushroom_consumption.status="effect_contract_unverified";return false; }
    if(decision==MushroomEffectDecision::invoke) {
        movement_pace.release(epoch,obs.creature,read_pace,set_pace);
        command_priority.release(epoch,reading,"mushroom_consumed",clear_command_priority);
        mushroom_priority.release(epoch,reading,"new_consumption",clear_command_priority);
        dispatch_mushroom_effect(callback,[&]() {
            const uint32_t empty_callback[4]={0,0,0,UINT32_MAX};
            std::memcpy(reinterpret_cast<void*>(obs.mental+0x21D0),empty_callback,sizeof(empty_callback));
        },[&]() {
            // Fixed native post-consumption callback (thiscall, ret 8); the
            // native function builds its own sequence. Never edit needs/stats.
            using Effect=void(__thiscall*)(void*,uintptr_t,uintptr_t);
            ++mushroom_effect_calls;
            reinterpret_cast<Effect>(mushroom_effect_callback)(reinterpret_cast<void*>(obs.creature),0,0);
        });
        reading=inspect_command_priority(obs.creature);
        mushroom_sequence_active=reading.valid && reading.active && has_mushroom_sequence(obs.mental);
    }
    if(mushroom_sequence_active && !mushroom_priority.active) {
        *reinterpret_cast<uint32_t*>(obs.mental+0x1C14)=1;
        mushroom_priority.begin(epoch,now,inspect_command_priority(obs.creature));
        mushroom_priority.window_ms=mushroom_effect_window_ms;
        mushroom_priority.deadline=now+mushroom_effect_window_ms;
        mushroom_consumption.status=decision==MushroomEffectDecision::invoke
            ?"effect_started":"native_effect_observed";
    } else if(!mushroom_sequence_active) mushroom_consumption.status="effect_not_observed";
    return decision==MushroomEffectDecision::invoke;
}
#endif

void tick(uint64_t epoch,bool observing) {
    struct ProbeScope {
        bool previous=inside_probe_tick;
        ProbeScope(){inside_probe_tick=true;}
        ~ProbeScope(){inside_probe_tick=previous;}
    } scope;
    if (!supported || !transport_ready) return;
    if (!game_thread) game_thread=GetCurrentThreadId();
    if (game_thread!=GetCurrentThreadId()) return;
    if(!observing && !observation_active)return;
    ScopedTypeNames cached_class_metadata;
    const uint64_t now=GetTickCount64();
    Observation obs=[&](){bwv_observer::TimedWork measured(inspection_work);return inspect();}();
    if(!observing) {
        if(!TryAcquireSRWLockExclusive(&lock))return;
        // Release only our still-owned temporary controls. An already-started
        // native action may finish normally; no replacement agenda is sent.
        command_priority.release(epoch,inspect_command_priority(obs.creature),
                                 "observer_disconnected",clear_command_priority);
        thinking_pause.release("observer_disconnected");
        thinking_owner=0;
#ifdef BWV_HELD_ACTIONS
        mushroom_priority.release(epoch,inspect_command_priority(obs.creature),
                                  "observer_disconnected",clear_command_priority);
        mushroom_consumption.reset();mushroom_sample_at=0;
        mushroom_reading_valid=false;mushroom_sequence_active=false;
#endif
        movement_pace.release(epoch,obs.creature,read_pace,set_pace);
        policy.observe(epoch,obs.creature,false,"observer_disconnected",now);
        if(has_pending) {
            has_pending=false;policy.phase=Phase::rejected;++policy.generation;
            outcome="observer_disconnected";result_phase="rejected";
        }
        catalog_refresh.initialized=false;
        observation_active=false;
        ReleaseSRWLockExclusive(&lock);
        return;
    }
    observation_active=true;
    if (epoch!=interrupted_epoch || obs.creature!=last_creature || obs.mental!=last_mental || obs.tick<last_tick) {
        stable_since=0; last_advanced=0;
        pickup_checked_at=0;
        interrupted_count=0;replacing_action={};interrupted_epoch=epoch;
    }
    if (obs.tick!=last_tick) last_advanced=now;
    last_tick=obs.tick; last_creature=obs.creature; last_mental=obs.mental;
    const char* blocked=obs.blocked;
    if (!blocked && (!last_advanced || now-last_advanced>500)) blocked="simulation_not_advancing";
    if (blocked) stable_since=0;
    else if (!stable_since) stable_since=now;
    if (!blocked && !(obs.actions&((1u<<11)|(1u<<15))) && now-stable_since<500) blocked="settling";
    if (!TryAcquireSRWLockExclusive(&lock)) return;
    latest_agenda=inspect_command_priority(obs.creature);
    latest_thinking_leash=obs.leash;
    update_thinking_pause(epoch,now,obs.creature,obs.mental,obs.blocked==nullptr,obs.leash);
#ifdef BWV_HELD_ACTIONS
    if(update_mushroom_effect(epoch,now,obs,blocked)) {
        obs=inspect();blocked=obs.blocked;
        catalog_refresh.initialized=false;
    }
#endif
    command_priority.update(epoch,now,inspect_command_priority(obs.creature),
                            obs.blocked!=nullptr,clear_command_priority);
    PaceReading pace_reading;
    movement_pace_verified=read_pace(obs.creature,pace_reading);
    current_pace=pace_reading;
    movement_pace.update(epoch,obs.creature,obs.plan,obs.activity.current_subaction,obs.scripted,read_pace,set_pace);
    current_plan=obs.plan; script_after=obs.scripted;
    current_hand_mode=obs.hand_mode;
    current_desire=obs.desire;
    current_activity=obs.activity;
    current_movement=obs.movement;
    movement_target.observe(obs.movement);
    policy.target_generation=movement_target.token();
#ifdef BWV_HELD_ACTIONS
    held_target.observe(epoch,obs.creature,obs.held,obs.held_table,obs.held_kind,obs.held_can_eat,
        obs.held_can_bring_home,obs.held_home_blocker);
    held_miracle=inspect_held_miracle(obs.held,type,word,
        [](uintptr_t p,uint32_t& v){return read(p,v);});
    policy.held_target_generation=held_target.token();
    const uintptr_t focus=lookup_epoch==epoch && lookup_creature==obs.creature && now<=lookup_until
        ?lookup_object:0;
    const bool refresh_catalog=catalog_refresh.due(now,epoch,obs.creature,obs.mental,
        held_target.token(),focus,obs.tick,!blocked,has_pending);
    if (refresh_catalog) {
    bwv_observer::TimedWork measured_catalog(catalog_work);
    KnownTargets known;
    known.focus_object=focus;
    {
        bwv_observer::TimedWork measured_pool(target_pool_work);
        inspect_target_pool(obs.creature,obs.mental,obs.held,known,
            obs.movement.verified,obs.movement.hand_x,obs.movement.hand_z);
    }
    lookup_observed_object=known.focus.object;
    if(known.focus.kind==11) {
        const auto bubble=inspect_held_miracle(known.focus.object,type,word,
            [](uintptr_t p,uint32_t& v){return read(p,v);});
        if(bubble.verified)known.focus.bubble_spell=bubble.bubble_spell;
    }
    for(auto* pool:{&known.nearby,&known.distant,&known.hand}) {
        for(size_t i=0;i<pool->counts[11];++i) {
            auto& point=pool->points[11][i];
            const auto bubble=inspect_held_miracle(point.object,type,word,
                [](uintptr_t p,uint32_t& v){return read(p,v);});
            if(bubble.verified)point.bubble_spell=bubble.bubble_spell;
        }
    }
    throw_verified=throw_contract();
    ThrowPoint throw_points[ThrowTargets::capacity]{};
    // Track current objects independently of whether a command may run now.
    // Clearing these lists during a routine animation invalidates the model's
    // targets even when the objects and their beliefs are unchanged. Policy
    // action masks and game-thread dispatch still enforce execution locks.
    const auto throw_count=throw_verified && obs.held
        ?known.select_nearby_or_focused(all_object_kinds,throw_points):0;
    throw_targets.observe(throw_points,throw_count,held_target.token(),epoch,obs.creature);
    for(size_t i=0;i<ThrowTargets::capacity;++i) policy.throw_tokens[i]=throw_targets.entries[i].token;
    pickup_verified=pickup_contract();
    ThrowPoint pickup_points[ThrowTargets::capacity]{};
    const auto pickup_count=pickup_verified
        ?known.select_nearby_or_focused(all_object_kinds,pickup_points):0;
    if(!blocked) {
        bwv_observer::TimedWork measured_pickup(pickup_check_work);
        const bool refresh=!pickup_checked_at || now-pickup_checked_at>=500;
        for(size_t i=0;i<pickup_count;++i) {
            auto& point=pickup_points[i];bool cached=false;
            if(!refresh)for(size_t j=0;j<pickup_targets.count;++j) {
                const auto& old=pickup_targets.entries[j].point;
                if(old.object==point.object && old.table==point.table && old.pickup_possible>=0) {
                    point.pickup_possible=old.pickup_possible;point.pickup_reason=old.pickup_reason;
                    cached=true;break;
                }
            }
            if(!cached)inspect_pickup(obs.creature,point);
        }
        if(refresh)pickup_checked_at=now;
    }
    pickup_targets.observe(pickup_points,pickup_count,1,epoch,obs.creature);
    for(size_t i=0;i<ThrowTargets::capacity;++i) policy.pickup_tokens[i]=pickup_targets.entries[i].token;
    bwv_observer::TimedWork measured_recipes(recipe_work);
    miracle_verified=true;learned_miracle_mask=0;
    for(const auto& r:miracle_recipes) {
        if(!miracle_contract(r)) miracle_verified=false;
        else if(miracle_learned(obs.mental,r)) learned_miracle_mask|=1u<<(r.action-1);
    }
    for(size_t i=0;i<ThrowTargets::capacity;++i)
        policy.water_tokens[i]=pickup_targets.entries[i].point.kind==1?pickup_targets.entries[i].token:0;
    WorldEntry world_entries[WorldOffers::capacity]{};size_t world_count=0;
    TeleportNetwork teleport_network;
    const bool teleport_verified=obs.movement.verified
        && word(0x92C12C)==0x5FC430
        && read_teleport_network(word(obs.creature+0x1070),obs.movement.width,obs.movement.depth,
            [](uintptr_t p,auto& v){return read(p,v);},type,teleport_network);
    supported_world_recipes="[";
    held_cast_offered=false;
    { // Planning targets remain visible while a put-down prerequisite runs.
        for(uint32_t i=0;i<world_recipe_count;++i) {
            const auto& r=world_recipes[i];
            if(!cut_content_enabled && recovered_body_gesture(r.body_animation))continue;
            const MiracleRecipe check{19,r.plan,r.spell,r.name,r.label,r.constructor};
            if(!miracle_contract(check) || (r.spell && (word(0x9D16A8+r.plan*80)!=r.spell
                    || !world_miracle_allowed(obs.mental,check))))continue;
            if(r.held_category && (!held_miracle.verified || !(held_miracle.categories&r.held_category)))continue;
            if(r.plan==259 && !held_home_available(obs.creature,word(0xD0195C),
                    [](uintptr_t p,auto& v){return read(p,v);}))continue;
            if(r.fish_to_hand && (!position_contract() || !valid_destination(
                    {float(obs.movement.hand_x),float(obs.movement.hand_z)},obs.movement)))continue;
            if(supported_world_recipes.size()>1)supported_world_recipes+=",";
            supported_world_recipes+="\""+std::string(r.name)+"\"";
            const auto first=world_count;
            if(r.self && world_count<WorldOffers::capacity)
                world_entries[world_count++]={0,i,{obs.creature,0,word(obs.creature),5,0},true};
            ThrowPoint targets[known_target_capacity]{};
            const auto target_count=known.select(r.kinds,targets);
            for(size_t j=0;j<target_count && world_count<WorldOffers::capacity;++j)
                if(world_target_supported(r,targets[j],type))world_entries[world_count++]={0,i,targets[j],false};
            if(teleport_verified && (r.plan==134 || r.plan==122)) {
                for(size_t j=0;j<teleport_network.count && world_count<WorldOffers::capacity;++j) {
                    const auto& gate=teleport_network.gates[j];TeleportRoute route;
                    if(r.plan==134 && !teleport_network.route(gate.object,
                            obs.movement.creature_x,obs.movement.creature_z,route))continue;
                    const double x=gate.x*(10./65536.),z=gate.z*(10./65536.);
                    ThrowPoint point{gate.object,0,gate.table,30,
                        std::hypot(x-obs.movement.creature_x,z-obs.movement.creature_z),false,-1,
                        std::hypot(x-obs.movement.hand_x,z-obs.movement.hand_z),x,z,-1,nullptr,0,true};
                    // No artificial beliefs during sampling. Dispatch obtains
                    // a real entrance belief only when travel is requested.
                    if(point.distance>0 && point.distance<=known_target_distance)
                        world_entries[world_count++]={0,i,point,false};
                    if(gate.object==focus)lookup_observed_object=focus;
                }
            }
            if(r.held_category)for(size_t j=first;j<world_count;++j) {
                auto& e=world_entries[j];e.held_generation=held_target.token();
                e.held_spell=held_miracle.spell;e.held_seed=held_miracle.seed;e.held_power=held_miracle.power;
                held_cast_offered=true;
            }
        }
    }
    supported_world_recipes+="]";
    world_offers.observe(world_entries,world_count,epoch,obs.creature);
    for(size_t i=0;i<WorldOffers::capacity;++i) {
        policy.world_tokens[i]=world_offers.entries[i].token;
        policy.world_held_generations[i]=world_offers.entries[i].held_generation;
    }
    }
#endif
#ifdef BWV_OBJECT_MOVEMENT
    // The legacy nearest-tree selector also traverses beliefs. Do not rescan
    // it on every hook; movement monitoring below must remain immediate.
#ifdef BWV_HELD_ACTIONS
    if (refresh_catalog) {
#endif
    bwv_observer::TimedWork measured_legacy(legacy_target_work);
    object_target_validation="creature_not_eligible";
    const ObjectPoint point=!blocked && (obs.actions&1)
        ? inspect_object_target(obs.creature,obs.mental,object_target.point.object) : ObjectPoint{};
    object_target.observe(point,epoch,obs.creature,obs.mental);
    policy.object_target_generation=object_target.token();
#ifdef BWV_HELD_ACTIONS
    }
#endif
    if (!monitoring_finished && agenda_dispatched && requested_plan==122
            && requested_action && !std::strcmp(requested_action,"approach_object")) {
        int32_t x=0,z=0,cx=0,cz=0;
        object_unchanged=object_unchanged && epoch==executed_epoch && obs.creature==executed_creature
            && word(executed_object.object)==executed_object.table
            && movement_belief(executed_object.belief)
            && word(executed_object.belief+0x30)==executed_object.object
            && read(executed_object.object+0x14,x) && read(executed_object.object+0x18,z)
            && x*(10./65536.)==executed_object.x && z*(10./65536.)==executed_object.z
            && read(obs.creature+0x14,cx) && read(obs.creature+0x18,cz);
        if (object_unchanged) {
            distance_to_object=std::hypot(double(x)-cx,double(z)-cz)*(10./65536.);
            movement_distance=std::hypot(cx*(10./65536.)-movement_start_x,cz*(10./65536.)-movement_start_z);
            if (movement_distance>=1.) movement_observed=true;
        }
        double allowance=0, radius=0;
        if (obs.plan==122)
            object_arrival.bind_allowance(object_approach_allowance(obs.mental,executed_object,allowance)?allowance:0);
        object_arrival.sample_radius(tree_arrival_contract(executed_object)
            && object_body_radius(obs.creature,radius)?radius:0,executed_object.kind);
    }
#endif
    if (!monitoring_finished && agenda_dispatched && requested_plan==89 && obs.movement.verified && epoch==executed_epoch
            && obs.creature==executed_creature) {
        movement_distance=std::hypot(obs.movement.creature_x-movement_start_x,
                                    obs.movement.creature_z-movement_start_z);
        if (movement_distance>=1.) movement_observed=true;
        distance_to_requested_hand=std::hypot(obs.movement.creature_x-requested_hand_x,
                                            obs.movement.creature_z-requested_hand_z);
        if (std::hypot(obs.movement.hand_x-requested_hand_x,obs.movement.hand_z-requested_hand_z)>2.)
            hand_changed_after_dispatch=true;
    }
    policy.actions(blocked?0:(obs.actions&~(1u<<15)) | (!obs.held && pickup_targets.count?(1u<<15):0u)
        | hand_approach_mask(obs.movement,obs.activity.verified,obs.held!=0)
        | (policy.held_enabled && !obs.held && obs.activity.verified && obs.movement.verified && position_contract()?1u<<19:0u)
        | ((!obs.held || held_cast_offered) && world_offers.count && obs.activity.verified?1u<<18:0u)
        | (policy.object_target_generation?16u:0u)
        | (throw_targets.count?(1u<<14):0u)
        | (miracle_verified && !obs.held && obs.activity.verified
            ? learned_miracle_mask & ((1u<<16) | (policy.has_water_target()?1u<<17:0)) : 0));
    policy.observe(epoch,obs.creature,!blocked,blocked?blocked:"manual_test_only",now);
    unlearned_counter_after_valid=false;
    if(unlearned_miracle_test.used && epoch==executed_epoch
            && obs.creature==executed_creature && obs.mental==executed_mental) {
        const MiracleRecipe check{19,186,9,"cast_megablast_pu2","CastExplosionPU2",0x4A6650};
        unlearned_counter_after_valid=miracle_counter(obs.mental,check,unlearned_counter_after);
    }
    if (!monitoring_finished && policy.consumed && agenda_dispatched && (epoch!=executed_epoch || obs.creature!=executed_creature || obs.mental!=executed_mental)) {
        outcome="session_or_creature_changed"; policy.finish(now,false);
    } else if (!monitoring_finished && policy.consumed && agenda_dispatched) {
        if((requested_plan==259 || requested_plan==29 || requested_plan==300)
                && obs.plan==requested_plan && obs.activity.verified)
            fishing_trace.observe(static_cast<uint32_t>(now-request_at),word(obs.mental+0xFB4),
                obs.activity.current_subaction,obs.held!=0);
        if (obs.plan_verified && obs.plan==requested_plan) observed_plan=true;
        if (requested_body_animation>=0 && obs.activity.verified
                && obs.activity.animation==static_cast<uint32_t>(requested_body_animation)
                && obs.activity.controller_mode==10) body_animation_observed=true;
        if (requested_action && !std::strcmp(requested_action,"pick_up") && observed_plan
                && obs.held==executed_pickup.object && obs.held_table==executed_pickup.table)
            pickup_observed=true;
        if (observed_plan) policy.phase=Phase::observed;
        if (obs.scripted) { outcome="external_script_control_observed"; policy.finish(now,false); }
        else if (requested_plan==161 && obs.plan_verified && awake_activity(obs.activity)) {
            completion="awake_observed"; outcome="native_wake_observed"; policy.finish(now,true);
        }
        else if (requested_plan==313 && obs.activity.verified && obs.activity.current_subaction==132
                && obs.activity.animation==29 && obs.activity.controller_mode==3) {
            completion="sleep_observed"; outcome="native_sleep_observed"; policy.finish(now,true);
        }
        else if (requested_plan!=161 && observed_plan && obs.plan_verified && obs.plan!=requested_plan) {
            const bool waiting_for_pickup=requested_action && !std::strcmp(requested_action,"pick_up")
                && pickup_completion.waiting(now,true,pickup_observed);
            if (!waiting_for_pickup) {
            outcome="native_plan_changed_after_action"; policy.finish(now,true);
            if(requested_body_animation>=0) completion=body_animation_observed?"body_animation_observed":"unconfirmed";
            if (requested_action && !std::strcmp(requested_action,"pick_up") && pickup_observed)
                completion="requested_object_held_observed";
            if(requested_plan==1 && obs.movement.verified
                    && std::hypot(obs.movement.creature_x-executed_position.x,obs.movement.creature_z-executed_position.z)<=3.)
                completion="reached_position_target";
            if (requested_plan==89 && obs.movement.verified && movement_observed
                    && obs.movement.distance<=2.)
                completion="reached_hand_target";
            if (requested_plan==122 && requested_action && !std::strcmp(requested_action,"approach_object")
                    && object_arrival.reached(distance_to_object,object_unchanged,
                    movement_observed,observed_plan,true))
                completion="reached_object_target";
            }
        }
        else if (action_monitor_timed_out(requested_plan,obs.plan_verified,obs.plan,
                                         now-policy.accepted_at,action_timeout_ms)) {
            outcome=observed_plan?"action_still_active_no_forced_cleanup":"action_not_observed";
            policy.finish(now,false);
        }
    }
    if (policy.consumed && policy.phase==Phase::finished) {
        monitoring_finished=true; result_phase="finished";
    }
    if (has_pending) {
        has_pending=false;
        const Pace selected_pace=effective_pace(pending.pace(),pending.action);
        if(selected_pace!=Pace::automatic && (!movement_pace_verified
                || (pending.action==19 && (!world_offers.find(pending.reserved)
                    || std::strcmp(world_recipes[world_offers.find(pending.reserved)->recipe].name,"move_to"))))) {
            policy.phase=Phase::rejected;++policy.generation;outcome="movement_pace_unavailable";
            result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
        }
        if (pending.action==20 && (!valid_destination(pending_position,obs.movement) || !position_contract())) {
            policy.phase=Phase::rejected; ++policy.generation; outcome="position_target_invalid_before_dispatch";
            result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
        }
        if (pending.action==3 && movement_block(obs.movement)) {
            policy.phase=Phase::rejected; ++policy.generation; outcome="hand_target_changed_before_dispatch";
            result_phase="rejected";
            ReleaseSRWLockExclusive(&lock); return;
        }
        if (policy.dispatch(pending,now)) {
            requested_pace=selected_pace;
            // Native AI uses this same agenda constructor. The fixed QA recipes
            // are idle-with-player/LookConfused and tiredness/SleepOnTheSpot.
            // These sequences need no
            // external target. The engine's normal plan contract still requires
            // a belief, so obtain its own self-belief through the engine interface.
            // No script ownership, VM operands or learned values are patched.
            using GetBelief = void*(__thiscall*)(void*,void*,void*);
            using SetAgenda = void(__thiscall*)(void*,int,void*,int,void*,void*,int,int);
            const auto get_belief=reinterpret_cast<GetBelief>(0x4D7BD0);
            const auto set_agenda=reinterpret_cast<SetAgenda>(0x4C4450);
            requested_plan=pending.action==20?1:pending.action==5?122:pending.action==4?161:pending.action==2?313:pending.action==3?89:92;
            requested_action=pending.action==20?"move_to_position":pending.action==5?"approach_object":pending.action==4?"wake_up":pending.action==2?"sleep_here":pending.action==3?"approach_hand":"look_confused";
            action_timeout_ms=requested_plan==1?180000u:requested_plan==313?120000u:(requested_plan==89 || requested_plan==122)?45000u:10000u;
#ifdef BWV_ACTION_CATALOG
            if (const auto* recipe=self_recipe(pending.action)) {
                requested_plan=recipe->plan;requested_action=recipe->name;action_timeout_ms=recipe->timeout_ms;
            }
#endif
#ifdef BWV_HELD_ACTIONS
            if(pending.action==19) {
                const auto* entry=world_offers.find(pending.reserved);
                if(!entry) {
                    policy.finish(now,false,Phase::rejected);outcome="world_target_changed_before_dispatch";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
                const auto& r=world_recipes[entry->recipe];
                requested_plan=r.plan;requested_action=r.name;
                action_timeout_ms=(r.plan==259 || r.plan==29 || r.plan==300)?180000:60000;
            }
            if(const auto* r=miracle_recipe(pending.action)) {
                requested_plan=r->plan;requested_action=r->name;action_timeout_ms=60000;
            }
            if(pending.action==15) {
                requested_plan=throw_plan;requested_action="throw_held_at";action_timeout_ms=45000;
            }
            if(pending.action==16) {
                requested_plan=pickup_plan;requested_action="pick_up";action_timeout_ms=45000;
                pickup_observed=false;executed_pickup={};pickup_completion.reset();
            }
            if (const auto* recipe=held_recipe(pending.action)) {
                if(pending.action==22)recipe=&held_home_recipe(obs.held_kind,obs.held_can_eat);
                requested_plan=recipe->plan;requested_action=recipe->name;action_timeout_ms=45000;
                if(pending.action==22)action_timeout_ms=180000;
            }
#endif
            movement_start_x=obs.movement.creature_x; movement_start_z=obs.movement.creature_z;
            requested_hand_x=obs.movement.hand_x; requested_hand_z=obs.movement.hand_z;
            if (pending.action==5) {
                executed_object=object_target.point;
                distance_to_object=executed_object.distance; object_unchanged=true;
                int32_t x=0,z=0;
                read(obs.creature+0x14,x);read(obs.creature+0x18,z);
                movement_start_x=x*(10./65536.);movement_start_z=z*(10./65536.);
            }
            initial_plan=obs.plan;
            initial_activity=obs.activity;
            executed_creature=obs.creature; executed_mental=obs.mental; executed_epoch=epoch;
            void* creature=reinterpret_cast<void*>(obs.creature);
            ++belief_calls;
            void* belief=get_belief(reinterpret_cast<void*>(obs.mental),creature,creature);
            const uintptr_t belief_address=reinterpret_cast<uintptr_t>(belief);
            if ((!type(belief_address,".?AVCreatureBeliefAboutCreature@@")
                    && !type(belief_address,".?AVCreatureBeliefSmall@@")
                    && !type(belief_address,".?AVCreatureBelief@@"))
                    || word(belief_address+0x30)!=obs.creature) {
                policy.finish(now,false,Phase::rejected); outcome="self_belief_unverified";
                monitoring_finished=true;
                result_phase="rejected";
                ReleaseSRWLockExclusive(&lock); return;
            }
#ifdef BWV_OBJECT_MOVEMENT
            if (pending.action==5) {
                const auto target=inspect_object_target(obs.creature,obs.mental,executed_object.object);
                if (!object_point_valid(target) || target.object!=executed_object.object
                        || target.belief!=executed_object.belief || target.table!=executed_object.table
                        || target.x!=executed_object.x || target.z!=executed_object.z) {
                    policy.finish(now,false,Phase::rejected); outcome="object_changed_before_dispatch";
                    monitoring_finished=true;result_phase="rejected";
                    ReleaseSRWLockExclusive(&lock);return;
                }
            }
#endif
            void* target=pending.action==5?reinterpret_cast<void*>(executed_object.belief):belief;
            void* secondary_target=nullptr;
            bool move_to_container=false;
            PositionDestination container_destination;
            TeleportRoute teleport_route;
            uintptr_t teleport_reaction_object=0;
            bool fish_to_hand=false;
            PositionDestination fish_destination;
#ifdef BWV_HELD_ACTIONS
            if(pending.action==19) {
                const auto* entry=world_offers.find(pending.reserved);
                bool valid=entry!=nullptr;
                if(valid) {
                    const auto& r=world_recipes[entry->recipe];
                    const auto actual_held=word(word(obs.creature+0x160)+0x28);
                    const auto current_seed=inspect_held_miracle(actual_held,type,word,
                        [](uintptr_t p,uint32_t& v){return read(p,v);});
                    const bool hand_valid=r.held_category
                        ? current_seed.verified && (current_seed.categories&r.held_category)
                            && actual_held==held_target.object && word(actual_held)==held_target.table
                            && entry->held_generation==held_target.token()
                            && entry->held_spell==current_seed.spell && entry->held_seed==current_seed.seed
                            && entry->held_power==current_seed.power
                        : !obs.held && !actual_held;
                    const MiracleRecipe check{19,r.plan,r.spell,r.name,r.label,r.constructor};
                    valid=hand_valid && miracle_contract(check) && (!r.spell || (word(0x9D16A8+r.plan*80)==r.spell && world_miracle_allowed(obs.mental,check)))
                        && word(entry->target.object)==entry->target.table
                        && (entry->self ? r.self && entry->target.object==obs.creature
                            : world_target_supported(r,entry->target,type)
                                && (entry->target.kind==30 || (movement_belief(entry->target.belief)
                                    && word(entry->target.belief+0x30)==entry->target.object)))
                        && (!entry->target.player_temple || is_player_temple(entry->target.object,obs.creature));
                    if(valid && !entry->self)target=reinterpret_cast<void*>(entry->target.belief);
                    if(valid && r.plan==259)valid=held_home_available(obs.creature,word(0xD0195C),
                        [](uintptr_t p,auto& v){return read(p,v);});
                    if(valid && r.fish_to_hand) {
                        fish_destination={float(obs.movement.hand_x),float(obs.movement.hand_z)};
                        valid=position_contract() && valid_destination(fish_destination,obs.movement);
                        fish_to_hand=valid;
                    }
                    if(valid && entry->target.kind==30) {
                        TeleportNetwork current;
                        valid=obs.movement.verified && read_teleport_network(word(obs.creature+0x1070),
                            obs.movement.width,obs.movement.depth,[](uintptr_t p,auto& v){return read(p,v);},type,current);
                        bool present=false;
                        for(size_t j=0;j<current.count;++j)present|=current.gates[j].object==entry->target.object
                            && current.gates[j].table==entry->target.table;
                        valid=valid && present;
                        if(valid && r.plan==122)target=belief;
                        if(valid && r.plan==134) {
                            valid=range(obs.creature+0x11CC,12) && current.route(entry->target.object,
                                obs.movement.creature_x,obs.movement.creature_z,teleport_route);
                            if(valid) {
                                teleport_reaction_object=teleport_reaction(teleport_route.entrance.object,
                                    [](uintptr_t p,auto& v){return read(p,v);},type);
                                valid=teleport_reaction_object && word(word(obs.creature)+0x9F0)==0x4F3A60
                                    && word(word(obs.creature)+0x990)==0x4F2680;
                                action_timeout_ms=180000;
                            }
                        }
                    }
                    if(valid && !entry->self && r.plan==122 && location_only_target(entry->target,type)) {
                        // Keep the selected identity, but resolve its LIVE position
                        // at dispatch. The stock object approach silently skips
                        // Citadel/Flock containers after its Object downcast fails.
                        valid=position_contract() && resolve_position_target(entry->target.object,obs.movement,
                            [](uintptr_t address,int32_t& value){return read(address,value);},container_destination);
                        if(valid) {move_to_container=true;requested_plan=1;action_timeout_ms=180000;}
                    }
                }
                if(!valid) {
                    policy.finish(now,false,Phase::rejected);outcome="world_target_or_learning_changed_before_dispatch";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
            }
            if(const auto* r=miracle_recipe(pending.action)) {
                const auto* entry=pending.action==18?pickup_targets.find(pending.reserved):nullptr;
                if(!miracle_contract(*r) || !miracle_learned(obs.mental,*r) || obs.held
                    || word(word(obs.creature+0x160)+0x28)
                    || (pending.action==18 && (!entry || entry->point.kind!=1
                        || !type(entry->point.object,".?AVTree@@") || word(entry->point.object)!=entry->point.table
                        || !movement_belief(entry->point.belief) || word(entry->point.belief+0x30)!=entry->point.object))) {
                    policy.finish(now,false,Phase::rejected);outcome="miracle_or_target_changed_before_dispatch";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
                if(entry) target=reinterpret_cast<void*>(entry->point.belief);
            }
            if(pending.action==16) {
                const auto* entry=pickup_targets.find(pending.reserved);
                if (!entry || !pickup_contract() || obs.held || word(word(obs.creature+0x160)+0x28)
                        || word(entry->point.object)!=entry->point.table
                        || !movement_belief(entry->point.belief)
                        || word(entry->point.belief+0x30)!=entry->point.object) {
                    policy.finish(now,false,Phase::rejected);outcome="pickup_target_changed_before_dispatch";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
                executed_pickup=entry->point;
                inspect_pickup(obs.creature,executed_pickup);
                if(executed_pickup.pickup_possible==0) {
                    policy.finish(now,false,Phase::rejected);
                    outcome=executed_pickup.pickup_reason?executed_pickup.pickup_reason:"engine_pickup_not_allowed";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
                target=reinterpret_cast<void*>(entry->point.belief);
            }
            if(pending.action==15) {
                const auto* entry=throw_targets.find(pending.reserved);
                if (!entry || !throw_contract() || throw_targets.held!=held_target.token()
                        || word(word(obs.creature+0x160)+0x28)!=held_target.object
                        || word(held_target.object)!=held_target.table
                        || word(entry->point.object)!=entry->point.table
                        || !movement_belief(entry->point.belief)
                        || word(entry->point.belief+0x30)!=entry->point.object) {
                    policy.finish(now,false,Phase::rejected);outcome="throw_target_changed_before_dispatch";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
                target=reinterpret_cast<void*>(entry->point.belief);
                ++belief_calls;
                secondary_target=get_belief(reinterpret_cast<void*>(obs.mental),creature,
                    reinterpret_cast<void*>(held_target.object));
                const auto projectile=reinterpret_cast<uintptr_t>(secondary_target);
                if (!movement_belief(projectile) || word(projectile+0x30)!=held_target.object
                        || word(word(obs.creature+0x160)+0x28)!=held_target.object
                        || word(held_target.object)!=held_target.table) {
                    policy.finish(now,false,Phase::rejected);outcome="throw_projectile_belief_unverified";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
            }
            if (held_recipe(pending.action)) {
                const auto held=held_target.object;
                if (!held || held_target.token()!=pending.reserved || word(word(obs.creature+0x160)+0x28)!=held
                        || word(held)!=held_target.table) {
                    policy.finish(now,false,Phase::rejected);outcome="held_object_changed_before_dispatch";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
                // Placement and untargeted discard use the physical hand.
                if (pending.action!=12 && pending.action!=21) {
                    ++belief_calls;
                    target=get_belief(reinterpret_cast<void*>(obs.mental),creature,reinterpret_cast<void*>(held));
                    const auto address=reinterpret_cast<uintptr_t>(target);
                    if (!movement_belief(address) || word(address+0x30)!=held
                            || word(word(obs.creature+0x160)+0x28)!=held || word(held)!=held_target.table) {
                        policy.finish(now,false,Phase::rejected);outcome="held_object_belief_unverified";
                        monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                    }
                }
            }
#endif
            requested_body_animation=-1;body_animation_observed=false;body_animation_duration=0;
            if(pending.action==19) {
                const auto* entry=world_offers.find(pending.reserved);
                if(entry) requested_body_animation=world_recipes[entry->recipe].body_animation;
            }
            if(requested_body_animation>=0) {
                const uintptr_t body=word(word(obs.creature+0x160)+0x58);
                uintptr_t clip=0;
                if(individual_gesture_argument(requested_body_animation) && type(body,".?AVLH3DCreature@@")) {
                    using GetAnimation=void*(__thiscall*)(void*,int32_t,int32_t);
                    ++calls;
                    clip=reinterpret_cast<uintptr_t>(reinterpret_cast<GetAnimation>(0x619650)(
                        reinterpret_cast<void*>(body),requested_body_animation,1));
                }
                if(!clip || !read(clip,body_animation_duration) || !body_animation_duration || body_animation_duration>60000) {
                    policy.finish(now,false,Phase::rejected);outcome="body_animation_unavailable_for_creature";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
            }
            if(pending.action==19 && requested_plan==186) {
                const MiracleRecipe check{19,186,9,"cast_megablast_pu2","CastExplosionPU2",0x4A6650};
                uint32_t counter=0;
                if(!miracle_counter(obs.mental,check,counter) || (!miracle_learning_observed(counter)
                        && !unlearned_miracle_test.consume(186,9,counter))) {
                    policy.finish(now,false,Phase::rejected);outcome="unlearned_miracle_test_unavailable";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
            }
            ++calls;
            agenda_dispatched=true;
            // SetAgenda invokes native subaction cleanup. For Sleep that calls
            // its exit callback before clearing the sequence; the controller
            // completes its own exit animation. No direct wake-state writes.
            // No tiredness, exhaustion, energy or other physiological value is written.
            // Requested work uses the native obey-player goal instead of idle
            // with player. Sleep remains explicit; no needs or fatigue are edited.
            movement_pace.release(epoch,obs.creature,read_pace,set_pace);
            command_priority.release(epoch,inspect_command_priority(obs.creature),"replaced",clear_command_priority);
#ifdef BWV_HELD_ACTIONS
            mushroom_priority.release(epoch,inspect_command_priority(obs.creature),"replaced_by_command",clear_command_priority);
            if(mushroom_consumption.pending)mushroom_consumption.cancel("replaced_by_command");
#endif
            PaceReading before_pace;
            const bool pace_ready=read_pace(obs.creature,before_pace);
            if(requested_plan==134 && teleport_route.entrance.object) {
                // Use the full native reaction setup. It cleans up the old
                // agenda, creates the entrance belief and registers the creature
                // with the gate's reaction, which keeps its route alive.
                using SetupTeleport=void(__thiscall*)(void*,void*,void*);
                ++belief_calls;
                reinterpret_cast<SetupTeleport>(0x4F3A60)(creature,
                    reinterpret_cast<void*>(teleport_route.entrance.object),
                    reinterpret_cast<void*>(teleport_reaction_object));
                if(word(obs.creature+0x94)!=teleport_reaction_object || word(obs.mental+0xF60)!=134) {
                    policy.finish(now,false,Phase::rejected);outcome="teleport_reaction_registration_failed";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
                // Setup uses the old onward goal. Replace it with the requested
                // EXIT after setup; a second SetAgenda would remove the reaction.
                // GoToTeleport and MoveByTeleport still perform actual travel.
                struct MapPoint { int32_t x,z;float altitude; } destination{
                    teleport_route.destination.x,teleport_route.destination.z,teleport_route.destination.altitude};
                static_assert(sizeof(MapPoint)==12);
                std::memcpy(reinterpret_cast<void*>(obs.creature+0x11CC),&destination,sizeof(destination));
                using RegisterDestination=void(__thiscall*)(void*,void*,const MapPoint*);
                ++calls;
                reinterpret_cast<RegisterDestination>(0x5FC6A0)(
                    reinterpret_cast<void*>(teleport_route.entrance.object),creature,&destination);
                if(pending.player_command())command_priority.begin(epoch,GetTickCount64(),
                    inspect_command_priority(obs.creature),pending.night_priority());
            } else if(pending.player_command()) {
                // Native forced-agenda wrapper: normal SetAgenda cleanup plus
                // Mental+0x1C14. AI selection honors it; native leash input can
                // still replace the agenda. The lease only clears our own flag.
                using ForceAgenda=void(__thiscall*)(void*,int,void*,int,void*,void*);
                reinterpret_cast<ForceAgenda>(0x4C4580)(creature,pending.action==2?8:24,
                    belief,requested_plan,target,secondary_target);
                command_priority.begin(epoch,GetTickCount64(),inspect_command_priority(obs.creature),pending.night_priority());
            } else set_agenda(creature,pending.action==2?8:24,belief,requested_plan,target,secondary_target,1,1);
            // Inspection can play a held-item gesture in an intermediate tool
            // round. Keep the turn's pause over our own command; the broker
            // releases it after the final selection or speech-only answer.
            if(thinking_pause.active)thinking_agenda=inspect_command_priority(obs.creature);
#ifdef BWV_HELD_ACTIONS
            if(requested_plan==259 || requested_plan==29 || requested_plan==300) {
                const auto sequence=obs.mental+0xFA8;bool adjusted=false;
                const bool prepared=prepare_fishing_approach(sequence,obs.creature,
                    [](uintptr_t p,auto& v){return read(p,v);},type,[&](const FishingPoint& point) {
                        using GetBody=void*(__thiscall*)(void*);
                        using Valid=int(__cdecl*)(const FishingPoint*);
                        calls+=2;reinterpret_cast<GetBody>(0x477850)(creature);
                        return reinterpret_cast<Valid>(0x483850)(&point)!=0;
                    },[&](const FishingPoint& from,FishingPoint& to) {
                        using Repair=void(__cdecl*)(const FishingPoint*,FishingPoint*);
                        ++calls;reinterpret_cast<Repair>(0x4839D0)(&from,&to);
                    },[&](uint32_t index,const FishingPoint& point,float radius) {
                        struct Argument {uintptr_t table;FishingPoint point;float radius;} argument{0x8CEFEC,point,radius};
                        static_assert(sizeof(Argument)==20);
                        using SetArgument=void(__thiscall*)(void*,void*,uint32_t);
                        ++calls;reinterpret_cast<SetArgument>(0x4FF820)(&argument,reinterpret_cast<void*>(sequence),index);
                        return true;
                    },adjusted);
                fishing_trace.approach=prepared?(adjusted?"native_shore_adjustment":"already_reachable"):"unavailable";
                if(!prepared) {
                    using Clear=void(__thiscall*)(void*);
                    ++calls;reinterpret_cast<Clear>(0x4FF5C0)(reinterpret_cast<void*>(sequence));
                    command_priority.release(epoch,inspect_command_priority(obs.creature),"fishing_setup_failed",clear_command_priority);
                    policy.finish(now,false,Phase::rejected);outcome="fishing_approach_unavailable";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
                fishing_trace.observe(0,0,8,false);
            }
            if(fish_to_hand) {
                const auto sequence=obs.mental+0xFA8;
                const bool configured=configure_fish_delivery(sequence,obs.creature,
                    [](uintptr_t p,auto& v){return read(p,v);},type,[&](uint32_t index,float radius) {
                        struct MapPoint {int32_t x,z;float altitude;} map{
                            static_cast<int32_t>(fish_destination.x*(65536./10.)),
                            static_cast<int32_t>(fish_destination.z*(65536./10.)),0};
                        using GroundHeight=float(__thiscall*)(void*);
                        const float y=reinterpret_cast<GroundHeight>(0x803090)(&map);
                        if(!std::isfinite(y))return false;
                        struct PointArgument {uintptr_t table;float x,y,z,radius;} argument{
                            0x8CEFEC,fish_destination.x,y,fish_destination.z,radius};
                        static_assert(sizeof(PointArgument)==20);
                        using SetArgument=void(__thiscall*)(void*,void*,uint32_t);
                        ++calls;
                        reinterpret_cast<SetArgument>(0x4FF820)(&argument,reinterpret_cast<void*>(sequence),index);
                        return true;
                    });
                if(!configured) {
                    // Do not fall back to a home trip if retargeting failed.
                    using Clear=void(__thiscall*)(void*);
                    ++calls;reinterpret_cast<Clear>(0x4FF5C0)(reinterpret_cast<void*>(sequence));
                    command_priority.release(epoch,inspect_command_priority(obs.creature),"fishing_setup_failed",clear_command_priority);
                    policy.finish(now,false,Phase::rejected);outcome="fishing_delivery_sequence_unavailable";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
            }
            if(pending.action==21 || (pending.action>=23 && pending.action<=27)) {
                const uintptr_t sequence=obs.mental+0xFA8;
                const auto apply=[&](int32_t animation) {
                    struct IntegerArgument {uintptr_t table;int32_t value;} argument{0x8CF064,animation};
                    using SetArgument=void(__thiscall*)(void*,void*,uint32_t);
                    ++calls;
                    reinterpret_cast<SetArgument>(0x4FF6F0)(&argument,reinterpret_cast<void*>(sequence),0);
                };
                const bool sequence_ok=type(sequence,".?AVCreatureSubActionAgenda@@")
                    && word(sequence+0xC28)==obs.creature
                    && type(sequence+0x28,".?AVCreatureSubAction@@");
                const bool configured=sequence_ok && (pending.action==21
                    ? configure_held_discard(word(sequence+0x14),word(sequence+0xC),
                        word(sequence+0x30),word(sequence+0x38),apply)
                    : held_examination_variation.configure(pending.action,word(sequence+0x14),word(sequence+0xC),
                        word(sequence+0x30),word(sequence+0x38),apply));
                if(!configured) {
                    policy.finish(now,false,Phase::rejected);
                    outcome=pending.action==21?"discard_sequence_mismatch":"examination_sequence_mismatch";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
            }
#endif
            if(pending.action==20 || move_to_container) {
                executed_position=move_to_container?container_destination:pending_position;
                if(!append_position_movement(obs.creature,obs.mental,executed_position)) {
                    policy.finish(now,false,Phase::rejected);outcome="position_sequence_unavailable";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
            }
            if(selected_pace!=Pace::automatic && pace_ready) {
                movement_pace.begin(epoch,obs.creature,requested_plan,selected_pace,before_pace);
                const auto sequence=obs.mental+0xFA8;
                const auto cursor=word(sequence+0xC),count=word(sequence+0x14);
                if(cursor<count && count<32)movement_pace.update(epoch,obs.creature,word(obs.mental+0xF60),
                    word(sequence+0x30+cursor*0x60),obs.scripted,read_pace,set_pace);
            }
            if(replacing_action.action && replacing_action.request) {
                if(interrupted_count==8) {
                    for(size_t i=1;i<8;++i)interrupted_actions[i-1]=interrupted_actions[i];
                    --interrupted_count;
                }
                interrupted_actions[interrupted_count++]=replacing_action;
                replacing_action={};
            }
            if(requested_body_animation>=0) {
                const uintptr_t sequence=obs.mental+0xFA8;
                const bool sequence_ok=type(sequence,".?AVCreatureSubActionAgenda@@")
                    && word(sequence+0xC28)==obs.creature
                    && type(sequence+0x28,".?AVCreatureSubAction@@")
                    && individual_gesture_sequence(word(sequence+0x14),word(sequence+0xC),
                        word(sequence+0x30),word(sequence+0x38));
                if(!sequence_ok) {
                    // Do not edit an unexpected sequence or repeat the command.
                    policy.finish(now,false,Phase::rejected);outcome="body_animation_sequence_mismatch";
                    monitoring_finished=true;result_phase="rejected";ReleaseSRWLockExclusive(&lock);return;
                }
                struct IntegerArgument { uintptr_t table; int32_t value; } argument{0x8CF064,requested_body_animation};
                using SetArgument=void(__thiscall*)(void*,void*,uint32_t);
                ++calls;
                reinterpret_cast<SetArgument>(0x4FF6F0)(&argument,reinterpret_cast<void*>(sequence),0);
            }
            current_plan=static_cast<uint32_t>(word(obs.mental+0xF60));
            observed_plan=current_plan==requested_plan;
            uint16_t flags=0; read(obs.creature+0x24,flags);
            script_after=(flags&0x400)!=0;
            policy.phase=observed_plan?Phase::observed:Phase::accepted;
            outcome=script_after?"unexpected_script_flag":"native_agenda_call_returned";
            if (script_after) {
                policy.finish(now,false); monitoring_finished=true; result_phase="finished";
            }
        }
        if (policy.phase==Phase::rejected) result_phase="rejected";
    }
    ReleaseSRWLockExclusive(&lock);
}

std::string json() {
    AcquireSRWLockShared(&lock);
    std::string fishing_json;
    if(requested_action && !std::strncmp(requested_action,"fish_",5)) {
        fishing_json=",\"fishing_delivery\":{\"approach\":\""+std::string(fishing_trace.approach)+"\",\"steps\":[";
        for(uint32_t i=0;i<fishing_trace.count;++i) {
            const auto& step=fishing_trace.steps[i];if(i)fishing_json+=",";
            fishing_json+="{\"elapsed_ms\":"+std::to_string(step.elapsed_ms)+",\"index\":"+std::to_string(step.index)
                +",\"subaction\":"+std::to_string(step.action)+",\"holding\":"+(step.holding?"true":"false")+"}";
        }
        fishing_json+="]}";
    }
    const char* move_block=movement_block(current_movement);
    std::string candidates="[";
    if (policy.available && !has_pending) {
        const auto append=[&](const char* name) {
            if (candidates.size()>1) candidates+=",";
            candidates+="\""+std::string(name)+"\"";
        };
        if (policy.allowed_actions&1) append("look_confused");
        if (policy.allowed_actions&2) append("sleep_here");
        if ((policy.allowed_actions&4) && !move_block) append("approach_hand");
        if (policy.allowed_actions&8) append("wake_up");
        if (policy.allowed_actions&16) append("approach_object");
        if (policy.catalog_enabled && self_catalog_verified)
            for (const auto& recipe:self_recipes)
                if (recipe.action>=6 && (policy.allowed_actions&(1u<<(recipe.action-1)))) append(recipe.name);
        if (policy.held_enabled && held_catalog_verified)
            for (const auto& recipe:held_recipes)
                if (policy.allowed_actions&(1u<<(recipe.action-1))) append(recipe.name);
        if(policy.allowed_actions&(1u<<21)) append("bring_home");
        if(policy.allowed_actions&(1u<<14)) append("throw_held_at");
        if(policy.allowed_actions&(1u<<15)) append("pick_up");
        if(policy.allowed_actions&(1u<<19)) append("move_to_position");
        for(const auto& r:miracle_recipes)
            if(policy.allowed_actions&(1u<<(r.action-1))) append(r.name);
        if(policy.allowed_actions&(1u<<18)) {
            bool seen[world_recipe_count]{};
            for(size_t i=0;i<world_offers.count;++i) {
                const auto recipe=world_offers.entries[i].recipe;
                if(!seen[recipe]) {append(world_recipes[recipe].name);seen[recipe]=true;}
            }
        }
    }
    candidates+="]";
    std::string interruptions="[";
    for(size_t i=0;i<interrupted_count;++i) {
        const auto& e=interrupted_actions[i];if(i)interruptions+=",";
        interruptions+="{\"action\":\""+std::string(e.action)+"\",\"request_at_uptime_ms\":"+
            std::to_string(e.request)+",\"replacement_request_at_uptime_ms\":"+std::to_string(e.replacement)+"}";
    }
    interruptions+="]";
    std::string targets="[";
    for(size_t i=0;i<throw_targets.count;++i) {
        if(i) targets+=",";
        const auto& entry=throw_targets.entries[i];
        targets+="{\"token\":"+std::to_string(entry.token)+",\"kind\":\""+throw_kind_name(entry.point.kind)
            +"\",\"object_handle\":"+std::to_string(entry.point.object)
            +",\"distance_engine_units\":"+std::to_string(entry.point.distance)
            +(entry.point.distance_to_god_hand<0?"":",\"distance_to_god_hand_engine_units\":"+std::to_string(entry.point.distance_to_god_hand))
            +(entry.point.on_fire<0?"":entry.point.on_fire?",\"on_fire\":true":",\"on_fire\":false")+"}";
    }
    targets+="]";
    std::string pickups="[";
    for(size_t i=0;i<pickup_targets.count;++i) {
        if(i) pickups+=",";
        const auto& entry=pickup_targets.entries[i];
        pickups+="{\"token\":"+std::to_string(entry.token)+",\"kind\":\""+throw_kind_name(entry.point.kind)
            +"\",\"object_handle\":"+std::to_string(entry.point.object)
            +pickup_metadata(entry.point)
            +",\"distance_engine_units\":"+std::to_string(entry.point.distance)
            +(entry.point.distance_to_god_hand<0?"":",\"distance_to_god_hand_engine_units\":"+std::to_string(entry.point.distance_to_god_hand))
            +(entry.point.on_fire<0?"":entry.point.on_fire?",\"on_fire\":true":",\"on_fire\":false")+"}";
    }
    pickups+="]";
    std::string world="[";
    for(size_t i=0;i<world_offers.count;++i) {
        const auto& e=world_offers.entries[i];if(i)world+=",";
        world+="{\"token\":"+std::to_string(e.token)+",\"action\":\""+world_recipes[e.recipe].name
            +"\",\"kind\":\""+(e.self?"self":throw_kind_name(e.target.kind))
            +"\",\"object_handle\":"+std::to_string(e.target.object)
            +pickup_metadata(e.target)
            +",\"distance_engine_units\":"+std::to_string(e.target.distance)
            +(e.held_generation?",\"held_generation\":"+std::to_string(e.held_generation)
                +",\"held_spell\":"+std::to_string(e.held_spell):"")
            +(e.target.distance_to_god_hand<0?"":",\"distance_to_god_hand_engine_units\":"+std::to_string(e.target.distance_to_god_hand))
            +(e.target.on_fire<0?"":e.target.on_fire?",\"on_fire\":true":",\"on_fire\":false")
            +(!e.self && !std::strcmp(world_recipes[e.recipe].name,"move_to") && context_position(e.target)
                ?",\"position\":{\"x\":"+std::to_string(e.target.x)+",\"z\":"+std::to_string(e.target.z)+"}":"")
            +(e.target.player_temple?",\"player_temple\":true":"")+"}";
    }
    world+="]";
    std::string position_info="{\"version\":11,\"verified\":"+std::string(policy.held_enabled && current_movement.verified && position_contract()?"true":"false")
        +",\"map_size\":{\"x\":"+std::to_string(current_movement.width)+",\"z\":"+std::to_string(current_movement.depth)+"}}";
    std::string out="{\"experimental_manual_only\":true,\"supported\":"+std::string(supported?"true":"false")+
        ",\"timing\":{\"catalog_interval_ms\":"+std::to_string(CatalogRefresh::interval_ms)
            +",\"inspection\":"+inspection_work.json()+",\"catalog\":"+catalog_work.json()
            +",\"target_pool\":"+target_pool_work.json()+",\"pickup_checks\":"+pickup_check_work.json()
            +",\"recipes\":"+recipe_work.json()+",\"legacy_target\":"+legacy_target_work.json()+"}"+
        ",\"position_movement\":"+position_info+
        ",\"movement_pace\":{\"version\":"+(movement_pace_verified?"1":"0")+",\"hand_default\":\"run\",\"active\":"+(movement_pace.creature?"true":"false")
            +",\"requested\":\""+(requested_pace==Pace::run?"run":requested_pace==Pace::walk?"walk":"auto")+"\",\"applied\":"+(movement_pace.written?"true":"false")
            +(movement_pace_verified?",\"controller_pace\":"+std::to_string(current_pace.current)+",\"walk_preset\":"+std::to_string(current_pace.walk)+",\"run_preset\":"+std::to_string(current_pace.run):"")+"}"+
        ",\"transport_ready\":"+(transport_ready?"true":"false")+
        ",\"phase\":\""+name(policy.phase)+"\",\"reason\":\""+policy.reason+"\",\"epoch\":"+
        std::to_string(policy.epoch)+",\"generation\":"+std::to_string(policy.generation)+
        ",\"offered_at_uptime_ms\":"+std::to_string(policy.offered_at)+
        ",\"conversation_actions\":{\"version\":2,\"max_offer_age_ms\":30000,\"hand_target_generation\":"+
        std::to_string(policy.target_generation)+",\"creature_handle\":"+std::to_string(last_creature)+
        ",\"mental_handle\":"+std::to_string(last_mental)+"}"+
        ",\"object_movement\":{\"protocol\":4,\"manual_only\":true,\"target_generation\":"+
        std::to_string(policy.object_target_generation)+",\"kind\":"+
        (object_target.token()?(object_target.point.kind==1?"\"tree\"":"\"rock\""):"null")+
        ",\"distance_engine_units\":"+(object_target.token()?std::to_string(object_target.point.distance):"null")+
        ",\"target_unchanged_after_dispatch\":"+(object_unchanged?"true":"false")+
        ",\"distance_to_requested_object\":"+(object_unchanged?std::to_string(distance_to_object):"null")+
        ",\"arrival\":{\"criterion\":\"conservative_native_tree_radius\",\"verified\":"+
        (object_arrival.radius_verified?"true":"false")+
        ",\"body_radius_engine_units\":"+(object_arrival.radius_verified?std::to_string(object_arrival.body_radius):"null")+
        ",\"approach_allowance_engine_units\":"+(object_arrival.allowance_verified?std::to_string(object_arrival.allowance):"null")+
        ",\"confirmed_radius_engine_units\":"+(object_arrival.radius_verified?std::to_string(object_arrival.confirmed_radius):"null")+"}"+
        ",\"validation\":\""+object_target_validation+"\",\"reachability_verified\":false}"+
        ",\"throw_action\":{\"version\":7,\"verified\":"+(throw_verified?"true":"false")+
        ",\"held_generation\":"+std::to_string(throw_targets.held)+",\"targets\":"+targets+"}"+
        ",\"pickup_action\":{\"version\":8,\"verified\":"+(pickup_verified?"true":"false")+
        ",\"hand_empty\":"+(held_target.token()?"false":"true")+",\"targets\":"+pickups+"}"+
        ",\"miracle_actions\":{\"version\":9,\"verified\":"+(miracle_verified?"true":"false")+
        ",\"heal_learning_observed\":"+((learned_miracle_mask&(1u<<16))?"true":"false")+
        ",\"water_learning_observed\":"+((learned_miracle_mask&(1u<<17))?"true":"false")+"}"+
        ",\"target_lookup_protocol\":12"+
        ",\"lookup_observed_object\":"+std::to_string(lookup_observed_object)+
        ",\"world_actions\":{\"version\":10,\"available_recipes\":"+supported_world_recipes+",\"offers\":"+world+"}"+
        ",\"unlearned_miracle_qa\":{\"enabled\":"+(unlearned_miracle_test.enabled?"true":"false")+
        ",\"used\":"+(unlearned_miracle_test.used?"true":"false")+
        ",\"spell\":\"megablast_extreme\",\"learning_before\":"+(unlearned_miracle_test.used?"0":"null")+
        ",\"learning_after\":"+(unlearned_counter_after_valid?std::to_string(unlearned_counter_after):"null")+"}"+
        ",\"body_gesture\":{\"requested_animation\":"+std::to_string(requested_body_animation)+
        ",\"clip_duration_ms\":"+std::to_string(body_animation_duration)+
        ",\"playback_observed\":"+(body_animation_observed?"true":"false")+"}"+
#ifdef BWV_HELD_ACTIONS
        ",\"cut_content\":{\"protocol\":13,\"enabled\":"+(cut_content_enabled?"true":"false")+"}"+
        ",\"mushroom_effect\":{\"enabled\":"+(cut_content_enabled?"true":"false")+",\"pending\":"+(mushroom_consumption.pending?"true":"false")+
        ",\"reading_valid\":"+(mushroom_reading_valid?"true":"false")+
        ",\"mushrooms_eaten\":"+(mushroom_reading_valid?std::to_string(mushroom_consumption.counter):"null")+
        ",\"native_sequence_active\":"+(mushroom_sequence_active?"true":"false")+
        ",\"eye_mode\":"+(mushroom_reading_valid?std::to_string(mushroom_eye_mode):"null")+
        ",\"effect_calls\":"+std::to_string(mushroom_effect_calls)+
        ",\"status\":\""+mushroom_consumption.status+"\",\"priority_active\":"+(mushroom_priority.active?"true":"false")+
        ",\"priority_remaining_ms\":"+std::to_string(mushroom_priority.remaining(GetTickCount64()))+
        ",\"priority_status\":\""+mushroom_priority.status+"\"}"+
#endif
        ",\"held_miracle\":{\"version\":1,\"verified\":"+(held_miracle.verified?"true":"false")+
        ",\"held_generation\":"+std::to_string(held_target.token())+
        ",\"spell\":"+std::to_string(held_miracle.spell)+",\"bubble_spell\":"+std::to_string(held_miracle.bubble_spell)+"}"+
        ",\"hand_preparation_protocol\":1,\"held_actions\":{\"version\":6,\"verified\":"+
        ((policy.held_enabled && held_catalog_verified)?"true":"false")+
        ",\"target_generation\":"+std::to_string(held_target.token())+
        ",\"discard_version\":1"+
        ",\"examine_version\":1,\"held_gesture_version\":1,\"bring_home_version\":1,\"can_bring_home\":"+(held_target.can_bring_home?"true":"false")+
        ",\"bring_home_unavailable_reason\":"+(held_target.home_blocker?"\""+std::string(held_target.home_blocker)+"\"":"null")+
        ",\"edibility_version\":1,\"can_eat\":"+(held_target.can_eat?"true":"false")+
        ",\"kind\":\""+held_kind_name(held_target.kind)+"\"}"+
        ",\"self_action_catalog\":{\"version\":5,\"verified\":"+
        ((policy.catalog_enabled && self_catalog_verified)?"true":"false")+"},\"wake_protocol\":3,\"allowed_action_mask\":"+std::to_string(policy.allowed_actions)+
        ",\"action_lifecycle\":{\"version\":1,\"repeated_actions_enabled\":"+(policy.repeat_enabled?"true":"false")+
        ",\"in_flight\":"+((policy.phase==Phase::queued || (policy.consumed && !policy.terminal))?"true":"false")+
        ",\"cooldown_ms\":"+std::to_string(Policy::cooldown_ms)+
        ",\"cooldown_remaining_ms\":"+std::to_string(policy.cooldown_remaining(GetTickCount64()))+
        ",\"attempts\":"+std::to_string(policy.attempts)+"}"+
        ",\"candidate\":"+(policy.phase==Phase::ready && (policy.allowed_actions&1)?"\"look_confused\"":"null")+
        ",\"candidates\":"+(policy.phase==Phase::ready?candidates:"[]")+
        ",\"priority_commands\":{\"version\":1,\"available\":"+
        ((policy.repeat_enabled && policy.available && !has_pending)?"true":"false")+
        ",\"candidates\":"+candidates+"}"+
        ",\"player_command_priority\":{\"version\":1,\"available\":"+
        ((supported && policy.repeat_enabled)?"true":"false")+
        ",\"night_priority_version\":1,\"day_duration_ms\":"+std::to_string(CommandPriority::duration_ms)+
        ",\"night_duration_ms\":"+std::to_string(CommandPriority::night_duration_ms)+
        ",\"duration_ms\":"+std::to_string(command_priority.window_ms)+
        ",\"active\":"+(command_priority.active?"true":"false")+
        ",\"remaining_ms\":"+std::to_string(command_priority.remaining(GetTickCount64()))+
        ",\"status\":\""+command_priority.status+"\"}"+
        ",\"thinking_pause\":{\"version\":2,\"available\":"+(thinking_hook_installed?"true":"false")+
        ",\"active\":"+(thinking_pause.active?"true":"false")+
        ",\"control_taken\":"+(thinking_pause.control_taken?"true":"false")+
        ",\"control_revision\":"+std::to_string(thinking_pause.control_revision)+
        ",\"token\":"+std::to_string(thinking_pause.token)+
        ",\"skipped_decisions\":"+std::to_string(thinking_pause.skipped)+
        ",\"status\":\""+thinking_pause.status+"\"}"+
        ",\"interrupted_actions\":"+interruptions+
        fishing_json+
        ",\"conditions_allow_action\":"+(policy.available?"true":"false")+
        ",\"compatibility_method\":\""+compatibility_method+"\""+
        ",\"requested_action\":"+(requested_action?"\""+std::string(requested_action)+"\"":"null")+
        ",\"requested_target_kind\":"+(requested_target_kind?"\""+std::string(requested_target_kind)+"\"":"null")+
        ",\"accepted_at_uptime_ms\":"+std::to_string(policy.accepted_at)+
        ",\"request_at_uptime_ms\":"+std::to_string(request_at)+
        ",\"result_phase\":"+(result_phase?"\""+std::string(result_phase)+"\"":"null")+
        ",\"completion\":\""+completion+"\",\"result_current_creature\":"+
        ((executed_creature && executed_creature==last_creature && executed_mental==last_mental
            && executed_epoch==policy.epoch)?"true":"false")+
        ",\"engine_calls\":"+std::to_string(calls+belief_calls)+",\"agenda_calls\":"+std::to_string(calls)+
        ",\"belief_calls\":"+std::to_string(belief_calls)+",\"initial_plan\":"+std::to_string(initial_plan)+
        ",\"initial_activity\":{\"verified\":"+(initial_activity.verified?"true":"false")+
        ",\"controller_mode\":"+(initial_activity.verified?std::to_string(initial_activity.controller_mode):"null")+
        ",\"animation\":"+(initial_activity.verified?std::to_string(initial_activity.animation):"null")+
        ",\"current_subaction\":"+(initial_activity.verified?std::to_string(initial_activity.current_subaction):"null")+"}"+
        ",\"current_plan\":"+std::to_string(current_plan)+",\"plan_observed\":"+(observed_plan?"true":"false")+
        ",\"current_desire\":"+(current_desire==UINT32_MAX?"null":std::to_string(current_desire))+
        ",\"hand_mode\":"+(current_hand_mode==UINT32_MAX?"null":std::to_string(current_hand_mode))+
        ",\"activity\":{\"verified\":"+(current_activity.verified?"true":"false")+
        ",\"controller_mode\":"+(current_activity.controller_mode==UINT32_MAX?"null":std::to_string(current_activity.controller_mode))+
        ",\"animation\":"+(current_activity.animation==UINT32_MAX?"null":std::to_string(current_activity.animation))+
        ",\"sleep_in_sequence\":"+(current_activity.verified?(current_activity.sleep_in_sequence?"true":"false"):"null")+
        ",\"current_subaction\":"+(current_activity.current_subaction==UINT32_MAX?"null":std::to_string(current_activity.current_subaction))+
        ",\"validation\":\""+current_activity.validation+"\"}"+
        ",\"movement\":{\"verified\":"+(current_movement.verified?"true":"false")+
        ",\"validation\":\""+current_movement.validation+"\",\"interfaces\":"+std::to_string(current_movement.interfaces)+
        ",\"reason\":\""+(move_block?move_block:"verified_hand_target")+"\""+
        ",\"distance_to_hand_engine_units\":"+(current_movement.verified?std::to_string(current_movement.distance):"null")+
        (current_movement.verified?",\"creature_position\":{\"x\":"+std::to_string(current_movement.creature_x)+",\"z\":"+std::to_string(current_movement.creature_z)+"},\"god_hand_position\":{\"x\":"+std::to_string(current_movement.hand_x)+",\"z\":"+std::to_string(current_movement.hand_z)+"},\"map_size\":{\"x\":"+std::to_string(current_movement.width)+",\"z\":"+std::to_string(current_movement.depth)+"}":"")+
        ",\"displacement_engine_units\":"+std::to_string(movement_distance)+
        ",\"movement_observed\":"+(movement_observed?"true":"false")+"}"+
        ",\"script_owned\":"+(script_after?"true":"false")+",\"outcome\":\""+outcome+"\"}";
    ReleaseSRWLockShared(&lock); return out;
}

DWORD WINAPI serve(void*) {
    HANDLE token=nullptr; DWORD size=0;
    if (!OpenProcessToken(GetCurrentProcess(),TOKEN_QUERY,&token)) return 1;
    GetTokenInformation(token,TokenUser,nullptr,0,&size);
    std::vector<unsigned char> buffer(size);
    const bool got=GetTokenInformation(token,TokenUser,buffer.data(),size,&size)!=FALSE;
    CloseHandle(token); if (!got) return 2;
    LPWSTR sid=nullptr;
    if (!ConvertSidToStringSidW(reinterpret_cast<TOKEN_USER*>(buffer.data())->User.Sid,&sid)) return 3;
    // Same-user local client, including a normal-integrity CLI talking to a game
    // launched as administrator. Only fixed QA action codes are understood.
    std::wstring acl=L"D:P(A;;GRGW;;;"+std::wstring(sid)+L")S:(ML;;NW;;;ME)";
    LocalFree(sid);
    PSECURITY_DESCRIPTOR descriptor=nullptr;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(acl.c_str(),SDDL_REVISION_1,&descriptor,nullptr)) return 4;
    SECURITY_ATTRIBUTES attributes{sizeof(attributes),descriptor,FALSE};
    const std::wstring name=L"\\\\.\\pipe\\bwv-action-probe-"+std::to_wstring(GetCurrentProcessId());
    HANDLE pipe=CreateNamedPipeW(name.c_str(),PIPE_ACCESS_DUPLEX|FILE_FLAG_FIRST_PIPE_INSTANCE,
        PIPE_TYPE_MESSAGE|PIPE_READMODE_MESSAGE|PIPE_NOWAIT|PIPE_REJECT_REMOTE_CLIENTS,
        1,128,128,1000,&attributes);
    LocalFree(descriptor);
    if (pipe==INVALID_HANDLE_VALUE) return 5;
    AcquireSRWLockExclusive(&lock); transport_ready=true; ReleaseSRWLockExclusive(&lock);
    for (;;) {
        if (!ConnectNamedPipe(pipe,nullptr) && GetLastError()!=ERROR_PIPE_CONNECTED) { Sleep(20); continue; }
        struct Packet {Command command;PositionDestination destination;};
        static_assert(sizeof(Packet)==48);
        Packet packet{};auto& command=packet.command; DWORD bytes=0; bool read_ok=false;
        const uint64_t deadline=GetTickCount64()+1000;
        do {
            if (ReadFile(pipe,&packet,sizeof(packet),&bytes,nullptr)) {
                read_ok=bytes==(command.protocol()==11?sizeof(packet):sizeof(Command)); break; }
            if (GetLastError()!=ERROR_NO_DATA) break;
            Sleep(10);
        } while (GetTickCount64()<deadline);
        uint32_t accepted=0;
        AcquireSRWLockExclusive(&lock);
        const auto lookup_now=GetTickCount64();
        const bool thinking=read_ok && command.magic==0x42575041 && command.version==ThinkingPause::protocol;
        if(thinking && thinking_hook_installed) {
            if(command.action==0) {
                accepted=thinking_pause.end(command.epoch,command.generation,command.reserved)?1:0;
                if(accepted)thinking_owner=0;
            } else if(command.epoch==policy.epoch && command.reserved==last_creature
                    && latest_agenda.valid && latest_agenda.creature==last_creature
                    && policy.available && !has_pending && observation_active
                    && command.offered_at<=lookup_now && lookup_now-command.offered_at<=1500) {
                const bool already_active=thinking_pause.active;
                accepted=thinking_pause.begin(command.epoch,command.generation,lookup_now,command.action,
                                               last_creature,last_mental,latest_thinking_leash)?1:0;
                if(accepted && !already_active) {thinking_agenda=latest_agenda;thinking_owner=last_creature;}
            }
        }
        bool configured=false;
#ifdef BWV_HELD_ACTIONS
        configured=read_ok && command.magic==0x42575041 && command.version==13
            && command.action==0 && command.reserved<=1 && command.epoch==policy.epoch
            && command.offered_at<=lookup_now && lookup_now-command.offered_at<=1500;
        if(configured) {
            cut_content_enabled=command.reserved==1;accepted=1;
            mushroom_sample_at=0;catalog_refresh.initialized=false;
            ++policy.generation;
            for(auto& token:policy.world_tokens)token=0;
        }
#endif
        const bool lookup=read_ok && command.magic==0x42575041 && command.version==12
            && command.action==0 && command.reserved && command.epoch==policy.epoch
            && command.generation==policy.generation && command.offered_at<=lookup_now
            && lookup_now-command.offered_at<=1500 && last_creature;
        if(lookup) {
            lookup_object=command.reserved;lookup_creature=last_creature;
            lookup_observed_object=0;
            catalog_refresh.initialized=false; // also refresh repeated lookup of the same object
            lookup_epoch=policy.epoch;lookup_until=lookup_now+60000;accepted=1;
        }
        const bool displacing=command.interrupts() && agenda_dispatched
            && (!policy.terminal || !policy.verified_exit) && executed_epoch==policy.epoch
            && executed_creature==last_creature && executed_mental==last_mental;
        const InterruptedAction previous{requested_action,request_at,0};
        if (read_ok && !lookup && !configured && !thinking && !has_pending
                && (command.pace()==Pace::automatic || (movement_pace_verified
                    && (command.action!=19 || (world_offers.find(command.reserved)
                        && !std::strcmp(world_recipes[world_offers.find(command.reserved)->recipe].name,"move_to")))))
                && (command.action!=20 || (position_contract() && valid_destination(packet.destination,current_movement)))
                && (command.action!=3 || !movement_block(current_movement))
                && policy.queue(command,GetTickCount64())) {
            pending=command;pending_position=packet.destination; has_pending=true; accepted=1;
            request_at=GetTickCount64();
            replacing_action=displacing?InterruptedAction{previous.action,previous.request,request_at}:InterruptedAction{};
            observed_plan=false; agenda_dispatched=false; monitoring_finished=false;
            fishing_trace=FishingTrace{};
            movement_observed=false; movement_distance=0; distance_to_requested_hand=0;
            hand_changed_after_dispatch=false; initial_plan=0; initial_activity=Activity{};
            result_phase=nullptr; completion="unconfirmed"; outcome="queued_not_executed";
            requested_target_kind=nullptr;
            object_unchanged=false;distance_to_object=0;object_arrival=ObjectArrival{};
            requested_action=command.action==20?"move_to_position":command.action==5?"approach_object":command.action==4?"wake_up":command.action==2?"sleep_here":command.action==3?"approach_hand":"look_confused";
#ifdef BWV_ACTION_CATALOG
            if (const auto* recipe=self_recipe(command.action)) requested_action=recipe->name;
#endif
#ifdef BWV_HELD_ACTIONS
            if (const auto* recipe=held_recipe(command.action)) requested_action=recipe->name;
            if (command.action==15) requested_action="throw_held_at";
            if (command.action==16) requested_action="pick_up";
            if (const auto* r=miracle_recipe(command.action)) requested_action=r->name;
            if(command.action==19) {
                if(const auto* entry=world_offers.find(command.reserved)) {
                    requested_action=world_recipes[entry->recipe].name;
                    requested_target_kind=entry->self?"self":throw_kind_name(entry->target.kind);
                }
            }
            if(command.action==15)if(const auto* entry=throw_targets.find(command.reserved))
                requested_target_kind=throw_kind_name(entry->point.kind);
            if(command.action==16 || command.action==18)if(const auto* entry=pickup_targets.find(command.reserved))
                requested_target_kind=throw_kind_name(entry->point.kind);
            if(command.action==17)requested_target_kind="self";
#endif
            executed_epoch=policy.epoch; executed_creature=last_creature; executed_mental=last_mental;
        }
        ReleaseSRWLockExclusive(&lock);
        DWORD written=0; WriteFile(pipe,&accepted,sizeof(accepted),&written,nullptr);
        // Receipt confirms queueing only. Observer snapshots report execution.
        FlushFileBuffers(pipe); DisconnectNamedPipe(pipe);
    }
}
void initialize() {
    supported=fingerprint();
    if (!supported) { policy.reason="unsupported_executable"; return; }
    if(policy.repeat_enabled)thinking_hook_installed=install_thinking_hook();
    HANDLE worker=CreateThread(nullptr,0,serve,nullptr,0,nullptr);
    if (worker) CloseHandle(worker);
}
}
