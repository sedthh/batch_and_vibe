#include "../../native/movement_guard.h"
#include "../../native/position_movement.h"
#include "check.h"
#include <limits>
#include <cstdio>
#include <initializer_list>
using namespace bwv_probe;
int main() {
    Movement map;map.verified=true;map.width=2560;map.depth=1280;
    assert(valid_destination({350,280},map));
    assert(!valid_destination({2560,280},map));
    assert(!valid_destination({0,280},map));
    assert(!valid_destination({350,1280},map));
    assert(!valid_destination({std::numeric_limits<float>::quiet_NaN(),280},map));
    map.verified=false;assert(!valid_destination({350,280},map));
    // Container movement resolves the chosen object's live position, not the
    // previous offer's coordinates. Failed reads cannot reuse an old point.
    map.verified=true;
    int32_t live_x=100*65536,live_z=60*65536;
    bool readable=true;
    const auto read_position=[&](uintptr_t address,int32_t& value) {
        if(!readable || (address!=0x1014 && address!=0x1018))return false;
        value=address==0x1014?live_x:live_z;return true;
    };
    PositionDestination resolved;
    assert(resolve_position_target(0x1000,map,read_position,resolved));
    assert(resolved.x==1000 && resolved.z==600);
    live_x=120*65536;
    assert(resolve_position_target(0x1000,map,read_position,resolved) && resolved.x==1200);
    readable=false;
    assert(!resolve_position_target(0x1000,map,read_position,resolved) && resolved.x==0);
    readable=true;live_z=-1;
    assert(!resolve_position_target(0x1000,map,read_position,resolved));
    Movement m;
    assert(movement_block(m));
    m.verified=true; m.distance=12;
    assert(!movement_block(m));
    for (double distance : {.5, 7.99, 30.01, 800., 12000.}) {
        m.distance=distance; assert(!movement_block(m));
    }
    for (double distance : {0., -1., std::numeric_limits<double>::infinity(),
                            std::numeric_limits<double>::quiet_NaN()}) {
        m.distance=distance; assert(movement_block(m));
    }
    assert(world_point(100,200,1000,1000));
    assert(!world_point(0,200,1000,1000));
    assert(!world_point(100,1000,1000,1000));
    assert(!world_point(-1,200,1000,1000));
    assert(!world_point(std::numeric_limits<double>::quiet_NaN(),200,1000,1000));
    MovementTarget target;
    m.distance=12; m.hand_x=100; m.hand_z=200;
    target.observe(m); const auto first=target.token();
    assert(first!=0);
    m.hand_x=101; target.observe(m); assert(target.token()==first);
    m.hand_x=102.1; target.observe(m); assert(target.token()==first);
    const auto moved=target.token();
    m.hand_x=100; target.observe(m); assert(target.token()==first && target.token()==moved);
    const auto returned=target.token();
    m.verified=false; target.observe(m); assert(target.token()==0);
    m.verified=true; target.observe(m); assert(target.token()!=returned);
    target.generation=UINT32_MAX; m.verified=false; target.observe(m);
    assert(target.token()==0); // Counter wrap cannot resurrect old targets.
    m.verified=true;
    assert(hand_approach_mask(m,true,false)==4); // Independent of sleeping animation.
    assert(!hand_approach_mask(m,false,false));
    assert(!hand_approach_mask(m,true,true));
    m.verified=false;assert(!hand_approach_mask(m,true,false));
    std::puts("Movement guard passed: unknown, distance, bounds and non-finite coordinates");
}
