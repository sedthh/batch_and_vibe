// Request-bound projectile/destination pairs. No engine code or raw IDs exposed.
#pragma once
#include <cstdint>
#include <cstddef>
#include <cmath>
namespace bwv_probe {
inline constexpr size_t object_target_capacity=16;
inline constexpr size_t known_target_capacity=2*object_target_capacity+1;
inline constexpr size_t world_target_capacity=2048;
inline constexpr double nearby_target_distance=120;
inline constexpr double known_target_distance=20000;
inline constexpr unsigned target_kind_count=28;
inline constexpr uint32_t all_object_kinds=(1u<<(target_kind_count+1))-2;
inline constexpr uint32_t portable_object_kinds=(1u<<1)|(1u<<2)|(1u<<3)|(1u<<7)
    |(1u<<8)|(1u<<9)|(1u<<10)|(1u<<11)|(1u<<26);
// Hurl takes the destination as the primary action belief and the projectile
// as the secondary action belief. ThrowObjectInHand instead reads the nearest
// god hand's position; it is not the targetless discard animation.
inline constexpr uint32_t throw_plan=15;
inline constexpr uintptr_t throw_constructor=0x49ADF0;
inline constexpr const char* throw_runtime_name="Hurl";
struct ThrowPoint {
    uintptr_t object=0,belief=0,table=0;
    unsigned kind=0;
    double distance=0;
    bool player_temple=false;
    int on_fire=-1;
    double distance_to_god_hand=-1;
    double x=0,z=0;
    int pickup_possible=-1;
    const char* pickup_reason=nullptr;
    uint32_t bubble_spell=0;
};
inline bool context_position(const ThrowPoint& point) {
    const bool kind=point.kind==4 || point.kind==5 || point.kind==6
        || (point.kind>=16 && point.kind<=24);
    return kind && std::isfinite(point.x) && std::isfinite(point.z)
        && point.x>0 && point.x<10240 && point.z>0 && point.z<10240
        && ((point.distance>=0 && point.distance<=360)
            || (point.distance_to_god_hand>=0 && point.distance_to_god_hand<=360));
}
// Keep nearby choices per kind, so dense trees cannot erase fields or buildings.
// One live traversal supplies every action; selection is deterministic and local.
struct NearbyTargets {
    bool around_hand=false;
    static constexpr size_t per_kind=8;
    ThrowPoint points[target_kind_count+1][per_kind]{};
    size_t counts[target_kind_count+1]{};
    double distance(const ThrowPoint& p) const {return around_hand?p.distance_to_god_hand:p.distance;}
    bool before(const ThrowPoint& a,const ThrowPoint& b) const {
        return distance(a)<distance(b) || (distance(a)==distance(b) && a.object<b.object);
    }
    void add(const ThrowPoint& p,double maximum=nearby_target_distance) {
        if(!p.object || !p.belief || !p.table || !p.kind || p.kind>target_kind_count
                || !std::isfinite(p.distance) || p.distance<=0
                || !std::isfinite(distance(p)) || distance(p)<0 || distance(p)>maximum)return;
        auto& n=counts[p.kind];auto* bucket=points[p.kind];
        for(size_t i=0;i<n;++i)if(bucket[i].object==p.object)return;
        size_t pos=0;while(pos<n && before(bucket[pos],p))++pos;
        if(pos>=per_kind)return;
        if(n<per_kind)++n;
        for(size_t i=n-1;i>pos;--i)bucket[i]=bucket[i-1];
        bucket[pos]=p;
    }
    size_t select(uint32_t kinds,ThrowPoint* out,size_t limit=object_target_capacity) const {
        size_t n=0;
        for(size_t rank=0;rank<per_kind && n<limit;++rank) {
            ThrowPoint round[target_kind_count]{};size_t count=0;
            for(unsigned kind=1;kind<=target_kind_count;++kind) {
                if(!(kinds&(1u<<kind)) || counts[kind]<=rank)continue;
                const auto& p=points[kind][rank];size_t pos=0;
                while(pos<count && before(round[pos],p))++pos;
                for(size_t i=count;i>pos;--i)round[i]=round[i-1];
                round[pos]=p;++count;
            }
            for(size_t i=0;i<count && n<limit;++i)out[n++]=round[i];
        }
        return n;
    }
};
// Preserve distant choices separately: nearby trees must not crowd them out.
// These are live objects from the creature's belief lists, not a global map scan.
struct KnownTargets {
    NearbyTargets nearby,distant;
    NearbyTargets hand{true};
    ThrowPoint home;
    uintptr_t focus_object=0;
    ThrowPoint focus;
    void add(const ThrowPoint& p) {
        if(p.object==focus_object && p.belief && p.table && p.kind && p.kind<=target_kind_count
                && std::isfinite(p.distance) && p.distance>0 && p.distance<=known_target_distance)focus=p;
        if(p.player_temple && p.kind==6 && p.object && p.belief && p.table
                && std::isfinite(p.distance) && p.distance>0 && p.distance<=known_target_distance)home=p;
        if(p.distance<=nearby_target_distance)nearby.add(p);
        else distant.add(p,known_target_distance);
        if(p.distance<=known_target_distance)hand.add(p,360.);
    }
    size_t select(uint32_t kinds,ThrowPoint* out) const {
        ThrowPoint hand_points[8]{};
        const auto hand_count=hand.select(kinds,hand_points,8);
        // Reserve hand-centered choices within the existing 32-object budget.
        const bool focused=focus.object && (kinds&(1u<<focus.kind));
        const auto limit=(hand_count?12u:16u)-(focused?1u:0u);
        const auto n=nearby.select(kinds,out,limit);
        auto total=n+distant.select(kinds,out+n,limit);
        for(size_t i=0;i<hand_count;++i) {
            bool included=false;
            for(size_t j=0;j<total;++j)if(out[j].object==hand_points[i].object)included=true;
            if(!included)out[total++]=hand_points[i];
        }
        if(focused) {
            bool included=false;
            for(size_t i=0;i<total;++i)if(out[i].object==focus.object)included=true;
            if(!included)out[total++]=focus;
        }
        // Reserve the verified home independently of the distance-ranked sample.
        if(home.object && (kinds&(1u<<6))) {
            bool included=false;
            for(size_t i=0;i<total;++i)if(out[i].object==home.object)included=true;
            if(!included)out[total++]=home;
        }
        return total;
    }
    size_t select_portable(uint32_t kinds,ThrowPoint* out) const {
        const bool focused=focus.object && (kinds&(1u<<focus.kind));
        size_t n=nearby.select(kinds,out,object_target_capacity-(focused?1:0));
        if(focused) {
            bool included=false;
            for(size_t i=0;i<n;++i)if(out[i].object==focus.object)included=true;
            if(!included)out[n++]=focus;
        }
        return n;
    }
};
struct ThrowEntry { ThrowPoint point; uint32_t token=0; };
struct ThrowTargets {
    static constexpr size_t capacity=object_target_capacity;
    ThrowEntry entries[capacity]{};
    size_t count=0;
    uint32_t serial=0,held=0;
    uint64_t epoch=0;
    uintptr_t creature=0;
    void observe(const ThrowPoint* points,size_t length,uint32_t projectile,uint64_t session,uintptr_t owner) {
        ThrowEntry next[capacity]{};size_t n=0;
        if (projectile && owner) for (size_t i=0;i<length && n<capacity;++i) {
            const auto& p=points[i];if (!p.object || !p.belief || !p.table || !p.kind) continue;
            bool duplicate=false;for (size_t j=0;j<n;++j) if (next[j].point.object==p.object) duplicate=true;
            if (duplicate) continue;
            uint32_t token=0;
            if (held==projectile && epoch==session && creature==owner)
                for (size_t j=0;j<count;++j) {
                    const auto& old=entries[j];
                    if (old.point.object==p.object && old.point.belief==p.belief && old.point.table==p.table
                            && old.point.kind==p.kind) token=old.token;
                }
            if (!token && serial!=UINT32_MAX) token=++serial;
            if (token) next[n++]={p,token};
        }
        for (size_t i=0;i<capacity;++i) entries[i]=next[i];
        count=n;held=projectile;epoch=session;creature=owner;
    }
    const ThrowEntry* find(uint32_t token) const {
        if (token) for (size_t i=0;i<count;++i) if (entries[i].token==token) return &entries[i];
        return nullptr;
    }
};
inline const char* throw_kind_name(unsigned kind) {
    const char* names[]={"unknown","tree","rock","villager","house","creature","temple","toy",
        "food_pile","wood_pile","animal","miracle_bubble","field","miracle_dispenser",
        "fishing_area","animal_group","storage","nursery","cemetery","workshop",
        "wonder","town_centre","totem","windmill","worship_site","fence","mushroom","bonfire","scaffolding"};
    return kind<=target_kind_count?names[kind]:"unknown";
}
}
