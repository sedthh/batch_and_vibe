// Authored target lease for the opt-in object-approach experiment.
#pragma once
#include <cmath>
#include <cstdint>

namespace bwv_probe {
struct ObjectPoint {
    uintptr_t object=0, belief=0, table=0;
    unsigned kind=0; // 1 tree, 2 rock. No inferred ownership or reachability.
    double x=0, z=0, distance=0;
};
// Tree approach contributes nonnegative target padding, capped at 0.25 units.
// Use its zero lower bound, plus a small numerical margin, rather than guessing
// a mesh radius. Rock padding uses another contract and is deliberately unknown.
struct ObjectArrival {
    bool allowance_verified=false, radius_verified=false;
    double allowance=0, body_radius=0, confirmed_radius=0;
    void bind_allowance(double value) {
        allowance_verified=std::isfinite(value) && value>0 && value<=500;
        allowance=allowance_verified?value:0;
        radius_verified=false;confirmed_radius=0;
    }
    void sample_radius(double value, unsigned kind) {
        radius_verified=allowance_verified && kind==1 && std::isfinite(value) && value>0 && value<=100;
        body_radius=radius_verified?value:0;
        // Native threshold is 1.1 * (body radius + target padding + allowance).
        // Stay strictly inside that threshold; never enlarge it to fit a test.
        confirmed_radius=radius_verified?(value+allowance)*1.1*.99:0;
    }
    bool reached(double distance, bool stable_target, bool moved, bool plan_seen, bool plan_ended) const {
        return radius_verified && stable_target && moved && plan_seen && plan_ended
            && std::isfinite(distance) && distance>=0 && distance<confirmed_radius;
    }
};
inline bool object_point_valid(const ObjectPoint& p) {
    return p.object && p.belief && p.table && (p.kind==1 || p.kind==2)
        && std::isfinite(p.x) && std::isfinite(p.z) && p.x>0 && p.z>0
        && std::isfinite(p.distance) && p.distance>=8 && p.distance<=30;
}
inline bool tree_has_approach_space(double distance, double body_radius, double allowance) {
    return std::isfinite(distance) && std::isfinite(body_radius) && std::isfinite(allowance)
        && body_radius>0 && body_radius<=100 && allowance>0 && allowance<=500
        && distance>(body_radius+allowance+.25)*1.1+1.;
}
struct ObjectTarget {
    ObjectPoint point;
    uint64_t epoch=0;
    uintptr_t creature=0, mental=0;
    uint32_t generation=0;
    bool valid=false, exhausted=false;
    void observe(const ObjectPoint& next, uint64_t session, uintptr_t owner, uintptr_t mind) {
        const bool usable=object_point_valid(next) && owner && mind;
        if (usable!=valid || session!=epoch || owner!=creature || mind!=mental
                || (usable && (next.object!=point.object || next.belief!=point.belief
                    || next.table!=point.table || next.kind!=point.kind
                    || next.x!=point.x || next.z!=point.z))) {
            if (generation==UINT32_MAX) exhausted=true;
            else ++generation;
        }
        point=next; epoch=session; creature=owner; mental=mind; valid=usable;
    }
    uint32_t token() const { return valid && !exhausted ? generation : 0; }
};
}
